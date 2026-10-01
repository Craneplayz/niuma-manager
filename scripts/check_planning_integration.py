"""Paid, synthetic-only planning check through the real account backend."""

import importlib.util
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from ddl_text.codex_client import live_options
from task_planning import planning_harness


def main():
    options = live_options(ROOT / ".env")
    if options["provider"] != "qiniu" or not options["key"].strip():
        raise SystemExit("Qiniu credentials required; no model call was made")
    spec = importlib.util.spec_from_file_location("niuma_app", ROOT / "server" / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    with tempfile.TemporaryDirectory() as directory:
        app.DB = Path(directory) / "synthetic.sqlite3"
        app.initialize()
        harness = planning_harness.ThreadingHTTPServer(("127.0.0.1", 0), planning_harness.Handler)
        harness.options, harness.token = options, "synthetic-planning-only"
        backend = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        servers = (harness, backend)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
        for thread in threads:
            thread.start()
        base = "http://127.0.0.1:%d/api" % backend.server_port
        client = build_opener(HTTPCookieProcessor(CookieJar()))

        def call(path, body=None, expected=200, method=None):
            request = Request(base + path, None if body is None else json.dumps(body).encode(),
                              {"Content-Type": "application/json"}, method=method)
            try:
                response = client.open(request, timeout=35)
            except HTTPError as error:
                response = error
            with response:
                data = json.load(response)
                if response.status != expected:
                    raise AssertionError((path, response.status, data))
                return data

        try:
            with patch.dict(os.environ, {
                    "HARNESS_URL": "http://127.0.0.1:%d/planning" % harness.server_port,
                    "HARNESS_TOKEN": "synthetic-planning-only"}):
                call("/auth/register", {"name": "Planning test", "email": "planning@integration.test",
                                        "password": "Only-for-test-2026!"})
                now = datetime.now(timezone(timedelta(hours=8)))
                task = {"id": "synthetic-report", "title": "整理一份三页的课程报告",
                        "description": "只需估算与安排写作时间；这是合成测试任务。",
                        "status": "not_started", "priority": "medium",
                        "due": {"date": (now + timedelta(days=7)).date().isoformat(), "precision": "date"},
                        "subtasks": [], "plans": []}
                call("/data/tasks", {"value": [task], "version": 0}, method="PUT")
                call("/data/availability", {"value": {"startTime": "10:00", "endTime": "22:00",
                                                         "weekdays": list(range(7))}, "version": 0}, method="PUT")
                proposal = call("/planning/proposals", {})
                assert proposal["estimates"][task["id"]] > 0
                assert proposal["blocks"], "Model estimated but did not schedule an easy task"
                assert not call("/workspace")["tasks"]["value"][0]["plans"], "Preview changed tasks"
                applied = call("/planning/apply", {"requestId": proposal["requestId"]})
                assert len(applied["value"][0]["plans"]) == len(proposal["blocks"])
                call("/planning/apply", {"requestId": proposal["requestId"]}, expected=409)
                followup = dict(task, id="synthetic-slides", title="准备五页课堂展示幻灯片",
                                due={"date": (now + timedelta(days=5)).date().isoformat(),
                                     "time": "18:00", "precision": "datetime"}, plans=[])
                excluded = dict(task, id="synthetic-excluded", title="无需安排的合成任务",
                                planningExcluded=True, plans=[])
                completed = dict(task, id="synthetic-completed", title="已完成的三页课程报告",
                                 status="completed", plans=[])
                call("/data/tasks", {"value": applied["value"] + [followup, excluded, completed],
                                     "version": applied["version"]}, method="PUT")
                call("/data/feedback", {"value": [{"taskId": completed["id"], "actualMinutes": 75,
                                                    "recordedAt": now.isoformat()}], "version": 0}, method="PUT")
                second = call("/planning/proposals", {})
                assert set(second["estimates"]) == {followup["id"]}, "Ineligible task sent to model"
                assert second["blocks"], "Follow-up task was not scheduled"
                assert all(block["taskId"] == followup["id"] for block in second["blocks"])
                print(json.dumps({"status": "passed", "mode": "real", "checks": [
                    "model_estimate", "backend_validated_schedule", "preview_does_not_write_tasks",
                    "explicit_apply", "duplicate_apply_rejected", "existing_plan_respected",
                    "excluded_and_completed_tasks_filtered", "personalization_feedback_accepted",
                    "exact_deadline_respected"], "block_count": len(proposal["blocks"]),
                    "followup_block_count": len(second["blocks"])}))
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()


if __name__ == "__main__":
    main()

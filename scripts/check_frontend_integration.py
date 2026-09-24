"""Exercise the actual frontend backend over HTTP with temporary accounts/DB.

Default: synthetic drafts, no model call. --live: two synthetic records sent
to the configured paid API. Never accepts arbitrary private test input.
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.error import HTTPError
from unittest.mock import patch
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ddl_text.codex_client import CodexClient, live_options, preflight
from ddl_text.config import Settings
from ddl_text.extraction import ExtractionAdapter, OfflineDraftClient
from ddl_text.http_server import RecognitionServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", default=str(ROOT), help="Checkout with server/app.py from PR #15")
    parser.add_argument("--live", action="store_true", help="Explicit paid synthetic-only API check")
    parser.add_argument("--output", help="New JSON report file (synthetic inputs/results only)")
    args = parser.parse_args()
    output = Path(args.output) if args.output else None
    if output and output.exists():
        parser.error("Choose a new output path")
    frontend = Path(args.frontend_dir).resolve()
    if not (frontend / "server/app.py").is_file():
        parser.error("Frontend server/app.py is not available at that path")
    # Import the real consumer/validator; do not replace it with our snapshot.
    sys.path.insert(0, str(frontend / "server"))
    spec = importlib.util.spec_from_file_location("frontend_app", frontend / "server/app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    entries = json.loads((ROOT / "docs/examples/text-frontend-v02-drafts.json").read_text(encoding="utf-8"))
    request = json.loads((ROOT / "docs/examples/text-frontend-v02-request.json").read_text(encoding="utf-8"))
    if args.live:
        options = live_options(ROOT / ".env")
        if not options["key"].strip():
            parser.error("Missing provider credential")
        adapter = ExtractionAdapter(CodexClient(preflight(options), options["key"]))
        settings = Settings(mode="real", model=options["model"], endpoint=options["endpoint"], timeout_seconds=20)
    else:
        adapter, settings = ExtractionAdapter(OfflineDraftClient(entries)), Settings()
    harness = RecognitionServer(("127.0.0.1", 0), adapter, settings, token="synthetic-integration-only")
    checks = []
    with tempfile.TemporaryDirectory() as folder:
        app.DB = Path(folder) / "integration.sqlite3"
        app.initialize()
        backend = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        base = "http://127.0.0.1:%d/api" % backend.server_port
        servers = [harness, backend]
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
        for thread in threads:
            thread.start()
        def client():
            return build_opener(HTTPCookieProcessor(CookieJar()))
        def call(c, path, data=None, expected=200):
            try:
                response = c.open(Request(base + path, None if data is None else json.dumps(data).encode(),
                                          {"Content-Type": "application/json"}), timeout=30)
            except HTTPError as error:
                response = error
            with response:
                body = json.load(response)
                assert response.status == expected, (path, response.status, body)
                return body
        try:
            with patch.dict(os.environ, {
                    "TEXT_HARNESS_URL": "http://127.0.0.1:%d/recognize" % harness.server_port,
                    "TEXT_HARNESS_TOKEN": "synthetic-integration-only"}):
                a, b = client(), client()
                call(a, "/auth/register", dict(name="Synthetic A", email="a@integration.test", password="Only-for-test-2026!"))
                call(b, "/auth/register", dict(name="Synthetic B", email="b@integration.test", password="Only-for-test-2026!"))
                batches = call(a, "/recognition/batches", request)
                assert len(batches) == 1 and not batches[0]["demo"]
                batch = batches[0]
                result = batch["result"]
                assert result["status"] == "success", result["errors"]
                assert len(result["tasks"]) == 3, "Expected group, plan, and report tasks"
                by_state = {c["deadline"]["status"]: c for c in result["tasks"]}
                assert set(by_state) == {"inferred", "not_provided", "explicit"}
                assert by_state["inferred"]["deadline"]["kind"] == "reference"
                assert by_state["explicit"]["deadline"]["value"] == "2026-09-30T18:00:00+08:00"
                checks.append("recognition_through_real_frontend_validator")
                assert "tasks" not in call(a, "/workspace")
                checks.append("no_tasks_written_before_confirmation")
                assert call(a, "/recognition/batches", request) == batches
                changed = dict(request, records=[])
                call(a, "/recognition/batches", changed, expected=409)
                checks.append("request_replay_and_conflict")
                assert call(b, "/recognition/batches") == []
                checks.append("account_isolation")
                item = next(x for x in batch["items"] if x["candidate"]["deadline"]["status"] == "explicit")
                resolution = dict(action="create", version=1, tasks_version=0, reviewed=True,
                                  draft=dict(title=item["candidate"]["title"], description=item["candidate"]["description"],
                                             due=dict(date="2026-09-30", time="18:00", precision="datetime")))
                path = "/recognition/candidates/%s/resolve" % item["id"]
                call(b, path, resolution, expected=404)
                call(a, path, resolution)
                call(a, path, resolution)
                tasks = call(a, "/workspace")["tasks"]["value"]
                assert len(tasks) == 1 and tasks[0]["due"] == resolution["draft"]["due"]
                checks.append("confirmation_is_idempotent_and_preserves_deadline")
                report = dict(mode=settings.mode, checks=checks, request=request, result=result,
                              frontend_validator_sha256=hashlib.sha256((frontend / "server/text_contract.py").read_bytes()).hexdigest(),
                              private_data_used=False, independent_recall_evaluation=False)
                if output:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(dict(mode=settings.mode, checks=checks, status="passed")))
        finally:
            for server in servers:
                server.shutdown(); server.server_close()
            for thread in threads:
                thread.join()


if __name__ == "__main__":
    main()

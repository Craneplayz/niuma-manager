"""Offline tests; optional real CLI talks only to a loopback synthetic server."""

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from ddl_text.adapters import AgentError
from ddl_text.codex_client import ROOT, CodexClient, live_options, load_env, stop_process
from ddl_text.config import Settings
from ddl_text.extraction import ExtractionAdapter
from ddl_text.pipeline import process_request


class CodexClientTests(unittest.TestCase):
    def test_real_child_is_terminated_on_cleanup(self):
        import sys
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                                      else {"start_new_session": True}))
        try:
            stop_process(process)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_dotenv_is_literal_and_never_changes_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nDEEPSEEK_API_KEY="$(not-executed)"\nDDL_MODEL=local\n', encoding="utf-8")
            before = dict(os.environ)
            self.assertEqual(load_env(path)["DEEPSEEK_API_KEY"], "$(not-executed)")
            self.assertEqual(dict(os.environ), before)
            path.write_text('DEEPSEEK_API_KEY=test-only\nTEXT_HARNESS_URL=http://127.0.0.1:1333/recognize\nHARNESS_TOKEN=other-service\n', encoding="utf-8")
            self.assertEqual(load_env(path), {"DEEPSEEK_API_KEY": "test-only"})
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-secret"}):
                self.assertEqual(live_options(path)["key"], "env-secret")
            for text in ("BAD=x", "DEEPSEEK_API_KEY=a\nDEEPSEEK_API_KEY=b", 'DEEPSEEK_API_KEY="broken'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_env(path)

    def test_endpoint_credentials_or_http_are_rejected(self):
        for endpoint in ("http://example.com", "https://user:secret@example.com", "https://example.com?key=secret"):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"DDL_ENDPOINT": endpoint}):
                with self.assertRaises(ValueError):
                    live_options(Path("nonexistent.env"))

    def test_missing_key_does_not_start_process(self):
        with patch("ddl_text.codex_client.subprocess.Popen") as popen, self.assertRaises(AgentError):
            CodexClient("codex", "").generate("rules", "{}", Settings(), 0)
        popen.assert_not_called()

    def test_child_gets_private_stdin_and_separate_rules_not_argv(self):
        seen = {}
        def start(command, **kwargs):
            seen.update(command=command, **kwargs)
            home = Path(kwargs["env"]["CODEX_HOME"])
            self.assertNotEqual(str(home), os.environ.get("CODEX_HOME"))
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertNotIn("secret-key", config)
            self.assertNotIn("private-text", config)
            process = Mock(returncode=0)
            process.poll.return_value = 0
            def communicate(data, timeout):
                self.assertEqual(json.loads(data), {"text": "private-text"})
                Path(command[command.index("--output-last-message") + 1]).write_text('{"tasks":[]}', encoding="utf-8")
            process.communicate.side_effect = communicate
            return process
        with patch("ddl_text.codex_client.subprocess.Popen", side_effect=start):
            result = CodexClient("codex", "secret-key").generate("rules", '{"text":"private-text"}', Settings(), 0)
        self.assertEqual(result, '{"tasks":[]}')
        self.assertNotIn("secret-key", str(seen["command"]))
        self.assertNotIn("private-text", str(seen["command"]))
        self.assertFalse(Path(seen["env"]["CODEX_HOME"]).exists())

    def test_failure_and_timeout_are_sanitized_and_reaped(self):
        for failure in (None, subprocess.TimeoutExpired("PRIVATE", 1)):
            process = Mock(returncode=1)
            process.poll.return_value = 1
            process.communicate.side_effect = failure
            with patch("ddl_text.codex_client.subprocess.Popen", return_value=process), \
                    patch("ddl_text.codex_client.stop_process") as stop, self.assertRaises(AgentError) as error:
                CodexClient("codex", "secret").generate("rules", '{"text":"private"}', Settings(), 0)
            self.assertEqual(error.exception.code, "agent_timeout" if failure else "agent_unavailable")
            self.assertNotIn("private", str(error.exception))
            stop.assert_called_once_with(process)


@unittest.skipUnless(os.environ.get("DDL_TEST_CODEX"), "opt-in local CLI integration")
class LocalCodexIntegrationTests(unittest.TestCase):
    def test_real_cli_against_loopback_responses_server(self):
        requests = []
        fixture = json.loads((ROOT / "docs/examples/text-stage-c-drafts.json").read_text(encoding="utf-8"))[0]
        draft_json = json.dumps(fixture["response"], ensure_ascii=False)
        response = {"id": "resp_synthetic", "object": "response", "created_at": 1,
                    "status": "completed", "model": "deepseek-flash", "output": [
                        {"id": "msg_synthetic", "type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": draft_json, "annotations": []}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                requests.append(json.loads(body))
                if getattr(self.server, "reject_auth", False):
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in (
                    {"type": "response.created", "response": dict(response, status="in_progress", output=[])},
                    {"type": "response.output_item.added", "output_index": 0, "item": dict(response["output"][0], content=[])},
                    {"type": "response.output_text.delta", "item_id": "msg_synthetic", "output_index": 0, "content_index": 0, "delta": draft_json},
                    {"type": "response.output_item.done", "output_index": 0, "item": response["output"][0]},
                    {"type": "response.completed", "response": response},
                ):
                    self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = Settings(mode="real", model="deepseek-flash",
                                endpoint="http://127.0.0.1:%s" % server.server_port, timeout_seconds=30)
            record = fixture["record"]
            record["text"] += " SYNTHETIC-NOTICE"
            result = process_request({"contract_version": "0.1", "request_id": "local-test", "records": [record]},
                                     ExtractionAdapter(CodexClient(os.environ["DDL_TEST_CODEX"], "synthetic-not-a-key")), settings)
            self.assertEqual(result["status"], "success", result["errors"])
            self.assertEqual(result["tasks"][0]["deadline"]["value"], "2026-09-16T18:40:00+08:00")
            self.assertIsNone(result["tasks"][1]["deadline"]["value"])
            self.assertEqual(len(requests), 1)
            request = requests[0]
            self.assertIn("text-task-extraction", request["instructions"])
            self.assertNotIn("SYNTHETIC-NOTICE", request["instructions"])
            self.assertIn("SYNTHETIC-NOTICE", json.dumps(request["input"]))
            tool_names = [tool.get("name", tool.get("type")) for tool in request.get("tools", [])]
            self.assertEqual(tool_names, [], "Unexpected tools: %s" % tool_names)
            self.assertEqual(request["model"], "deepseek-flash")
            self.assertEqual(request["reasoning"]["effort"], "low")
            self.assertEqual(request["text"]["format"]["type"], "json_schema")
            self.assertEqual(request["text"]["format"]["schema"]["properties"]["tasks"]["items"]["properties"]["course"], {"type": "null"})
            server.reject_auth = True
            failed = process_request({"contract_version": "0.1", "request_id": "local-failure", "records": [record]},
                                     ExtractionAdapter(CodexClient(os.environ["DDL_TEST_CODEX"], "synthetic-not-a-key")), settings)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["errors"][0]["code"], "agent_unavailable")
            self.assertEqual(len(requests), 2, "Provider must not retry authentication failures")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

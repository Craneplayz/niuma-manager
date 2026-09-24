"""Loopback-only synchronous JSON bridge for the frontend's TEXT_HARNESS_URL."""

import argparse
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .codex_client import ROOT, CodexClient, live_options, preflight
from .config import Settings
from .extraction import ExtractionAdapter, OfflineDraftClient
from .pipeline import process_request


MAX_BYTES = 2_000_000


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-finite number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


class RecognitionServer(ThreadingHTTPServer):
    # A server exit waits for the bounded active extraction to clean up Codex.
    daemon_threads = False

    def __init__(self, address, adapter, settings, token="", budget_seconds=20):
        if address[0] != "127.0.0.1":
            raise ValueError("Only loopback binding is supported")
        if not 0 < budget_seconds <= 20:
            raise ValueError("Budget must be within (0, 20] seconds")
        self.adapter, self.settings = adapter, settings
        self.token, self.budget_seconds = token, budget_seconds
        self.active = threading.Lock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never log body, source URLs, credentials, or user-supplied paths.

    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) >= MAX_BYTES:
            status, raw = 502, b'{"error":"Recognition response is too large"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok", "contract_version": "0.2", "mode": self.server.settings.mode})
        else:
            self.reply(404, {"error": "Endpoint not found"})

    def do_POST(self):
        if self.path != "/recognize":
            self.reply(404, {"error": "Endpoint not found"})
            return
        token = self.server.token
        if token and not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
            self.reply(401, {"error": "Unauthorized"})
            return
        if self.headers.get_content_type() != "application/json":
            self.reply(415, {"error": "Use application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if self.headers.get("Transfer-Encoding") or length < 0 or length > MAX_BYTES:
            self.reply(413, {"error": "Invalid or oversized request body"})
            return
        if not self.server.active.acquire(blocking=False):
            self.reply(503, {"error": "Recognition is busy; retry later"})
            return
        try:
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("incomplete body")
                request = strict_json(raw.decode("utf-8"))
            except (ValueError, UnicodeError, OSError, RecursionError):
                self.reply(400, {"error": "Invalid JSON request"})
                return
            if not isinstance(request, dict) or request.get("contract_version") != "0.2":
                self.reply(400, {"error": "This endpoint requires contract_version 0.2"})
                return
            result = process_request(request, self.server.adapter, self.server.settings,
                                     budget_seconds=self.server.budget_seconds)
            # A failed record is still a valid result, never a fabricated no_tasks.
            self.reply(200, result)
        except Exception:
            self.reply(500, {"error": "Recognition service failed"})
        finally:
            self.server.active.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=1333)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--real", action="store_true", help="Use configured DeepSeek API via Codex")
    source.add_argument("--drafts", help="Synthetic exact-input drafts for offline integration")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args(argv)
    try:
        if args.real:
            options = live_options(Path(args.env_file))
            if not options["key"].strip():
                raise ValueError("missing credential")
            adapter = ExtractionAdapter(CodexClient(preflight(options), options["key"]))
            settings = Settings(mode="real", model=options["model"], endpoint=options["endpoint"],
                                timeout_seconds=20, max_retries=0)
        else:
            entries = json.loads(Path(args.drafts).read_text(encoding="utf-8-sig"))
            if not isinstance(entries, list):
                raise ValueError("invalid drafts")
            adapter, settings = ExtractionAdapter(OfflineDraftClient(entries)), Settings()
        server = RecognitionServer(("127.0.0.1", args.port), adapter, settings,
                                   os.environ.get("TEXT_HARNESS_TOKEN", ""))
    except (OSError, ValueError, TypeError, KeyError):
        parser.error("Cannot start recognition service; check configuration, CLI, and port.")
    print("Text recognition (%s): http://127.0.0.1:%d/recognize" % (settings.mode, server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

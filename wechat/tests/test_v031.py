"""Offline acceptance tests for the Windows-oriented v0.3.1 hand-off."""

import json
import tempfile
import unittest
from pathlib import Path

try:
    from contracts.check_v031_examples import check_request
except ModuleNotFoundError:  # The minimal exporter environment has no dev deps.
    check_request = None
from wechat.script.v031 import (
    ClientSettings,
    PendingQueue,
    V031RemoteError,
    build_requests,
    submit_pending,
    validate_result,
    validate_job,
)


class FakeUploader:
    def __init__(self):
        self.calls = []

    def upload(self, path, metadata):
        self.calls.append((Path(path), dict(metadata)))
        return dict(metadata, file_ref="asset:" + "a" * 24)


def make_chat(messages):
    return {
        "chat_name": "合成课程群",
        "chat_username": "10000000000@chatroom",
        "exported_at": "2026-10-01T20:05:00+08:00",
        "messages": messages,
    }


def check_contract(request):
    if check_request is not None:
        check_request(request)
    else:
        assert request["contract_version"] == "0.3.1"
        assert 1 <= len(request["records"]) <= 20
        for record in request["records"]:
            assert set(record) == {
                "input_id", "text", "text_quality", "platform", "published_at",
                "collected_at", "timezone", "timezone_basis", "source_url",
                "source_label", "source_item_id", "course_or_project", "context",
                "upstream_hints", "attachments",
            }


def message(server_id, content="通知文字", **extra):
    result = {
        "time": "2026-10-01T20:01:00+08:00",
        "type_code": 1,
        "content": content,
        "message_id": {"server_id": server_id},
    }
    result.update(extra)
    return result


class V031RequestTest(unittest.TestCase):
    def test_text_record_uses_v031_and_contract_validator(self):
        [request] = build_requests([make_chat([message(1)])], request_id="offline")
        check_contract(request)
        self.assertEqual(request["contract_version"], "0.3.1")
        self.assertEqual(request["records"][0]["attachments"], [])

    def test_upload_receipt_is_used_and_same_bytes_are_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"same bytes")
            asset = {"kind": "file", "path": str(path), "original_name": "report.pdf"}
            uploader = FakeUploader()
            requests = build_requests(
                [
                    make_chat([message(1, "[文件] report.pdf", type_code=49, assets=[asset]),
                               message(2, "[文件] report.pdf", type_code=49, assets=[asset])])
                ],
                request_id="same-file",
                uploader=uploader,
            )
        self.assertEqual(len(uploader.calls), 1)
        self.assertEqual(len(requests[0]["records"]), 2)
        for record in requests[0]["records"]:
            check_contract({**requests[0], "records": [record]})
            attachment = record["attachments"][0]
            self.assertEqual(attachment["status"], "available")
            self.assertEqual(attachment["file_ref"], "asset:" + "a" * 24)
            self.assertNotIn("path", json.dumps(record, ensure_ascii=False))

    def test_windows_supported_types_and_same_name_different_bytes_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [
                ("photo.jpg", "image/jpeg"),
                ("photo.png", "image/png"),
                ("report.pdf", "application/pdf"),
                ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ]
            messages = []
            for index, (name, mime) in enumerate(files, start=1):
                path = root / name
                path.write_bytes(f"bytes-{index}".encode())
                messages.append(message(index, "[文件] " + name, type_code=49, assets=[{"kind": "file", "path": str(path)}]))
            same_a = root / "a" / "same.pdf"
            same_b = root / "b" / "same.pdf"
            same_a.parent.mkdir()
            same_b.parent.mkdir()
            same_a.write_bytes(b"first")
            same_b.write_bytes(b"second")
            messages.extend([
                message(5, "[文件] same.pdf", type_code=49, assets=[{"kind": "file", "path": str(same_a)}]),
                message(6, "[文件] same.pdf", type_code=49, assets=[{"kind": "file", "path": str(same_b)}]),
            ])
            uploader = FakeUploader()
            [request] = build_requests([make_chat(messages)], request_id="types", uploader=uploader)
        self.assertEqual(len(uploader.calls), 6)
        self.assertEqual([record["attachments"][0]["status"] for record in request["records"]], ["available"] * 6)
        hashes = [record["attachments"][0]["sha256"] for record in request["records"][-2:]]
        self.assertNotEqual(hashes[0], hashes[1])

    def test_missing_and_unsupported_files_remain_visible(self):
        chat = make_chat(
            [
                message(1, "[文件] missing.pdf", type_code=49, assets=[{"kind": "file", "file_name": "missing.pdf"}]),
                message(2, "[语音]", type_code=34),
            ]
        )
        [request] = build_requests([chat], request_id="failures")
        check_contract(request)
        missing, unsupported = request["records"]
        self.assertEqual(missing["attachments"][0]["status"], "failed")
        self.assertEqual(missing["attachments"][0]["errors"][0]["code"], "asset_missing")
        self.assertEqual(unsupported["attachments"][0]["status"], "unsupported")
        self.assertEqual(unsupported["attachments"][0]["errors"][0]["code"], "unsupported_type")

    def test_unauthed_upload_is_a_structured_failure_not_a_fake_file_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"report")
            [request] = build_requests(
                [make_chat([message(1, "[文件] report.pdf", type_code=49, assets=[{"path": str(path)}])])],
                request_id="offline-upload",
            )
        attachment = request["records"][0]["attachments"][0]
        self.assertEqual(attachment["status"], "failed")
        self.assertIsNone(attachment["file_ref"])
        self.assertEqual(attachment["errors"][0]["code"], "upload_failed")

    def test_only_explicit_reply_context_is_included(self):
        first = message(1, "前一条文字")
        second = message(2, "附件说明", context=[first])
        [request] = build_requests([make_chat([first, second])], request_id="context")
        self.assertEqual(request["records"][0]["context"], [])
        self.assertEqual(request["records"][1]["context"][0]["text"], "前一条文字")

    def test_links_reject_private_and_signed_urls_without_dropping_evidence(self):
        item = message(
            1,
            "https://example.com/course?id=42 http://127.0.0.1/private?token=x",
        )
        [request] = build_requests([make_chat([item])], request_id="links")
        record = request["records"][0]
        self.assertEqual(record["source_url"], "https://example.com/course?id=42")
        self.assertIn("unusable_links", record["upstream_hints"])


class V031QueueTest(unittest.TestCase):
    def test_pending_batches_are_idempotent_and_errors_are_serializable(self):
        class Client:
            def submit_import(self, request):
                return {
                    "contract_version": "0.3.1",
                    "job_id": "job-1",
                    "request_id": request["request_id"],
                    "status": "queued",
                }

        with tempfile.TemporaryDirectory() as directory:
            queue = PendingQueue(Path(directory) / "pending.json")
            request = build_requests([make_chat([message(1)])], request_id="queued")[0]
            queue.add(request)
            queue.add(request)
            responses = submit_pending(Client(), queue)
            payload = json.loads(queue.path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["batches"]), 1)
        self.assertEqual(payload["batches"][0]["job_id"], "job-1")
        self.assertEqual(responses[0]["status"], "queued")

    def test_job_progress_and_remote_error_feedback(self):
        job = {
            "contract_version": "0.3.1",
            "job_id": "job-1",
            "request_id": "queued:1",
            "status": "queued",
            "progress": {"total_records": 1, "finished_records": 0, "failed_records": 0},
            "result": None,
        }
        self.assertEqual(validate_job(job)["status"], "queued")
        error = V031RemoteError("queue_busy", "暂时不可用", retryable=True, status=503)
        self.assertEqual(error.envelope()["error"]["code"], "queue_busy")
        self.assertTrue(error.envelope()["error"]["retryable"])

        result = {
            "contract_version": "0.3.1",
            "request_id": "queued:1",
            "status": "no_tasks",
            "record_results": [],
            "tasks": [],
            "errors": [],
        }
        self.assertEqual(validate_result(result)["status"], "no_tasks")

    def test_client_settings_can_clear_limited_credential(self):
        settings = ClientSettings("https://example.com", "secret")
        settings.clear_credential()
        self.assertIsNone(settings.credential)


if __name__ == "__main__":
    unittest.main()

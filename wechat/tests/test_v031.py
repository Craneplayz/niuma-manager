"""Offline acceptance tests for the Windows-oriented v0.3.1 hand-off."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from contracts.check_v031_examples import check_request
except ModuleNotFoundError:  # The minimal exporter environment has no dev deps.
    check_request = None
from wechat.script.v031 import (
    ClientSettings,
    MAX_QUEUE_ATTEMPTS,
    PendingQueue,
    V031RemoteError,
    V031BuildError,
    _multipart_body,
    build_requests,
    submit_pending,
    validate_ack,
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

    def test_same_bytes_with_different_names_reuse_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.pdf"
            second = root / "b.pdf"
            first.write_bytes(b"same")
            second.write_bytes(b"same")
            messages = [
                message(1, "[文件] a.pdf", type_code=49, assets=[{"kind": "file", "path": str(first)}]),
                message(2, "[文件] b.pdf", type_code=49, assets=[{"kind": "file", "path": str(second)}]),
            ]
            uploader = FakeUploader()
            [request] = build_requests([make_chat(messages)], request_id="same-content", uploader=uploader)
        self.assertEqual(len(uploader.calls), 1)
        self.assertEqual([item["status"] for item in (record["attachments"][0] for record in request["records"])], ["available", "available"])
        self.assertEqual(request["records"][0]["attachments"][0]["file_ref"], request["records"][1]["attachments"][0]["file_ref"])

    def test_server_dedupe_receipt_can_use_canonical_name_across_builds(self):
        class CanonicalNameUploader:
            def __init__(self):
                self.calls = 0

            def upload(self, path, metadata):
                self.calls += 1
                return {
                    "contract_version": "0.3.1",
                    "file_ref": "asset:" + "c" * 24,
                    "file_name": "canonical.pdf",
                    "mime_type": metadata["mime_type"],
                    "size_bytes": metadata["size_bytes"],
                    "sha256": metadata["sha256"],
                }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current-name.pdf"
            path.write_bytes(b"same-server-content")
            uploader = CanonicalNameUploader()
            [first] = build_requests(
                [make_chat([message(1, "[文件] first-name.pdf", type_code=49, assets=[{"path": str(path), "file_name": "first-name.pdf"}])])],
                request_id="server-dedupe-1",
                uploader=uploader,
            )
            [second] = build_requests(
                [make_chat([message(2, "[文件] second-name.pdf", type_code=49, assets=[{"path": str(path), "file_name": "second-name.pdf"}])])],
                request_id="server-dedupe-2",
                uploader=uploader,
            )
        self.assertEqual(uploader.calls, 2)
        self.assertEqual(first["records"][0]["attachments"][0]["status"], "available")
        self.assertEqual(second["records"][0]["attachments"][0]["status"], "available")
        self.assertEqual(second["records"][0]["attachments"][0]["file_ref"], "asset:" + "c" * 24)

    def test_readable_image_supersedes_matching_encrypted_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "photo.jpg"
            encrypted = root / "photo.cache"
            image.write_bytes(b"jpeg")
            encrypted.write_bytes(b"encrypted")
            asset_list = [
                {"kind": "image", "path": str(image), "md5": "a" * 32, "mime_type": "image/jpeg"},
                {"kind": "wechat_encrypted_image_cache", "path": str(encrypted), "md5": "a" * 32},
            ]
            uploader = FakeUploader()
            [request] = build_requests(
                [make_chat([message(1, "[图片]", type_code=3, assets=asset_list)])],
                request_id="image-coalesce",
                uploader=uploader,
            )
        self.assertEqual(len(request["records"][0]["attachments"]), 1)
        self.assertEqual(request["records"][0]["attachments"][0]["status"], "available")

    def test_zero_server_id_falls_back_to_valid_message_id(self):
        first = message(0, "first", message_id={"server_id": 0, "sort_seq": 0, "local_id": 1})
        second = message(0, "second", message_id={"server_id": 0, "sort_seq": 0, "local_id": 2})
        [request] = build_requests([make_chat([first, second])], request_id="zero-id")
        self.assertEqual([record["source_item_id"] for record in request["records"]], ["1", "2"])

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

    def test_multiple_public_links_are_marked_incomplete(self):
        item = message(1, "https://example.com/one https://example.org/two")
        [request] = build_requests([make_chat([item])], request_id="multiple-links")
        record = request["records"][0]
        self.assertEqual(record["source_url"], "https://example.com/one")
        self.assertEqual(record["upstream_hints"]["unrepresented_links"], ["https://example.org/two"])
        self.assertTrue(record["upstream_hints"]["incomplete_content"])

    def test_type_49_link_card_does_not_become_a_fake_file(self):
        item = message(
            1,
            "[链接/卡片] 课程公告",
            type_code=49,
            details={"app_message_type": "5"},
            assets=[{"kind": "url", "url": "https://example.com/course"}],
        )
        [request] = build_requests([make_chat([item])], request_id="link-card")
        record = request["records"][0]
        self.assertEqual(record["attachments"], [])
        self.assertEqual(record["text"], "[链接/卡片] 课程公告")

    def test_failed_file_keeps_visible_filename_and_encrypted_cache_reports_decrypt(self):
        with tempfile.TemporaryDirectory() as directory:
            encrypted = Path(directory) / "image.cache"
            encrypted.write_bytes(b"encrypted")
            chat = make_chat(
                [
                    message(1, "[文件] missing.pdf", type_code=49, assets=[{"kind": "file", "file_name": "missing.pdf"}]),
                    message(2, "[图片]", type_code=3, assets=[{"kind": "wechat_encrypted_image_cache", "path": str(encrypted)}]),
                ]
            )
            [request] = build_requests([chat], request_id="read-errors")
        missing, encrypted_record = request["records"]
        self.assertEqual(missing["text"], "[文件] missing.pdf")
        self.assertEqual(missing["attachments"][0]["errors"][0]["code"], "asset_missing")
        self.assertEqual(encrypted_record["attachments"][0]["errors"][0]["code"], "decrypt_failed")

    def test_remote_upload_retryability_is_preserved(self):
        class UnavailableUploader:
            def upload(self, path, metadata):
                raise V031RemoteError("forbidden", "没有上传权限", retryable=False, status=403)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"report")
            [request] = build_requests(
                [make_chat([message(1, "[文件] report.pdf", type_code=49, assets=[{"path": str(path)}])])],
                request_id="remote-error",
                uploader=UnavailableUploader(),
            )
        error = request["records"][0]["attachments"][0]["errors"][0]
        self.assertEqual(error["code"], "upload_failed")
        self.assertFalse(error["retryable"])
        self.assertIn("forbidden", error["message"])

    def test_hint_and_url_boundaries_are_rejected_before_enqueue(self):
        links = " ".join(f"http://127.0.0.1/private?token={index}" for index in range(33))
        with self.assertRaises(V031BuildError):
            build_requests([make_chat([message(1, links)])], request_id="too-many-hints")
        [request] = build_requests(
            [make_chat([message(1, "https://example.com:80/course")])],
            request_id="non-default-port",
        )
        self.assertIsNone(request["records"][0]["source_url"])
        self.assertIn("unusable_links", request["records"][0]["upstream_hints"])

    def test_duplicate_source_and_input_entries_are_rejected(self):
        with self.assertRaises(V031BuildError):
            build_requests([make_chat([message(1), message(1)])], request_id="duplicate-source")
        first = message(1)
        second = message(2)
        first["input_id"] = second["input_id"] = "same-input"
        with self.assertRaises(V031BuildError):
            build_requests([make_chat([first, second])], request_id="duplicate-input")

    def test_multipart_uses_declared_filename_and_mime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "real.pdf"
            path.write_bytes(b"pdf")
            body, content_type = _multipart_body(
                path,
                {"file_name": "报告.pdf", "mime_type": "application/pdf", "size_bytes": 3, "sha256": "a" * 64},
            )
        self.assertIn(b'filename="\xe6\x8a\xa5\xe5\x91\x8a.pdf"', body)
        self.assertIn(b"Content-Type: application/pdf", body)
        self.assertNotIn(b'name="sha256"', body)
        self.assertIn(b"multipart/form-data; boundary=", content_type.encode())

    def test_direct_script_uses_shared_validator_for_record_text_limit(self):
        chat = make_chat([message(1, "x" * 60000, context=[message(2, "y" * 60000)])])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "output.jsonl"
            input_path.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "wechat/script/v031.py", str(input_path), "--output", str(output_path)],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("record_text_too_large", completed.stderr)


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

    def test_queue_rejects_same_id_with_different_content(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingQueue(Path(directory) / "pending.json")
            request = build_requests([make_chat([message(1)])], request_id="conflict")[0]
            queue.add(request)
            changed = dict(request)
            changed["records"] = [dict(request["records"][0], text="changed")]
            with self.assertRaisesRegex(V031BuildError, "内容冲突"):
                queue.add(changed)

    def test_ack_and_terminal_job_ids_and_statuses_are_checked(self):
        ack = {"contract_version": "0.3.1", "job_id": "job-1", "request_id": "request-1", "status": "queued"}
        with self.assertRaises(V031BuildError):
            validate_ack(ack, job_id="job-2")
        terminal = {
            "contract_version": "0.3.1",
            "job_id": "job-1",
            "request_id": "request-1",
            "status": "succeeded",
            "progress": {"total_records": 1, "finished_records": 1, "failed_records": 0},
            "result": {
                "contract_version": "0.3.1",
                "request_id": "request-1",
                "status": "no_tasks",
                "record_results": [{"input_id": "input-1", "status": "success", "task_ids": [], "warnings": [], "coverage": {"complete": True, "uncertain": False}, "attachments": []}],
                "tasks": [],
                "errors": [],
            },
        }
        self.assertEqual(validate_job(terminal, job_id="job-1")["status"], "succeeded")

    def test_request_level_failure_counts_all_records_as_failed(self):
        request = build_requests([make_chat([message(1)])], request_id="request-level-failure")[0]
        result = {
            "contract_version": "0.3.1",
            "request_id": request["request_id"],
            "status": "failed",
            "record_results": [],
            "tasks": [],
            "errors": [
                {
                    "input_id": None,
                    "asset_id": None,
                    "block_id": None,
                    "stage": "request",
                    "code": "agent_unavailable",
                    "message": "识别服务暂不可用",
                    "retryable": True,
                }
            ],
        }
        job = {
            "contract_version": "0.3.1",
            "job_id": "job-request-level-failure",
            "request_id": request["request_id"],
            "status": "failed",
            "progress": {"total_records": 1, "finished_records": 1, "failed_records": 1},
            "result": result,
        }
        self.assertEqual(validate_result(result, request=request)["status"], "failed")
        self.assertEqual(validate_job(job)["status"], "failed")

    def test_retryable_upload_is_recovered_before_import(self):
        class FlakyClient:
            def __init__(self):
                self.upload_calls = 0
                self.submitted = []

            def upload(self, path, metadata):
                self.upload_calls += 1
                if self.upload_calls == 1:
                    raise V031RemoteError("queue_busy", "暂时不可用", retryable=True)
                return dict(metadata, file_ref="asset:" + "b" * 24)

            def submit_import(self, request):
                self.submitted.append(request)
                return {
                    "contract_version": "0.3.1",
                    "job_id": "job-recovered",
                    "request_id": request["request_id"],
                    "status": "queued",
                }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"report")
            client = FlakyClient()
            [request] = build_requests(
                [make_chat([message(1, "[文件] report.pdf", type_code=49, assets=[{"path": str(path)}])])],
                request_id="recover-upload",
                uploader=client,
            )
            self.assertEqual(request["records"][0]["attachments"][0]["status"], "failed")
            queue = PendingQueue(Path(directory) / "pending.json")
            queue.add(request)
            responses = submit_pending(client, queue)
        self.assertEqual(client.upload_calls, 2)
        self.assertEqual(len(client.submitted), 1)
        self.assertEqual(client.submitted[0]["records"][0]["attachments"][0]["status"], "available")
        self.assertEqual(responses[0]["job_id"], "job-recovered")
        self.assertEqual(queue.pending(), [])

    def test_recovery_after_an_attempt_uses_a_new_request_id(self):
        class OfflineThenRecoveredClient:
            def __init__(self):
                self.upload_calls = 0
                self.submitted = []

            def upload(self, path, metadata):
                self.upload_calls += 1
                if self.upload_calls <= 2:
                    raise V031RemoteError("queue_busy", "网络中断", retryable=True)
                return dict(metadata, file_ref="asset:" + "c" * 24)

            def submit_import(self, request):
                self.submitted.append(request)
                return {
                    "contract_version": "0.3.1",
                    "job_id": "job-new-id",
                    "request_id": request["request_id"],
                    "status": "queued",
                }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"report")
            client = OfflineThenRecoveredClient()
            [request] = build_requests(
                [make_chat([message(1, "[文件] report.pdf", type_code=49, assets=[{"path": str(path)}])])],
                request_id="recover-after-attempt",
                uploader=client,
            )
            original_id = request["request_id"]
            queue = PendingQueue(Path(directory) / "pending.json")
            queue.add(request)
            first = submit_pending(client, queue)
            second = submit_pending(client, queue)
            pending_batches = queue.pending()
        self.assertEqual(first[0]["error"]["retryable"], True)
        self.assertEqual(len(second), 1)
        self.assertEqual(client.upload_calls, 3)
        self.assertNotEqual(client.submitted[0]["request_id"], original_id)
        self.assertTrue(client.submitted[0]["request_id"].startswith("recovery:"))
        self.assertEqual(pending_batches, [])

    def test_permanent_queue_errors_and_attempt_limit_stop_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingQueue(Path(directory) / "pending.json")
            request = build_requests([make_chat([message(1)])], request_id="bounded-retry")[0]
            queue.add(request)
            queue.mark_error("bounded-retry:1", {"error": {"code": "forbidden", "retryable": False}})
            self.assertEqual(queue.pending(), [])

            request = build_requests([make_chat([message(2)])], request_id="attempt-limit")[0]
            queue.add(request)
            for _ in range(MAX_QUEUE_ATTEMPTS):
                queue.mark_error("attempt-limit:1", {"error": {"code": "queue_busy", "retryable": True}})
            self.assertEqual(queue.pending(), [])


if __name__ == "__main__":
    unittest.main()

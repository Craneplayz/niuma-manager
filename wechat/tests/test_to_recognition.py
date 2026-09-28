import unittest

from ddl_text.contract_v02 import validate_request
from wechat.script.to_recognition import to_requests

CHAT = {
    "schema_version": 1, "chat_name": "合成课程群", "chat_username": "10000000000@chatroom",
    "exported_at": "2026-09-28T12:00:00+08:00",
    "messages": [
        {"time": "2026-09-27T20:15:00+08:00", "type_code": 1, "content": "请于2026.9.29 9:00前提交课程回顾。",
         "message_id": {"server_id": 1}},
        {"time": "2026-09-27T20:16:00+08:00", "type_code": 3, "content": "[图片]", "message_id": {"server_id": 2}},
        {"time": "2026-09-27T20:17:00+08:00", "type_code": 1, "content": "  ", "message_id": {"server_id": 3}},
    ],
}


class ToRecognitionTest(unittest.TestCase):
    def test_text_messages_become_valid_v02_request(self):
        [request] = to_requests([CHAT])
        validate_request(request)
        [record] = request["records"]
        self.assertEqual(record["platform"], "wechat")
        self.assertEqual(record["source_label"], "合成课程群")
        self.assertEqual(record["published_at"]["value"], "2026-09-27T20:15:00+08:00")

    def test_batches_at_100_records(self):
        chat = dict(CHAT, messages=[dict(CHAT["messages"][0], message_id={"server_id": i}) for i in range(150)])
        requests = to_requests([chat])
        self.assertEqual([len(r["records"]) for r in requests], [100, 50])
        for r in requests:
            validate_request(r)


if __name__ == "__main__":
    unittest.main()

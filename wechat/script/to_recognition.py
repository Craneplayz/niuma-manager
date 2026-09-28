"""Convert exported WeChat chat JSON into source-to-text v0.2 JSONL requests.

Usage: python wechat/script/to_recognition.py wechat/data/*.json > requests.jsonl
Only text messages (type_code 1) are sent; attachments, account and member IDs are dropped.
"""
import json
import sys
import uuid
from pathlib import Path

BATCH = 100  # v0.2 caps a request at 100 records


def to_records(chat):
    records = []
    for m in chat.get("messages", []):
        text = m.get("content")
        if m.get("type_code") != 1 or not isinstance(text, str) or not text.strip():
            continue
        item_id = str(m["message_id"]["server_id"])
        time = m.get("time")
        records.append({
            "input_id": f"wechat:{chat['chat_username']}:{item_id}",
            "text": text,
            "text_quality": "complete",
            "platform": "wechat",
            "published_at": {"value": time, "precision": "datetime", "basis": "source"} if time
            else {"value": None, "precision": "unknown", "basis": "unknown"},
            "collected_at": chat.get("exported_at"),
            "timezone": "Asia/Shanghai",
            "timezone_basis": "source",
            "source_url": None,
            "source_label": chat.get("chat_name") or None,
            "source_item_id": item_id,
            "course_or_project": None,
            "context": [],
            "upstream_hints": None,
        })
    return records


def to_requests(chats, request_id=None):
    # ponytail: batches by count only; add a byte cap if long messages push a batch past 2 MB
    request_id = request_id or f"wechat:{uuid.uuid4()}"
    records = [r for chat in chats for r in to_records(chat)]
    return [
        {"contract_version": "0.2", "request_id": f"{request_id}:{i // BATCH + 1}", "records": records[i:i + BATCH]}
        for i in range(0, len(records), BATCH)
    ]


def main(paths):
    chats = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    for request in to_requests(chats):
        print(json.dumps(request, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])

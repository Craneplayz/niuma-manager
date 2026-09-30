"""Combine validated semantic time proposals; do not parse source wording."""


def unknown(raw, basis, kind="unknown", zone_name=None):
    return {"value": None, "precision": "unknown", "timezone": zone_name,
            "kind": kind, "status": "unknown", "raw": raw,
            "basis": basis, "alternatives": []}


def combine_times(items, default_zone=None):
    if not items:
        return unknown(None, "原文未给出可确定的完成日期。", zone_name=default_zone)
    # The model identifies urgency by meaning, not a list of literal spellings.
    if any(item["value"] is not None and item["kind"] == "deadline" for item in items):
        items = [item for item in items if item["kind"] != "urgency"]
    kinds = {item["kind"] for item in items}
    zones = {item["timezone"] for item in items}
    kind = items[0]["kind"] if len(kinds) == 1 and "urgency" not in kinds else "unknown"
    zone_name = items[0]["timezone"] if len(zones) == 1 else None
    raw = "；".join(item["raw"] for item in items)
    if any(item["value"] is None for item in items):
        return unknown(raw, "；".join(dict.fromkeys(item["basis"] for item in items
                                                 if item["value"] is None)), kind, zone_name)
    if len(kinds) > 1:
        return unknown(raw, "同时引用了截止和参考时间，需要核实各自用途。", zone_name=zone_name)
    distinct = list(dict.fromkeys(item["value"] for item in items))
    if len(distinct) > 1:
        result = unknown(raw, "同一任务存在多个未消解的时间，请核实。", kind, zone_name)
        result.update(status="conflict", alternatives=distinct)
        return result
    # Equivalent proposals must not hide an inference just because it came last.
    result = dict(items[0])
    if any(item["status"] == "inferred" for item in items):
        result.update(status="inferred", basis="；".join(dict.fromkeys(item["basis"] for item in items)))
    return result

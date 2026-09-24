"""Initial text view; presentation can be replaced without changing the pipeline."""


def render_text(result, mode):
    statuses = {"success": "成功", "no_tasks": "无任务", "partial_success": "部分成功", "failed": "处理失败"}
    date_states = {"explicit": "明确", "inferred": "推断", "unknown": "未知", "conflict": "冲突"}
    notice_types = {"create": "任务要求", "update": "修改", "postpone": "延期", "cancel": "取消", "uncertain": "待核实"}
    modes = {"offline": "离线样例回放（非模型识别）",
             "drafts": "离线合成草稿处理（非真实模型调用）",
             "real": "真实 Agent（DeepSeek 经 Codex）"}
    lines = ["运行模式：" + modes[mode],
             "请求标识：" + (result["request_id"] or "未提供"),
             "处理状态：" + statuses[result["status"]], "失败输入：%d 条" % len(result["errors"])]
    if result["status"] == "no_tasks":
        lines.append("已处理的文本中未识别到待办。")
    for index, task in enumerate(result["tasks"], 1):
        deadline = task["deadline"]
        label = "参考完成时间" if deadline["kind"] == "reference" else "截止日期"
        lines.extend(["", "任务 %d：%s" % (index, task["title"]),
                      "候选标识：" + task["candidate_id"],
                      "课程/项目：" + (task["course_or_project"] or "未提供"),
                      "通知性质：" + notice_types[task["notice_type"]],
                      label + "：" + (deadline["value"] or "待核实") + "（" + date_states[deadline["status"]] + "）"])
        if task["description"].strip():
            lines.append("具体要求：" + task["description"])
        if deadline["raw"]:
            lines.append("时间原文：" + deadline["raw"])
        if deadline["precision"] == "date":
            lines.append("时间精度：仅日期，未指定时刻。")
        if task["prerequisites"]:
            lines.append("前置步骤：" + "；".join(task["prerequisites"]))
        if task["target_reference"]:
            lines.append("变更对象：" + task["target_reference"])
        if deadline["alternatives"]:
            lines.append("冲突时间：" + "；".join(deadline["alternatives"]))
        for source in task["sources"]:
            lines.append("来源：" + (source["label"] or source["input_id"]) + "；" + (source["url"] or "未提供链接"))
        if task["needs_review"]:
            lines.append("待核实：" + "；".join(task["review_reasons"]))
    for record in result["record_results"]:
        if "incomplete_text" in record["warnings"]:
            lines.append("提示：输入 %s 正文不完整，未见部分可能仍有待办。" % record["input_id"])
    for error in result["errors"]:
        lines.append("失败输入 %s：%s" % (error["input_id"] or "请求", error["message"]))
    return "\n".join(lines)

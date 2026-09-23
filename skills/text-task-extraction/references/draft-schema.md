# Agent 草稿协议 v0.1

只返回下面的 JSON 对象，不增加字段。每个任务必须含全部字段：

```json
{
  "tasks": [{
    "title": "提交实验报告",
    "description": "提交实验报告。",
    "prerequisites": [],
    "notice_type": "create",
    "target": null,
    "course": null,
    "evidence": [{"input_id": "msg-1", "quote": "请明天18点前提交实验报告。"}],
    "times": [{"input_id": "msg-1", "quote": "明天18点", "kind": "deadline"}],
    "review_reasons": []
  }]
}
```

- `title`：非空行动名称；`description`：原文行动含义的说明。
- `prerequisites`：有原文依据且服务于同一目的的前置步骤字符串列表。
- `notice_type`：`create/update/postpone/cancel/uncertain`。
- `target`：变更对象的逐字引用 `{"input_id": "old-1", "quote": "实验报告"}`，无法唯一确定时为 null。不是数据库任务 ID。create 时必须为 null。
- `course`：未提供课程元数据时，可用 `{"value": "ICS", "input_id": "msg-1", "quote": "ICS第一次小班课"}`；value 必须实际出现在 quote 中。未知为 null。不要将组织者名称直接当作课程。
- `evidence`：非空引用列表，每项只含 input_id/quote，且至少一项来自当前正文。不得将模型概括作为引文。
- `times`：零到多个引用，每项只含 input_id/quote/kind。kind 为 deadline 或 reference。无时间时为空列表。
- `review_reasons`：非空字符串列表或空列表，列出适用对象、课程推断、对象匹配、原文缺失等具体不确定性。

输入上下文仅用于辅助理解当前消息。引用必须属于本条输入或所给上下文，不能引用同批其他记录。所有候选的来源均由程序根据这些引用从元数据复制，模型不得返回 URL。

当前程序支持确定换算：完整 ISO/中文年月日、今天/明天/明晚/后天、本周或下周的具体星期、带可信时刻基准的 N 小时后；时刻接受 24 小时制或明确上午/下午/晚上。其余表达保留为待核实。不要为了适配这些支持范围而改写原文引文。

# Agent 内部草稿协议 v0.2

此版本仅指模型到编译器的内部草稿，不改变对外 JSON v0.2。每个 times 项都必须含 normalized；旧的纯引文草稿需迁移，没有原文解析回退。

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
    "times": [{
      "input_id": "msg-1", "quote": "明天18点", "kind": "deadline",
      "normalized": {
        "value": "2026-09-30T18:00:00+08:00", "precision": "datetime",
        "derivation": "relative", "anchor": "published_at",
        "basis": "按该消息的可信发布时间换算明天。"
      }
    }],
    "review_reasons": []
  }]
}
```

- `title`：非空行动名称；`description`：原文行动含义的说明。
- `prerequisites`：服务于同一目的的前置步骤字符串列表；每项须逐字摘录，且包含在本任务 `evidence` 的至少一个 quote 中。引用上下文中的步骤时也须在 evidence 中提供对应引用。不得改写或补造步骤。
- `notice_type`：`create/update/postpone/cancel/uncertain`。
- `target`：变更对象的逐字引用 `{"input_id": "old-1", "quote": "实验报告"}`，无法唯一确定时为 null。不是数据库任务 ID。create 时必须为 null。
- `course`：上游 `course_or_project` 非 null 时必须为 null，程序会复制元数据；不要返回 `{"value": "元数据课程名"}`，也不要给它编造引文。上游未提供时，可用 `{"value": "ICS", "input_id": "msg-1", "quote": "ICS第一次小班课"}`；value 必须实际出现在 quote 中。未知为 null。不要将组织者名称直接当作课程。
- `evidence`：非空引用列表，每项只含 input_id/quote，且至少一项来自当前正文。不得将模型概括作为引文。
- `times`：每项只含 input_id/quote/kind/normalized。kind 为 deadline、reference 或 urgency。无时间要求时为空列表。
- `review_reasons`：非空字符串列表或空列表，列出适用对象、课程推断、对象匹配、原文缺失等具体不确定性。

输入上下文仅用于辅助理解当前消息。引用必须属于本条输入或所给上下文，不能引用同批其他记录。所有候选的来源均由程序根据这些引用从元数据复制，模型不得返回 URL。

上例假定 msg-1 的可信发布时间为 `2026-09-29T10:00:00+08:00`、时区为 Asia/Shanghai；实际值只能使用当前输入的元数据，不能照抄例子。

## normalized 的全部字段

| 字段 | 约束 |
| --- | --- |
| value | `YYYY-MM-DD` 或 `YYYY-MM-DDTHH:mm:ss±HH:mm`；未知为 null；秒数不可丢失。 |
| precision | date / datetime / unknown，与 value 一致；仅日期不补时刻。 |
| derivation | absolute / relative / offset / missing_year / unresolved，含义见 skill。 |
| anchor | absolute、unresolved 为 null；relative、offset 为 published_at；missing_year 优先 published_at、缺可信发布时间才可 collected_at。引用的基准属于该项 input_id，不另外填写时间。 |
| basis | 非空的一句依据；unresolved 说明真正缺少的条件，不写长篇推演。 |

unresolved 和 urgency 的 value=null、precision=unknown、anchor=null。日期无法确定也要保留其引文。程序从合法提案生成 status 和 timezone；不让模型自行填写这两个对外字段。来源时区之外的原文时区须换算到来源时区，不能只改偏移符号。

日期与时刻分隔、中文数字、其他语言或标点形式不限制归一化；模型根据语义判断，程序只验证标准值、日历、基准、时区和逐字引文。结构通过不等于语义一定正确。

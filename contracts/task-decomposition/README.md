# AI 任务拆解 API v0.1

```text
task-decomposition/
├── README.md           # 前端、业务后端与规划模块的交接说明
├── schema-v0.1.json     # 请求、回应和错误的 JSON Schema 定义
└── examples-v0.1.json   # 追问、可确认方案、无需拆解、确认及错误的合成样例
```

前端（Dranix123）、业务后端及规划模块（Yongzhen23）以本目录为接口对齐依据；所有样例均为人工合成，不代表真实模型评测。

## 生成预览

`POST /api/decomposition/proposals` 须登录；请求含当前账号的 `taskId` 和可选 `conversation`（默认 `[]`，最多 20 条，仅 `user` / `assistant`，每条 1–4000 字且不能全为空白）。后端从账号任务读取说明、DDL、已有步骤及原始通知要求，前端不传模型凭据或整份任务。

200 回应含公开版本 `contract_version: "0.1"`、发起任务 `taskId`、`status`、`assessment`、`questions` 和 `plan`；内部模型的 `0.1-internal` 请求不属于前端协议。

| status | assessment.complexity | questions | plan | requestId |
| --- | --- | --- | --- | --- |
| needs_clarification | uncertain | 1–3 个问题 | null | 无 |
| ready_to_plan | multi_step | 空数组 | 至少 2 个步骤 | 必有，用于确认 |
| no_split | atomic | 空数组 | 恰好 1 个步骤 | 无，不提供确认入口 |

`assessment` 包含摘要、复杂度、0–1 置信度、带来源的已知事实、明确假设及缺失信息；问题包含编号、提问、理由和可选答案数组。`plan` 包含目标、策略、警告及步骤；步骤字段见 Schema，包括可检查的完成标准、正整数分钟、前置步骤、来源及假设标识。步骤 ID 唯一，依赖必须引用本方案步骤且不得成环。来源引用为 `task`、`source:<id>`、`conversation:<从0开始的索引>` 或 `assumption`，`assumption` 布尔值必须与来源列表一致。

拆解流程只生成步骤，不写日历；`scheduled_start` / `scheduled_end` 无具体时段时为 null，确认步骤不会把这两个字段写成任务排程。日期只精确到天时由后端按北京时间当天末尾传给模型，任务原有 DDL 精度保持原值；无 DDL 传 null。

## 确认方案

`POST /api/decomposition/apply` 请求必须含 `requestId` 和当前 `taskId`；后端校验账号、方案所属任务、任务集合版本及是否已应用，200 回应为 `{value: 当前账号完整任务数组, version: 新任务集合版本}`。确认会替换所选任务的步骤、重建步骤 ID 和依赖并更新预计分钟；已有日历安排保持原值。

前端在发起时固定 taskId 和对话快照，切换任务、重置或切换账号时取消等待并丢弃旧结果；显示和确认前都须核对方案 taskId 与当前选择及 requestId。取消等待不会删除服务器方案，也不会写入步骤。

## 错误

错误回应为 `{error: 提示文本}`：400 请求不合法或任务已完成/取消；401 未登录；404 当前账号不存在任务/方案；409 taskId 不匹配、方案已应用或任务版本变化；503 模型配置不可用或已有拆解请求正在运行；502 模型超时、不可用或输出不合法。提示文本用于展示，调用方按 HTTP 状态处理，409 后重新读取任务并重新拆解。

Schema 定义入口为 `$defs.proposalRequest`、`proposalResponse`、`applyRequest`、`applyResponse` 和 `errorResponse`；跨字段来源、依赖及版本约束由后端额外校验。后端及前端回归测试直接读取 `examples-v0.1.json`，无需调用模型。

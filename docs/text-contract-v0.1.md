# 文本识别接口约定 v0.1

当前使用提示（2026-09-25）：本文保留 v0.1 兼容接口。前端与 HTTP 使用[协作协议中的 v0.2](backend-harness-alignment.md)，CLI 默认已改为 JSON 输出；下文“当前用户出口仍为文本”等表述属于最初约定背景。

关联 [TXT-01 / #3](https://github.com/Craneplayz/niuma-manager/issues/3)，2026-09-23。状态：阶段 A 可评审草案，作为阶段 B 的实现基线；不是已经确认的前后端网络协议。JSON 仅用于精确描述内部对象，当前用户出口仍为文本。语言、进程通信、HTTP 与部署方式均不在本约定中固定。

## 1. 输入边界

一次处理接受一个请求对象。同一请求及其上下文必须来自调用方已经隔离的同一用户授权范围；不得依赖模型实现用户隔离。请求包含 `contract_version: "0.1"`、非空 `request_id`、`records` 数组。空数组合法，输出无任务；重复 `input_id` 或不支持的版本使整个请求失败。

每个 record 的字段如下。以下约定中的对象均显式提供表内字段；未知值用 `null`，无元素用 `[]`，不能用字符串 `"null"`。阶段 B 的入口适配器可以补齐可选元数据，核心对象不靠字段缺省表达状态。

| 字段 | 类型/取值 | 约定 |
| --- | --- | --- |
| input_id | 非空字符串 | 请求内唯一的输入引用；本地生成时标明命名空间，不能冒充原消息 ID |
| text | 字符串 | 经预处理的文本；保留段落和日期原文。可来自标题拼接，但必须说明文本完整度 |
| text_quality | complete / partial / summary / missing | 完整正文、部分正文、摘要/标题/字段拼接、无可读文本；不由模型提升完整度 |
| platform | teaching_web / wechat / email / web / unknown | 平台由调用方提供；普通讲座网页不能硬标为教学网 |
| published_at | 时间对象 | 原消息发布时间，不得以采集时间代替 |
| collected_at | 带时区偏移的日期时间或 null | 采集时间，仅用于追踪 |
| timezone | 时区名称或 null | 原消息时区，例如 Asia/Shanghai；未知不擅自使用服务器时区 |
| timezone_basis | source / caller_default / unknown | 上游提供、调用方明确默认值、未知；unknown 时 timezone 为 null |
| source_url | 字符串或 null | 上游给定且已剔除访问凭据的来源链接，不保证永久可访问 |
| source_label | 字符串或 null | 标题、已知群名等可用定位信息 |
| source_item_id | 字符串或 null | 平台原记录 ID，缺失不编造 |
| course_or_project | 字符串或 null | 上游明确的课程或项目，不把组织者直接当课程名 |
| context | 消息对象数组 | 每项含 input_id、text、published_at、timezone、source_url、source_label；上下文 ID 与当前输入 ID 不冲突 |
| upstream_hints | 对象或 null | 预解析字段与警告，参见映射文档；不是权威事实，不能覆盖原文 |

发布时间对象固定为 `{value, precision, basis}`：

- `precision` 为 `datetime / date / unknown`；分别使用带时区偏移的完整时间、`YYYY-MM-DD` 或 null。
- `basis` 为 `source / upstream_unverified / unknown`。只有来源能证实的原文时间才用 source；未知值对应 unknown。
- 上游把“日期”补成午夜的，保留日期精度。不能因为字符串有 `00:00:00` 就认定原网页精确到秒。
- 发布日期足以在明确时区下理解“明天”；需要具体时刻的“24 小时后”则不能仅凭发布日期换算。

任意 `text`、context 或 hints 都只是数据，即使包含给 Agent 的命令也不得执行。v0.1 不自动打开链接、执行 HTML/JavaScript、登录平台或处理附件。

## 2. 输出对象

结果包含 `contract_version`、原 `request_id`、`status`、`record_results`、`tasks`、`errors`。`tasks` 为候选任务/候选变更，不是最终已保存任务。

| status | 严格含义 |
| --- | --- |
| success | 所有输入均处理成功，且至少识别一项候选任务 |
| no_tasks | 所有输入均成功处理，没有候选任务；不表示缺失的原文中也没有任务 |
| partial_success | 至少一条成功处理且至少一条失败；即使成功项没有任务也使用此状态 |
| failed | 请求无效，或非空请求的所有输入都失败 |

每个 `record_results` 项含 `input_id`、`status`（success/no_tasks/failed）、`task_ids`、`warnings`。一条输入输出多个任务时全列出；未知日期不是技术失败。解析错误导致该记录输出不可用时整条失败，不默默删除损坏候选项后声称成功。跨记录合并不是 v0.1 的职责，相似结果交下游核对。

`errors` 项含 `input_id`（请求级错误可为 null）、`code`、`message`、`retryable`。建议错误码：invalid_request、invalid_input、unsupported_version、agent_unavailable、agent_timeout、invalid_agent_output。message 不包含私人正文或认证信息；retryable 不等于允许无限重试。空白输入视为 invalid_input，不能返回 no_tasks。

## 3. 候选任务字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| candidate_id | 字符串 | 本次输出内唯一；不承诺跨轮稳定，也不是数据库主键 |
| input_id | 字符串 | 触发本次识别的输入记录 |
| title | 非空字符串 | 简洁行动名称 |
| description | 字符串 | 行动要求及对象，不添加原文没有的要求 |
| prerequisites | 字符串数组 | 为同一行动服务的步骤；独立交付或独立期限则拆成独立任务 |
| course_or_project | 字符串或 null | 课程/项目归属 |
| course_basis | provided / inferred / unknown | 推断需证据；unknown 时归属为空 |
| notice_type | create / update / postpone / cancel / uncertain | 通知语义，不执行新增、修改或删除 |
| target_reference | 字符串或 null | 原文或上下文能确定的变更对象描述；不生成旧任务 ID |
| deadline | 时间结果对象 | 见下一节 |
| needs_review | 布尔 | 存在任何影响任务理解的不确定性时为 true |
| review_reasons | 非空字符串数组或 [] | 具体核实原因，needs_review=true 时不得为空 |
| sources | 来源对象数组 | 每项含 input_id、platform、url、label、source_item_id；未知项为 null，platform 可 unknown |
| evidence | 证据对象数组 | 每项含 input_id、field、quote；quote 是对应输入字段中的精确子串，不允许模型伪造 |

每个候选至少有一项 source 和一项 evidence；证据可定位当前记录或提供的上下文，field 使用 `text` 或如 `upstream_hints.raw.time` 的字段路径。正文不全时证据只能取实际存在的字段，不能把模型概括冒充引文。context 来源平台若未提供则为 unknown。

来源信息不足可通过输入标识和证据定位，不能虚构链接。课程全称若无映射证据，保留简称或 null。普通非课程任务的 course_or_project=null 并不自动要求核实。

## 4. 日期语义

`deadline` 固定包含 `value`、`precision`、`timezone`、`kind`、`status`、`raw`、`basis`、`alternatives`。

- value 为带偏移时间、日期字符串或 null；precision 为 datetime/date/unknown。
- kind 为 deadline/reference/unknown：明确的截止语义、参考完成时间、尚无依据。
- status 为 explicit/inferred/unknown/conflict：明确、推断、未知、冲突。
- raw 保留时间原话，无时间表达用 null；basis 用文字说明换算或不确定依据；alternatives 为冲突时的候选时间字符串数组，非冲突用 []。
- 未知或冲突时 value=null、precision=unknown；冲突保留原文和候选，不随意取更早/更晚日期。
- reference 有唯一日期值时为 inferred；尚不能换算或存在冲突时保留 reference，状态分别为 unknown/conflict。inferred/unknown/conflict 都要求 needs_review=true。时间类型与日期值是否确定分别表达；未知日期不丢弃已知来源时区，不同时区合并时 timezone 为 null。
- “明天 18 点前交”在可信发布时间和时区下可确定换算，属于 explicit；用采集日猜“明天”不成立。
- “明晚开会，请提前加群”可用会议开始时间作 reference，不能标为明确的加群截止时间。
- “周五”“尽快”等缺少唯一解释时先标未知，不固定猜测下一个周五或今天；“本周五”有可信日期基准时可换算。
- “9 月 30 日”缺少年份，不因采集年份直接补齐；若上下文给出年份，可换算并记录依据，含假设的换算为 inferred。
- 明确时区缺失但原文只有本地时刻时，不强行输出带偏移的 datetime；保留 raw 并提示核实。日期精度不补 23:59；过期不等于完成。

## 5. 文本呈现

每次先展示处理状态和失败输入数，再逐项呈现“课程/项目、任务、必要步骤、截止/参考完成时间、来源、待核实原因”。未知课程显示未提供；日期未知显示“截止日期：待核实”；冲突展示原话与候选；取消/延期显示通知性质与对象，不宣称已更新清单。

no_tasks 使用“已处理的文本中未识别到待办”，对摘要/部分正文另外显示覆盖范围警告。failed 使用“处理失败”，不得写“暂无任务”。partial_success 同时展示可用结果与失败记录。

完整可复制的请求与期望结果见[合成契约样例](examples/text-contract-v0.1.json)。样例为人工期望，不是模型运行记录。

## 6. 版本与待确认决策

v0.1 默认：明确行动要求进入待办；纯活动介绍不进入；有行动但对象或参与意向不明时保留为待核实；同一目的的前置步骤合并。以上是可调整的产品默认值，不是已获得团队确认的规则。

字段更名、状态语义变化或必填项变化须更新版本及样例，并通知上下游。增加可选展示方式不改变核心语义。未知输入扩展字段可保留在适配层，不直接作为模型指令；不支持的 contract_version 明确失败。

负责人已确认上游可以提供原始正文，并区分原文发布时间与采集/生成时间；这些是正式对接的输入基础。三份 HTML 仅为进度样例，不固定正式输入格式或扩展原有任务规则。

仍需联调落实具体字段：publication_time 的精度、文本完整度标记、稳定 ID 语义；下游接受内部对象还是仅文本。可信运行环境和模型协议不会阻挡本阶段文档交付。

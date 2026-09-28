# 爬虫样例观察与 v0.1 映射

关联 #3。2026-09-22 至 09-23 对负责人提供的三个 HTML 文件进行了本地静态结构检查，未执行其中脚本，未访问其中链接，未将原文件或正文复制到仓库。本页仅记录字段结构与汇总数量；样例不等于团队正式协议。

负责人补充：这些仅是进度样例，实际输入大概率并非这些活动；继续按原有任务识别规则。上游可以提供原始正文，并区分原文发布时间与采集/生成时间。下表仅供适配参考，不要求爬虫继续采用此 HTML 或 events 结构。

## 实际观察

这三个文件均为展示页，在 `script#review-data[type="application/json"]` 中内嵌数据，根字段为 `runId`、`generatedAt`、`events`。HTML 可见文本不是全部内容，直接去标签会漏掉内嵌记录。后续建议由采集侧直接交付记录或预处理文本，HTML 仅用于人工查看。

| 本地样例（按提供顺序） | 记录数 | description 缺失 | start_time 缺失 | publication_time 缺失 | parse.status=partial |
| --- | --- | --- | --- | --- | --- |
| A | 57 | 38 | 7 | 7 | 7 |
| B | 42 | 42 | 0 | 0 | 0 |
| C | 120 | 0 | 0 | 0 | 0 |

总计 219 条记录，80 条缺少 description。它们是 lecture/forum/seminar 等活动记录，不能直接视为 219 项用户待办。三份数据均包含 exact 和 context_inferred 时间解析状态；A 还包含 unresolved。上游解析成功只说明其解析器状态，不证明任务识别成功或时间来自原文明确表述。

所有记录都有来源 URL，但没有可独立确认是“完整原文”的字段。description 有值也不能据此认定内容完整。publication_time 存在午夜时间值，原文精度及其是否由默认值补齐尚不明确。raw 仅包含 time/location/organizer/speaker，没有完整通知正文。数据可能含历史活动，不得以文件名或生成时间推断事件年份。

## 映射建议

| 上游字段 | 本模块映射 | 限制/缺失策略 |
| --- | --- | --- |
| runId | request_id 的来源之一 | 与用户授权范围隔离；不是消息发布时间 |
| generatedAt | upstream_hints.generated_at | 展示文件生成时间，不能冒充 collected_at 或 published_at |
| events[].id | input_id 的来源之一 | 先验证运行间稳定性与唯一性；无保证时用请求内命名空间 |
| title + description | text | 用明确分隔符保留原始字段内容；无正文时 text_quality=summary，不能称完整原文 |
| source.source_id | upstream_hints.source_id | 平台枚举需调用方映射，未知用 web/unknown |
| source.source_item_id | source_item_id | 原样保留，不假设是全平台唯一 ID |
| source.source_url | source_url | 去除凭据后保留；链接缺失时用 null |
| publication_time | published_at | 来源/精度未确认前 basis=upstream_unverified；不能支持“明确”的相对日期换算 |
| timezone | timezone | 明确上游默认规则后用 source 或 caller_default；未确认前保留为 hint |
| start_time / end_time | upstream_hints.start_time/end_time | 活动起止时间，禁止直接映射为 deadline |
| all_day | upstream_hints.all_day | 全天活动不等于当日 23:59 截止 |
| raw.time | upstream_hints.raw.time | 保留原时间表达，供与预解析结果核对 |
| parse.status/warnings/time_resolution | upstream_hints.parse | 保留警告；partial/unresolved 不丢弃整个候选，必要时标待核实 |
| registration_url / online_url | upstream_hints 中同名字段 | 报名链接存在不代表用户必须报名，也不自动打开 |
| cancellation_status | upstream_hints.cancellation_status | 需结合原文/证据判定取消；不能据此直接删除旧任务 |
| organizers/speakers/location 等 | upstream_hints 中同名字段 | 只作背景信息；组织者不是课程名或任务执行人 |

如果适配器只有标题与结构化字段，按“标题：…\n时间原文：…”形式组成 text，并标记 summary；必须保留来自哪一字段的证据。整条无可读文本则返回 invalid_input。正文或摘要缺失不等同于采集失败，采集失败则由上游显式报告，不能传空文本冒充成功。

## 交接清单

- 推荐上游直接提供每条原始正文、独立发布时间、时区、文本完整度和来源标识；一次提交多条独立记录，避免拼成无法溯源的大段文本。
- 对正文缺失但可用的摘要，成功处理也必须带 `incomplete_text` 覆盖范围警告；能否包含任务依据现有文本判定，不能保证未见部分没有待办。
- parse 的 inferred/unresolved 信息必须传下去，不能在统一格式时消失。原文与预解析时间矛盾时保留冲突。
- 跨轮新增/修改/取消需要下游识别；本模块只提供证据与候选语义，不以本次生成的 candidate_id 充当稳定任务 ID。
- HTML 输入适配与解析实现另在 #4 的接口工作中确认；阶段 A 只完成映射，不扩大为网页采集或浏览器执行功能。

所有未知项先采用保守处理，得到组员确认后更新[接口约定](2026-09-26-text-contract-v0.1.md)与对应样例。

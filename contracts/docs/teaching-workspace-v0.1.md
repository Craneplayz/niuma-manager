# 教学网运行端视图 v0.1

根 Schema：[teaching-workspace-v0.1.schema.json](../schemas/teaching-workspace-v0.1.schema.json)；合成边界样例：[teaching-workspace-v01.json](../examples/teaching-workspace-v01.json)。此视图只供读取；所有真实性与自动入库决策依旧验证原生 v0.4 结果。

- 采集器 `--format=teaching` 输出共享 `teaching-request-v0.4.schema.json`；服务器按当前用户、教学网账号、内容摘要保存每条快照。
- `POST /api/recognition/batches` 或 `/retry` 运行请求严格为 `{ "contract_version": "teaching-run/0.1", "request_id": "服务器返回的 ID" }`；正文、可信标记和课程信息均不能由浏览器覆盖。
- 同步响应和批次查询提供 `contract_version=teaching-workspace/0.1` 展示视图：`request` 包含 request_id、records；记录沿用页面的 input_id/text/text_quality/platform/published_at/collected_at/timezone/timezone_basis/source_url/source_label/source_item_id/course_or_project/context/upstream_hints 字段，其中 text_quality 仅指正文，references 放在 upstream_hints 中。
- `result` 包含 request_id/status/record_results/tasks/errors；tasks 复用现有卡片字段（candidate_id/input_id/title/description/course_or_project/course_basis/notice_type/target_reference/prerequisites/deadline/needs_review/review_reasons/evidence/sources）。这是服务端从已验证 v0.4 决策生成的显示视图，不是 v0.2 模型结果，不通过 v0.2 正文完整性规则重新分类。
- 每个 v0.4 decision 对应一张卡；filtered 不产生卡；incomplete 投影为 incomplete_source、retryable=false，在“来源待补全”展示正文/附件缺口与来源入口，不能视为确定无任务；只有 recommendation=task 可自动入库。日期仅精确到天时保持 date；value=null 不补造时间或课程。
- `/retry` 仅重新识别已保存快照，不补抓页面或读取缺失附件；同一 incomplete 快照返回 409 且不调用模型，内容未变化的同步跳过重复识别，补全后内容变化产生新快照。模型超时/输出失败仍允许人工重试；原始结果与待补全记录继续留存。
- 数据库保留原生 v0.4 输入、结果及真实 policy_version；重复运行成功快照复用原结果及用户决定，失败快照允许重试。识别完成和自动入库前重新核验快照与账号，旧快照不可自动生效。
- 学期仅从登录后课程列表的学期标签获取：第一学期按 9 月至次年 2 月、第二学期按 3 月至 8 月的名义范围辅助补年，不冒充学校正式校历；当前学期的原生活动作业，或明确“课程作业/作业/Assignments/Homework”栏目中的非原生条目（包括邮件提交），可绑定 current_assignment，并将栏目作为适用范围证据。已知 submitted/unknown 原生状态不因栏目改为 active；教学内容、通知和普通材料仍为 course_material，未知或非当前学期不绑定。栏目绑定只提供补年范围，行动、正文完整性、显式旧日期和冲突仍须独立判断。
- 仅年代未知、日期本身已有合法推算值时，展示视图保留 inferred 日期与审核原因；不清空日期，也不授予自动入库权限。
- 教学网页面只采集课程作业、课程通知、教学内容，按此顺序识别；原始正文与 PDF/TXT 附件文本独立保存，未读附件不会自动把正文标成不完整。

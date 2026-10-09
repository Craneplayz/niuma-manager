# 教学网批次输入与结果 v0.4

本契约供 #108/#109 显式接入，输入结构改变所以使用 `contract_version="0.4"`；旧 v0.2 及微信 v0.3.1 不静默迁移；此文不新增 HTTP 路由。

- [请求根 Schema](../schemas/teaching-request-v0.4.schema.json) 与 [结果根 Schema](../schemas/teaching-result-v0.4.schema.json) 自包含，无外部 URN；以 `python -m contracts.build_teaching_v04 --check` 核对生成文件。
- [7 组样例](../examples/teaching-v04-cases.json) 覆盖日精度、补充附件、学期补年、年份依据不足、无截止、纯资讯/空课程名和一条双任务；`verified` 是测试包装，不能送进请求。
- 每个 decision 的 `pair` 冻结使用已审查的 `0.4-draft.1` [政策组件](teaching-review-v0.4-draft.md)，内层版本不冒充完整批次版本。

## 输入

请求含 `request_id` 和至多 20 条 `records`，每条包含完整政策 `record` 与 `source={label,url,course_or_project}`；source 的三项均可为 null，名称最长 512 字符，URL 最长 2048 字符且须经现有 v0.3.1 无认证公开 HTTP(S) 规则校验；source 仅用于展示，不是可信证明。

`record` 保留 `input_id/source_item_id/course_id/snapshot_id`、原文及完整度、逐份资料的读取状态和文字、来源元数据、原发布时间、采集时间、IANA 时区和可选学期证据；原字段上限与关系以政策组件为准；单信封按规范 JSON 的 UTF-8 计最多 2,000,000 字节。

`check_request` 除结构外还检查 input_id 唯一、资料/证据关系、时区和日期语义；已拼接的 v0.2 正文或 `upstream_hints` 不能直接转成完整正文、已读资料或可信学期。

可信标志不在 JSON 中；后端先核对当前账号有权访问的受控快照，再把这些**完整 entry** 的 `snapshot_digest(entry)` 作为 `verified_snapshot_digests` 参数传给识别及消费校验；digest 覆盖原文、引用、快照、学期和展示来源，任何字段改变必须重新核验；hash 本身不是签名或授权，严禁对任意客户端输入直接赋予信任。

## 输出与消费

结果含 `request_id`、`policy_version`、批次 `status` 和与输入顺序/数量完全一致的 `record_results`；每条保留 input_id、snapshot_id、source、attempted、status、decisions、warnings、error。

| 记录状态 | 含义 |
| --- | --- |
| success | 至少一项经过校验的 decision；仍可能全部待审核或过滤，不等于新增任务成功。 |
| no_tasks | 已核验来源且正文/所有资料完整，模型返回无行动。 |
| incomplete | 空草稿且读取或来源核验有缺口；保留 warning，不能缓存为确定无任务。 |
| failed | 超时、调用失败、结构/证据不合法或容量超限；decisions 为空、error 非空。 |

`attempted=false` 只允许 failed，表示预算或部署检查已阻止开始本次识别；true 表示进入一次识别尝试，不证明供应商收到请求或识别成功。

批次有失败/不完整且也有成功/无任务记录时为 partial_success，全为失败/不完整时为 failed；无失败且存在 decision 时为 success，余下为 no_tasks；空输入返回 no_tasks、空结果，不代表调用过模型。

每条最多 50 项 decision，保留标题/描述、通知性质/目标、历史判断/证据、政策 pair、recommendation、review_codes/review_reasons、filter_reason；`task` 为可交给后端进一步入库检查的明确新增行动，`candidate` 必须带待审核原因，`filtered` 保留历史过滤依据。

`contracts.teaching_io.check_pair` 检查身份/数量/顺序、来源未改写、证据存在、状态和基础政策；**保存前还须调用 `ddl_text.teaching_pipeline.validate_teaching_result` 重算完整政策和 F3 历史判断**，不能只相信模型或请求中自带的分类；无日期允许保留明确行动，不能伪造截止；更新/延期/取消仍需确认原任务。

error.code 为 agent_timeout、agent_unavailable、invalid_agent_output 或 limit_exceeded，消息不包含供应商原始异常；retryable 只是暂时故障提示，不触发自动重试；容量超限按记录失败，不截断证据或丢掉后续正常记录。

## 运行版本与重识别

真实内置 ChatClient 批次携带 `teaching-policy-v1:<sha256>`，对应 `teaching_policy_manifest(settings)` 的规则/校验、实际模型/地址、超时上限及依赖版本；它与旧 text-policy 独立，离线回放为 null，不能用于生产自动入库或标记真实模型执行。

后端必须从实际工作进程取得并保存 manifest、批次预算、逐条状态及结果；不能接受客户端声称的版本，不能将失败、未尝试或旧结果标为当前成功；输入、模型版本或读取器结果变化应按 #112 启动重新识别。

`candidate_id` 是批次记录内的序号身份，多任务重排会变化，永久任务匹配与用户确认/忽略/删除决定由后端独立维护；本接口不撤销用户决定、不自动完成 submitted 作业、不建立账号隔离或重复入库保护，前述内容须在消费端联调验收。

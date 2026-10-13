# 教学网批次输入与结果 v0.4

本契约供 #108/#109 显式接入，输入结构改变所以使用 `contract_version="0.4"`；旧 v0.2 及微信 v0.3.1 不静默迁移；此文不新增 HTTP 路由。

- [请求根 Schema](../schemas/teaching-request-v0.4.schema.json) 与 [结果根 Schema](../schemas/teaching-result-v0.4.schema.json) 自包含，无外部 URN；以 `python -m contracts.build_teaching_v04 --check` 核对生成文件。
- [7 组样例](../examples/teaching-v04-cases.json) 覆盖日精度、补充附件、学期补年、年份依据不足、无截止、纯资讯/空课程名和一条双任务；`verified` 是测试包装，不能送进请求。
- 每个 decision 的 `pair` 保留 `0.4-draft.1` 字段结构；新结果用 `classification_mode=model-v1` 显式区分模型分类，旧结果沿用历史政策。新增兼容字段不改变采集请求；生产方和消费方须一起升级，新服务可读旧记录，旧服务不能消费新结果。

## 输入

请求含 `request_id` 和至多 20 条 `records`，每条包含完整政策 `record` 与 `source={label,url,course_or_project}`；source 的三项均可为 null，名称最长 512 字符，URL 最长 2048 字符且须经现有 v0.3.1 无认证公开 HTTP(S) 规则校验；source 仅用于展示，不是可信证明。

`record` 保留 `input_id/source_item_id/course_id/snapshot_id`、原文及完整度、逐份资料的读取状态和文字、来源元数据、原发布时间、采集时间、IANA 时区和可选学期证据；原字段上限与关系以政策组件为准；单信封按规范 JSON 的 UTF-8 计最多 2,000,000 字节。

`check_request` 除结构外还检查 input_id 唯一、资料/证据关系、时区和日期语义；已拼接的 v0.2 正文或 `upstream_hints` 不能直接转成完整正文、已读资料或可信学期。

可信标志不在 JSON 中；后端先核对当前账号有权访问的受控快照，再把这些**完整 entry** 的 `snapshot_digest(entry)` 作为 `verified_snapshot_digests` 参数传给识别及消费校验；digest 覆盖原文、引用、快照、学期和展示来源，任何字段改变必须重新核验；hash 本身不是签名或授权，严禁对任意客户端输入直接赋予信任。

## 输出与消费

每项 decision 新增兼容字段 `content_type="task"|"notice"`，旧结果缺省为 task；新模型必须返回该字段。类型与 `recommendation` 独立：后者 task 表示直接收录，candidate 表示待审核，filtered 表示不进入活动列表，不能用有无截止日期区分任务和通知。

notice 是相关告知，不虚构行动；task 和 notice 均可携带有依据的 deadline。通知的公示截止、有效期限或关键提醒时间按原意保留，在 description 说明时间含义；没有日期才使用 not_provided。沿用 assessment.action 证据槽：notice 的 supported 表示事实有原文依据，并非存在执行动作；来源、证据、内容不确定性检查仍适用。新增内容类型不改变既有任务过期策略。

前端投影卡片与 workspace.tasks 都保留同名 content_type，notice 暂用同一列表、编辑、完成、删除和导出流程；不进入自动耗时估算/排期、不询问完成用时。两种类型都可设置和编辑日期，仅填写日期也有效；有日期不会把 notice 自动改成 task。历史记录不自动改型，重新识别保留用户已确认/忽略决定；旧消费端需随服务一起更新。无需数据库表迁移。[共享样例](../examples/teaching-content-types-v04.json) 覆盖无 DDL 任务、有/无日期的通知、一条原文含两种内容。

结果含 `request_id`、`policy_version`、批次 `status` 和与输入顺序/数量完全一致的 `record_results`；每条保留 input_id、snapshot_id、source、attempted、status、decisions、warnings、error。

| 记录状态 | 含义 |
| --- | --- |
| success | 至少一项经过校验的 decision；仍可能全部待审核或过滤，不等于新增任务成功。 |
| no_tasks | model-v1：模型有已取得证据支持无行动；无关的部分读取不强制改判。旧结果仍按历史覆盖规则校验。 |
| incomplete | model-v1：模型明确无法根据已有内容判断是否有行动，或来源未经核验；保留具体原因。 |
| failed | 超时、调用失败、结构/证据不合法或容量超限；decisions 为空、error 非空。 |

`attempted=false` 只允许 failed，表示预算或部署检查已阻止开始本次识别；true 表示进入一次识别尝试，不证明供应商收到请求或识别成功。

批次有失败/不完整且也有成功/无任务记录时为 partial_success，全为失败/不完整时为 failed；无失败且存在 decision 时为 success，余下为 no_tasks；空输入返回 no_tasks、空结果，不代表调用过模型。

每条最多 50 项 decision，保留标题/描述、通知性质/目标、历史判断/证据、政策 pair、recommendation、review_codes/review_reasons、filter_reason；`task` 为可交给后端进一步入库检查的明确新增行动，`candidate` 必须带待审核原因，`filtered` 保留历史过滤依据。

`contracts.teaching_io.check_pair` 和 `validate_teaching_result` 对 model-v1 仅检查身份/数量/顺序、来源未改写、证据存在、合法日期与状态一致性，不调用旧 review_codes 或重算历史分类。推荐 task 时行动须有证据且不能同时声明期限 unresolved；candidate 必须有非空具体原因；filtered 必须有原文证据与过滤原因。修改/延期/取消不能绕过既有任务确认。来源授信仅由后端核验，不由模型自报。

新模型内部每项增加 `recommendation` 与 `reason`；task 的 reason 为空，candidate 的 reason 映射为 `review_codes=["model_review"]` 与 review_reasons，filtered 的 reason 映射为 filter_reason。旧 assessment 的覆盖标志和 other_review_reasons 不再单独触发分类，日期补年的元数据引用可作辅助证据，不能替代正文/已读附件中的时间原话。

无行动输出 tasks=[] 时，模型同时提供 `empty_decision={status:"no_tasks"|"incomplete",reason,evidence}`，保存在对应 record_results。no_tasks 须有真实引用，incomplete 可无引文；非空决策或失败不能同时携带 empty_decision。新结果的 classification_mode 由识别服务固定写入，不接受模型选择旧政策。旧批次缺少这个字段时仍可校验读取，展示不会再次按当前规则改判。

[新分类八组真实合成样例](../examples/teaching-model-routing-v04-cases.json) 包含 partial 明确任务、补年、必需/补充附件、过期过滤、无任务和无期限。

error.code 为 agent_timeout、agent_unavailable、invalid_agent_output 或 limit_exceeded，消息不包含供应商原始异常；retryable 只是暂时故障提示，不触发自动重试；容量超限按记录失败，不截断证据或丢掉后续正常记录。

## 运行版本与重识别

真实内置 ChatClient 批次携带 `teaching-policy-v1:<sha256>`，对应 `teaching_policy_manifest(settings)` 的规则/校验、实际模型/地址、超时上限及依赖版本；它与旧 text-policy 独立，离线回放为 null，不能用于生产自动入库或标记真实模型执行。

后端必须从实际工作进程取得并保存 manifest、批次预算、逐条状态及结果；不能接受客户端声称的版本，不能将失败、未尝试或旧结果标为当前成功；输入、模型版本或读取器结果变化应按 #112 启动重新识别。

`candidate_id` 是批次记录内的序号身份，多任务重排会变化，永久任务匹配与用户确认/忽略/删除决定由后端独立维护；本接口不撤销用户决定、不自动完成 submitted 作业、不建立账号隔离或重复入库保护，前述内容须在消费端联调验收。

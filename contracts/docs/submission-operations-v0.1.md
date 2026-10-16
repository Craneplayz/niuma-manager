# 教学网提交状态操作接口 v0.1

`observation.actions[].link_basis=model_verified` 表示后台跨来源关联已经完成事实复核并应用；该字段与 `server_exact`、`user_confirmed` 区分来源。只对完整覆盖任务的单一接收入口投影到此接口，联合提交条件不能变成任一入口可单独完成任务。

`submission-operations/0.1` 是 #110 的业务接口契约，复用 [submission-policy/0.1](submission-policy-v0.1.md)，不改识别输入 v0.2/v0.3.1；运行端由 `server/submission_runtime.py` 和前端导入页实现，本目录保留格式和纯转换参考。

## 查询与操作

以下路由以登录会话鉴权；微信上传凭据、模型输出和导入 JSON 均不能调用这些控制操作。

| 方法与路径 | 输入 / 输出根 | 行为 |
| --- | --- | --- |
| GET `/api/sources/submissions?source_account_id=...&cursor=...` | directory | 必填账号内 source_account_id，cursor 可省略；每页最多 100 条受控教学网作业记录，不限于已生成任务的记录。 |
| GET `/api/sources/submissions/{record_id}` | detail | 返回当前快照、控制版本、范围和所有永久行动及显示标题。 |
| GET `/api/sources/submissions/{record_id}/source` | teaching-workspace/0.1 请求展示根 | 当前账号的完整已保存正文与引用；用于关联确认时携带详情中的 `expected_snapshot_id`、`expected_control_revision` 查询参数，任一版本不符返回 409。未读取部分明确标注。 |
| POST `/api/sources/submissions/{record_id}/operations` | operation → receipt | 原子应用显式用户操作，首次成功 201，同一请求重放 200。 |

record_id 是服务器分配的稳定、不透明 ID；既有 input_id 原样保存在 observation.snapshot 中，不能截短、trim、拼进路由或按标题猜测。record_id 绑定登录用户、source_account_id、input_id、course_id、content_id；详情与操作都须再次按用户查询全部行动。跨账号资源和不存在资源均返回 404，分页游标也必须绑定登录用户与来源账号，不能从另一账号延续。

detail.observation 复用内部根，仅用于显示；客户端操作不得携带 state、basis、sequence、link_basis、完成状态、verified_digest 或 actor，也不能回传整个详情授权自动完成。action_labels 与 observation.actions 必须一一对应，课程名未知时 course_or_project=null；界面需结合已有来源原文查看入口显示完整来源与所有行动，不能只显示用户选中的部分。

关联界面先读取 detail，再使用其中两个版本字段条件读取 source；服务端在同一事务中比较版本并读取原文。发生 409 时丢弃本次详情，重新打开核对，不允许用新版版本配旧原文继续保存。无查询参数的 source 读取保留为只读兼容接口，不能作为关联确认依据；来源在读取后再次更新时由 POST 的版本检查拒绝陈旧确认。

每个信封规范 JSON UTF-8 至多 250 KB，其中 observation 沿用 200 KB 上限；最多 100 个行动，显示标题至多 512 字符且无控制字符。服务端请求读入即限制体积，超限返回 400，不静默截短 input_id、丢弃行动或分页后冒充整份来源；无法完整提供来源/行动时不开放整份 submission_only 确认。

所有操作带 operation_id、record_id、expected_snapshot_id、expected_control_revision；路径与正文 record_id 必须一致。

| kind | 额外字段 | 作用 |
| --- | --- | --- |
| associate_actions | scope、links | 用户明确确认当前整份来源的范围，并逐一标注所有已有 action_id 的 relation；不增加行动、不修改任务完成状态。 |
| reopen_actions | 非空 action_ids | 复用内部控制：重新开启明确关联的提交任务并暂停来源自动完成，保留手改内容。 |
| resume_source_completion | 非空 action_ids | 仅解除所选提交任务的手动暂停；不解除来源核对状态、不立即完成任务。 |
| resolve_source_review | action_ids=[] | 仅解除当前快照的 review_pending；不改变来源事实、不重开任务、不解除手动暂停。 |

associate_actions 必须覆盖当前记录全部行动，每个 action_id 恰好一次；relation 为 assignment_submission/other/unknown。submission_only 表示用户确认整份当前来源只包含提交行动（即使尚未生成任何任务），因此全部现有行动都必须是 assignment_submission；mixed 可包含其他或尚不确定的行动，unknown 保留不确定范围，不能据此整条跳过模型。

关联操作对发生变化的明确关系写入 user_confirmed，对 unknown 写入 unresolved；未变关系保留原授信依据。它不解除已有手动暂停或 review_pending，不复活 ignored/deleted，不修改手动完成或已编辑内容；要取消暂停任务的提交关联，先显式恢复来源自动完成，再刷新版本进行关联。

## 事务、幂等与来源刷新

1. 服务端从数据库构造 detail，先完成登录用户、来源账号、稳定课程/作业身份和永久行动的授权解析；纯校验函数和摘要均不能替代鉴权。
2. 幂等键是 `(登录用户, submission-operations/0.1, operation_id)`；比较完整 operation 的规范 JSON 摘要。同键同请求返回已保存回执，同键不同内容（含 record_id、版本或动作）返回 409；重放先于快照版本检查，用户或来源已删除/撤权则不得返回旧私密回执。
3. 在同一事务内重新检查 snapshot_id、control_revision 和全部行动映射；成功后控制版本恰好加一，保存控制状态、关联、登录 actor、服务器时间、前后审计及原始回执。两个相同版本的并发操作只能有一个成功，失败不得留下部分写入。
4. receipt 记录该操作提交时的完整详情与控制版本；后来来源更新后，幂等重放仍返回原回执，客户端随后 GET 最新详情，不能把回执当最新来源状态。
5. 新采集的 snapshot_id/sequence 由服务端生成；普通重复采集可保留已审计的永久行动关系，但正文或附件集合变化时，不能直接沿用此前人工确认的整份 submission_only 范围。课程/作业身份变化必须分配新 record_id，不迁移旧行动关系；原记录的手动暂停和 review_pending 始终保留，新的或无法精确对应的行动保持 unknown；所有影响控制或行动选择的变化须推进控制版本。
6. 本接口只修改控制与关联；下次受控来源观察再按内部 decide 规则处理 submitted，不得将客户端确认变成 submitted 事实。来源流程须读取新控制版本，不能被“正文未改变”的识别缓存跳过；结果落库也须比较快照和控制版本，防止迟到识别覆盖用户操作。
7. 后端真正执行自动完成时，与任务完成状态、未来规划释放和旧提案失效在同一事务中提交；保留历史安排。候选退出活动区须保存审计，不等于删除来源缓存。
8. 教学网自动完成的 `completedAt` 使用北京时间的业务日期，审计时刻及规划比较仍使用 UTC 时刻。

400 invalid_request：格式、路径/正文不符、重复/缺失/未知行动或当前状态不允许操作；401 unauthorized：未登录；404 not_found：账号/记录不可访问；409 conflict：同幂等键不同请求、快照/控制版本过期；503 unavailable：事务或服务暂不可用。错误文本不包含私密原文、其他账号标识或内部异常；重试 503 沿用原 operation_id，409 刷新后由用户重新确认并使用新 operation_id。

## 校验与交接

`schemas/submission-operations-v0.1.schema.json` 自包含且可直接校验五种信封；Python 用 root_schema("detail") 等选择独立根，并调用 check 完成引用、状态及上限检查。apply_operation 只返回计划、回执与审计，OperationConflict 对应 409；其余输入校验错误对应 400，持久化错误不得伪装成输入错误。request_digest 仅供后端持久幂等，不提供 HTTP、授权或事务。

```powershell
python -m contracts.submission_operations --check
python -m unittest contracts.tests.test_submission_operations -q
```

现已由 `server/submission_runtime.py` 对接登录鉴权、游标、持久幂等与事务，`frontend/src/submissions-ui.js` 提供操作入口；采集方提供稳定课程/作业身份及状态事实。审查按团队公约执行；合成运行端验收通过不等于生产环境已经部署。

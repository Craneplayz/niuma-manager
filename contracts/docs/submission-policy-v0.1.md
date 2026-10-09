# 教学网已提交状态决策 v0.1

本契约对应 #110：按已确认产品规则 submitted 视为完成，不判断成绩或通过情况；这里只提供共享输入校验、纯决策参考与样例，不抓取页面、不调用模型、不完成数据库或 UI 接入。

```text
contracts/submission_policy.py                    # 来源/行动验证、顺序检查与纯决策参考
contracts/schemas/submission-policy-v0.1.schema.json # 自包含后端内部观察对象根
contracts/schemas/submission-control-v0.1.schema.json # 自包含显式用户操作根
contracts/examples/submission-policy-v01-cases.json # 首次提交、多行动、状态变化及不可信输入
contracts/tests/test_submission_policy.py          # 来源、关联、保留决定和迟到观察反例
```

## 服务端观察对象

`contract_version="submission-policy/0.1"` 的 observation 由服务端从当前账号受控采集记录及已有永久行动映射构造；它不是模型输入，也不是任意导入 JSON 的自动完成 API，不改动 #127 已有 v0.2 hints。

snapshot 含账号内 source_account_id、稳定 input_id/course_id/content_id、snapshot_id、递增 sequence、observed_at、state 和 basis；sequence 由后端在同一账号和同一作业下持久生成，不能取客户端序号、模型值或仅靠时间排序。

input_id 原样继承已有 v0.2 非空白字符串，可含中文、斜杠、空格及超过 160 字符；不得 trim 或改写，长度受 observation 的 200 KB 总上限约束；其余服务端分配 ID 仍限制为 1–160 个 ASCII 字母、数字或 `_.:-`。

| state | basis | 含义 |
| --- | --- | --- |
| submitted | review_submission_history_title | 按已确认产品规则，受控原生作业页的提交历史标题作为提交状态；正文/导航中的同名文字不算。 |
| active | active_assignment_page | 正常原生作业页面；不单独证明“撤回”或“要求重交”。 |
| unknown | unknown | 不具备可靠状态；不能当成 active/submitted。 |

此版本不新增成绩/尝试/提交文件/答案采集；网页无法区分草稿、提交失败、撤回或重交时保留 unknown，不根据模型判断提升状态。

scope 为 submission_only/mixed/unknown；submission_only 必须由服务端已有明确的作业提交来源映射或人工确认给出，**不能仅凭“在作业栏目”或 submitted 标题推出整份正文只含一个提交行动**；映射不明保持 unknown。

actions 至多 100 条，以后端永久 action_id 区分 candidate/task，记录 relation、link_basis、用户 decision 和 completion_origin；server_exact/user_confirmed 关系须可审计，不能用模型标题、候选序号或课程同名判断；明确属于其他行动为 other，不知道为 unknown/unresolved。

每个 action 必填 source_completion_paused；用户主动重开任务时置 true，只适用于已明确对应的提交任务。observation 必填 review_pending 和 control_revision；这三个字段从账号内数据库恢复，与来源快照一起纳入摘要，不能每次采集重置默认值或从模型/hints 获取。新建记录可初始化 false/0；旧记录已有手动重开或待核对历史时先恢复相应状态，不能按新记录处理。

参数 verified_digest 只在后端核对当前登录账号、该来源记录和全部行动映射后提供，覆盖完整 observation；同时传入服务器解析的 source_account_id/input_id、latest_sequence 和 previous_state；hash 本身不是授权，不能对任意导入值算 hash 就放行。

## 决策及转换

| 情形 | 决策 |
| --- | --- |
| 导入、来源身份不符或完整摘要不符 | untrusted：不自动完成、不撤候选，继续原有识别/审核路径。 |
| sequence 不大于已处理序号 | stale：忽略晚到或重复观察，不调用模型、不改任务。 |
| submitted + submission_only | 明确对应的候选退出活动区，已有任务只更新完成状态，首次尚无任务时不创建任何新任务且不调用模型。 |
| 对应任务 source_completion_paused=true | 保留用户重新开启的任务，不再自动完成；后续 submitted、日期变化或重新采集都不解除暂停。 |
| submitted + mixed | 只处理明确对应的提交行动；其他行动保留，识别时须排除已完成的准确映射，不能整条丢弃或批量完成。 |
| submitted + unknown 或 unknown 行动关联 | 明确关联的提交行动仍可处理，其余保留并记录 submission_scope_unknown，不能据此整条跳过模型或全部完成。 |
| active/unknown，之前不是 submitted 且无 review_pending | 不自动改变任务；走正常来源识别流程。 |
| submitted 后出现 active/unknown | reopened_review：持久设置 review_pending=true，control_revision 加一，暂停自动处理并保留全部行动。 |
| review_pending=true 的任何后续观察 | 持续 needs_review=true、skip_model=true，不完成任务或撤候选；连续 active/unknown、甚至恢复 submitted 都不能自动解除。 |

后端处理 complete_action_ids 时保留手改标题/描述、来源和历史，只改变完成状态；withdraw_candidate_ids 退出活动候选但保留审计，不新建一条已完成任务；ignored/deleted/manual completed 一律保留，不能复活或覆盖。

`check_successor` 校验同一来源/课程/作业、递增 sequence、新快照身份和采集时间不倒退；实际提交还需账号范围的事务比较，旧识别迟到不能重新把 submitted 的提交行动放回候选；先持久化来源状态，再进行精确对应操作。

## 显式用户操作与持久化

`control_schema()` / submission-control-v0.1.schema.json 定义内部用户操作，`apply_user_control(observation, operation)` 返回新 observation 及审计内容，不自行鉴权或写库；公开请求与回执见独立的 [submission-operations/0.1](submission-operations-v0.1.md)。

| kind | action_ids | 行为 |
| --- | --- | --- |
| reopen_actions | 非空，明确对应的已保存提交任务 | 从 completed 改为 confirmed、清除 completion_origin，并设置 source_completion_paused=true；已有 confirmed/edited 保持原决定，仅暂停来源自动完成。 |
| resume_source_completion | 非空，当前已暂停的提交任务 | 用户明确接受未来来源自动完成，清除所选任务暂停；不清除来源 review_pending，也不立即改变任务完成状态。 |
| resolve_source_review | 空数组 | 用户确认当前快照，清除 review_pending；不解除任何任务的手动暂停，不自动撤销完成或创建重复任务。 |

每次操作携带 operation_id、expected_snapshot_id、expected_control_revision；服务端在同一账号/来源事务内核对当前快照和控制版本，过时确认要求刷新，不能套用到新状态。忽略/删除的任务、候选、其他行动或外部 action_id 都不能通过这些操作复活。需要重做时用 reopen_actions；仅确认来源提示不等于重新开启任务。

每次用户操作 control_revision 加一，服务端保存操作、前后控制状态、行动决定、登录用户与操作时间；相同账号和 operation_id 的相同请求返回已保存回执，不再执行，改动请求内容应冲突。纯函数不提供持久幂等，重复旧操作会因版本不符拒绝，不能用该异常代替后端幂等回执。

处理新来源时，将 decide 返回的 review_pending/control_revision 与新来源 sequence/state、行动结果原子保存；结果落库前重新比较计算时的 snapshot_id/control_revision，迟到结果不能覆盖用户操作。未授信/过时观察不得更新来源或控制状态；两类结果都会保留已有 review_pending。所有改变用户决定/暂停/待核对状态的入口都须推进控制版本；服务重启恢复这些字段，不能仅保存最近一次 previous_state。

明确解除后，以已保存最新来源 state 作为下一次 previous_state；后续新的 submitted→active/unknown 再次触发核对。人工确认的 HTTP/UI、数据库事务和审计存储由后端实现，内部策略本身不提供运行端路由。

只有可信且 submission_only 的首次观察才保证零模型调用；混合行动或未知范围属于真实语义缺口，应保留核对路径；不读取到附件不影响已有可信提交状态的处理。

完成任务后复用已合入 #128 的未来规划释放和旧提案失效，不删除历史安排；重识别用户决定遵循 #112 的永久行动映射，两个操作都在账号/来源/记录范围内进行。

## 验证与交接

```powershell
python -m pip install -r contracts/requirements-dev.txt
python -m contracts.submission_policy --check
python -m unittest contracts.tests.test_submission_policy -q
```

Ken 审查 scope 与状态转换的产品边界并最终合并；foolyuyu 在 #107/#127 提供采集状态事实，Dranix 在 #110 接受控来源与永久行动关系、状态持久化和事务，识别侧不会从文本推断已完成；通过这些合成检查不表示实际账号自动完成已上线。

# 定向重新识别业务契约 v0.1

本契约对应 #112，基于 Dranix 的操作提议和现有 UI 反馈，只处理服务端已保存的 v0.2 文本记录；不依赖教学网 v0.4 或附件读取 PR，不替换微信 v0.3.1 文件队列，也不声明这些路由已经部署。

```text
contracts/reprocessing.py                    # 根 Schema、状态/身份校验和纯关联决策参考
contracts/schemas/reprocessing-v0.1.schema.json # 生成的自包含 bundle
contracts/examples/reprocessing-v01-cases.json # 目录、成功/失败/停止/重启和多行动共享样例
contracts/tests/test_reprocessing.py           # 状态、版本、迟到响应及用户决定反例
```

## 操作

| 业务 API | 根 Schema | 行为 |
| --- | --- | --- |
| GET /api/recognition/reprocessing/sources | sources | 当前登录账号的已保存来源及通知目录，分页；无账号密码、原文和文件句柄。 |
| POST /api/recognition/reprocessing/runs | start → run | operation_id、source_account_id、非空 input_ids，至多 100 条，服务端恢复原文和来源事实。 |
| GET /api/recognition/reprocessing/runs | runs | 当前账号的持久化运行列表，分页。 |
| GET /api/recognition/reprocessing/runs/{run_id} | run | 查询逐条状态、版本、旧结果是否保留与用户决定摘要。 |
| POST /api/recognition/reprocessing/runs/{run_id}/stop | stop → run | 幂等停止，不再派发排队记录；当前调用可清理退出，但迟到结果不得写入。 |
| 操作失败 | error | invalid_request=400、not_found=404、conflict=409、unavailable=503；不要返回内部异常。 |

所有信封标记 `contract_version="reprocessing/0.1"`；原识别请求/结果仍是 v0.2，不能把这个业务信封送给模型；`root_schema(kind)` 返回无需外部引用的独立根，`check(kind,value)` 同时执行语义校验。

单信封最多 2 MB，列表页最多 50 个来源、每个来源 100 条目录记录，运行列表每页最多 20 项；目录的 `next_cursor` 应封装当前遍历位置及来源账号，后端仍须重新检查登录账号权限，不能由游标授权；必须遍历后续页才能声称已列全。

空 input_ids 不是全量运行；任意原文、user_id、来源信任或策略版本额外字段均拒绝；source_account_id 为服务端账号内不透明 ID，不能直接使用教学网用户名或仅有其 hash 充当授权。

## 幂等、运行与停止

- 相同应用账号内，相同 operation_id 且来源/选中 input_id 集合相同返回原运行，列表顺序无关；更改选择返回 409，不同应用账号独立；同一原始提交的显示顺序固定保留，校验用 `same_operation`、`check_start_run`。
- 创建运行时在事务内保存选定记录的输入版本及原文快照；后续来源更新不改写已排队运行；每条记录当前应接纳的 run_id 也由服务端持久化，新运行只覆盖相同来源/记录的接纳标识。
- `revision` 每次提交状态快照加一；`check_transition` 只接受相邻已提交快照，查询可以跳版本但不能将跨版本查询直接当作一个合法转换；服务端应保存或测试真实相邻转换。
- 记录状态为 queued/running/succeeded/failed/cancelled/interrupted/superseded；counts 按七种状态逐项计算且总和等于 total，不能将未处理伪装成失败或 no_tasks；attempted 表示已进入一次执行尝试，不证明供应商收到请求。
- 全部成功为 succeeded，全部失败为 failed，成功与失败/过时结果混合为 partial_success；停止后含 cancelled/interrupted 为 stopped，重启或全部被新运行替代为 interrupted；终态运行不可重新开启，重试使用新的 operation_id。
- 停止在事务中将 queued 置 cancelled；有正在执行的记录时为 stopping，否则 stopped；正在执行的调用结束后置 interrupted，不接纳结果，用户可用新操作显式重试。
- 服务重启后的活动记录标为 interrupted，queued 的 attempted=false、已发起的 running 保持 attempted=true；不要自动重新发送无法确认是否已计费的请求，用户显式启动下一次运行。
- 事务提交前按登录账号 + source_account_id + input_id 读取最新 run_id，并用 `may_commit` 检查仍为 running；即使模型成功，已停止或过时运行也不得覆盖新结果；内存布尔标志不能代替事务检查。

`policy_version` 只接受实际 v0.2 工作进程的 text-policy-v1，成功记录不可为空；queued/cancelled 为空，失败或中断可保留已取得的真实版本，不能给旧结果补写新版本；真实 manifest 另存服务器运行元数据。

## 结果与永久行动关联

新识别结果先经过原 v0.2 消费校验；失败或中断必须保留旧有效结果，识别出 no_tasks 也不能删除已确认、手改、忽略、完成或删除的用户决定。

`association` 是共享**内部测试/服务端关联对象**，不是公开 start 请求；其永久 action_id 由 server 分配，以账号、来源账号、input_id 保存，links 只能来自已验证的稳定映射或明确人工关联，不能让客户端自报绑定。

`association_plan` 输出可审查的替换/保留计划：有明确映射的受保护行动保留原决定并抑制重复候选；仅 pending 可替换；无旧决定的新增结果成为新 pending；存在受保护行动而无法匹配时 hold_record=true，整条记录保留旧结果，未解决候选作为差异等待核对，不先删再插。

标题、数组下标、模型 candidate_id 均不能独立建立永久对应；允许 server 保存人工确认的 links 或 confirmed_new，但必须校验候选属于当前运行，且一对一、不引用其他账号/记录；无法唯一对应的拆分/合并进入人工核对，不强行多对一。

`decision_summary` 的 preserved/replaced/new_pending/unresolved 是本次关联处理计数，不是任务总数；outcome=tasks/no_tasks 只表示新的合法识别结果；出现 unresolved 时保留旧有效结果，不能因 run 成功而自动入库全部候选。

后端必须在同一事务中进行版本比较、候选替换、任务去重和用户决定保留；这里的纯校验函数不能证明账号隔离、队列持久化、停止或数据库操作已经实现。

## 验证与接入责任

```powershell
python -m pip install -r contracts/requirements-dev.txt
python -m contracts.reprocessing --check
python -m unittest contracts.tests.test_reprocessing -q
```

Ken 审查共享契约并最终合并；Dranix 实现 server 的来源目录、幂等运行、持久化队列和事务；现有 UI 负责人接旧记录选择、刷新恢复及停止；识别侧已有 #120/#129 的实际版本与回归输入，微信/教学网 v0.4 后续接入须另行显式扩展版本。

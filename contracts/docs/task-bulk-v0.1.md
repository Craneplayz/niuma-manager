# 任务批量操作

`POST /api/tasks/bulk` 使用当前登录账号，一次处理最多 500 项普通任务、通知或待核对新增候选；稳定意图沿用任务保存队列、账号代次和回执，结构与样例以 `task_bulk.py`、`schemas/task-bulk-v0.1.schema.json` 和 `examples/task-bulk-v01.json` 为准。

请求为 `{version,operation:{id,owner,intent}}`，操作编号及 owner 规则沿用 [任务保存](task-writes-v0.1.md)。`version` 是发送时最新已确认任务版本，不进入稳定意图；`intent` 含 `kind:"bulk"`、`action`、`workspaceGeneration`、`tasks:[]`、`candidates:[]`，UTF-8 规范 JSON 不超过 100000 字节。任务项为 `{id,expected}`；候选项为 `{id,version,taskId,expected}`，其中 taskId 为稳定的新任务编号，不能与现有任务或本批其他编号冲突。

`expected` 为用户操作时的清单字段 `{title,notes,course,content_type,due,dueTime,status,completedAt}`；空文本、空日期和空时刻为 `""`，默认类型为 task，状态为 todo/doing/done。回收站使用回收前状态。候选使用清单当前投影：仅明确的 deadline 日期展示为 due，推断日期和参考日期仍留空；具体时刻换算为北京时间，含秒或毫秒的时间不擅自舍入。服务端核对完整编辑前值、候选版本和账号归属，防止批量操作覆盖后来修改。

| action | 附加字段 | 普通任务和通知 | 待核对新增候选 |
| --- | --- | --- | --- |
| complete | completedAt：用户操作当天的日期 | 未完成项设为完成；已完成项保留原完成日期 | 主动确认新增并完成 |
| reopen | 无 | 仅已完成项设为未开始，清空完成日期 | 不支持 |
| deadline | date：日期或空串 | 改为同一天，分别保留各自时刻；空串同时清除日期和时刻 | 主动确认新增，并应用所选日期及原明确时刻 |
| trash | deletedAt：带时区的 ISO 时间 | 移入回收站并保留原状态 | 记录为忽略，不创建普通任务 |
| restore | 无 | 从回收站恢复原状态 | 不支持 |

通知的完成/重新打开分别代表已阅读/未阅读。候选仅允许 create/uncertain 类型，更新、延期和取消建议继续逐项核对目标。候选确认复用原 resolve 逻辑保留来源、证据历史及提交状态保护；查看、选择和退出批量模式不改变候选决定。

整批在一个 SQLite 写事务中保存，最终任务版本只增加一次；任一任务或候选冲突、校验失败、存储异常均整批回滚，不产生部分完成。新建任务、候选状态、提交控制、必要的逆向字段和幂等回执一起提交。保存期间其他队列操作按原顺序执行，不能并发覆盖整个旧工作区。

响应为 `{version,value,operationId,undo,batches,candidates,generation,replayed?}`，value 为当前普通任务与回收站的规范任务数组，batches 为同一事务内当前账号候选展示，candidates 为本次涉及候选的轻量当前状态。重放相同操作时先核对回执，返回当前任务和候选，不重复创建或增加版本；同编号不同意图拒绝。`POST /api/tasks/reconcile` 同样识别 bulk，并返回其涉及候选的当前状态。

响应丢失后通过 reconcile 确认时，响应可附带 `bulkResults:[{operationId,undo}]`，仅包含本次 applied 的 bulk。客户端移除本地队列前保留原操作涉及的任务编号，将真实撤销引用交回界面；撤销本身的回执或账号清空后缺少有效逆向记录时返回 null，不凭原意图伪造撤销。恢复不再次执行已确认的候选操作。

`undo` 为 `{kind:"bulk",action:"undo",undoOf:原操作编号,workspaceGeneration}`，没有可撤销内容或原账号数据已清空时为 null。客户端使用新的稳定操作编号，仍向同一入口提交 `{version,operation:{id,owner,intent:undo}}`。服务端读取原操作的必要逆向字段，整体核对本次修改的字段及回收站身份；候选忽略撤销还核对原候选的 ignored 状态和精确版本，期间被撤回、再次忽略或移除时整组拒绝，避免复活已删除内容。

完成或改期接受的候选撤销后仍是普通任务，只恢复原状态或日期；忽略撤销才重新显示为待核对。撤销不覆盖后来修改的标题、说明或其他无关字段。撤销成功返回 undo:null，不提供重做。重复提交同一个撤销编号只确认回执。

SQLite 启动自动增加 `task_bulk_undo`，仅按账号和操作编号保存状态、日期、回收站身份、候选版本及原字段缺省信息，不保存标题、正文或整个任务快照。无自动过期，保证原操作重放仍能取得同一个撤销引用；账号清空时删除该账号逆向字段并增加既有代次，保留无正文回执以确认历史操作。上线前备份数据库，后端先于或与前端同版本部署，不需要另设环境配置。

验证：`python -m contracts.task_bulk --check` 核对生成 Schema，`PYTHONPATH=.:server python -m unittest contracts.tests.test_task_bulk server.test_task_bulk server.test_task_operations server.test_review_operations server.test_review_task_actions -q` 使用合成样例、临时数据库和真实 HTTP 检查原子性、版本、幂等、撤销和账号边界。

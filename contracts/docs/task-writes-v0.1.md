# 任务保存与浏览器恢复

HTTPS 和 localhost 的普通任务及待核对操作先写入按当前账号分隔的 IndexedDB，再更新页面；恢复时使用当前账号会话核对回执及服务器最新任务与候选状态，按原顺序应用尚未确认的单条操作，不重放旧的整个工作区。

`GET /api/workspace` 同时返回 `taskWriteOwner`，客户端先确认它与刚查询的登录账号一致，避免另一个页面在两次请求之间切换账号时混用任务与本地队列。

`PUT /api/data/tasks` 保留 `value` 与 `version`，新增可选 `operation: {id, owner, intent}`；`id` 是 16–100 位字母、数字、下划线或连字符，`owner` 必须等于会话用户，`intent.kind` 为 `save/status/deadline/trash/restore`。`intent.workspaceGeneration` 来自 `GET /api/workspace` 的 `taskWriteGeneration`，默认 0；测试账号清空后增加，清空前未执行的操作拒绝恢复。

成功返回 `{version, operationId}`；同账号同编号同意图的重复请求先查回执，不再次写任务、增加版本或触发副作用，并返回 `{version, value, operationId, replayed:true}`，其中任务和版本是当前值。同编号不同意图、账号不符、旧版本和已清空的账号代次返回 409；任务写入和回执在同一个事务提交。

`POST /api/tasks/reconcile` 接收 `{owner, operations:[{id,owner,intent}]}`，最多 500 条；返回 `{applied:[id], tasks:{value,version}, candidates:[{id,state,version,taskId?}], generation, bulkResults?:[{operationId,undo}]}`。回执、任务及涉及候选的轻量状态读取来自同一个 SQLite 快照；仅当前会话账号可查询，响应禁止缓存。客户端只移除已确认的本地编号，再检查各操作原本编辑的字段是否仍然匹配；不匹配时保留本地操作并提示重新读取。bulkResults 只列本次已确认的 bulk，撤销引用来自当前账号持久逆向记录及当前代次；撤销操作本身、缺少逆向记录或账号已清空时 undo 为 null，不合成旧撤销引用。

批量任务与待核对新增候选使用 `kind:"bulk"`，通过专用 `POST /api/tasks/bulk` 一次提交，普通任务 PUT 拒绝 bulk；选择、原子保存、候选状态恢复和整批撤销见 [任务批量操作](task-bulk-v0.1.md)。其回执仍只保存摘要，独立逆向表仅保存必要状态、日期和身份，不保存任务正文。

本地仅保存必要操作字段（修改的标题、说明、日期、状态或任务编号及比较前值），不保存密码、Cookie、附件或整个任务列表；操作使用 AES-GCM 加密，账号各有一个不可导出的 WebCrypto 密钥，密钥与密文由同源 IndexedDB 保存。持久暂存需要 HTTPS 或 localhost；安全入口存储禁用、损坏、容量不足或不支持加密时拒绝暂存，不提前更改任务。加密不能防止已经能运行同源脚本或控制浏览器的攻击者；共享设备应使用独立浏览器资料并退出账号。

浏览器明确报告 `isSecureContext === false` 的普通 HTTP 入口的单条与混合批量操作沿用同一意图、回执与串行保存逻辑，仅在当前页面内存保留未确认操作，不调用本地操作日志；等待服务器回执后才更新可见任务和候选。网络失败可在本页用原操作编号重试或重新连接，先 reconcile 再继续；刷新、关页或进程退出不能恢复尚未送达的操作。此兼容路径不因安全入口的 IndexedDB/WebCrypto 失败而启用，不写本地明文。

使用加密持久暂存的 HTTPS/localhost 入口中，已确认操作立即删除；正常退出/切换账号先保存当前队列，再清除空队列的密钥，其他窗口仍有待保存内容时不静默清除。会话过期或强制关闭时保留待保存内容，必须重新登录原账号恢复；不按时间静默淘汰。所有入口的显式“重新读取”均放弃本页已加载的待保存操作；浏览器清理站点数据、私密窗口结束或浏览器自行回收存储仍会使未送达内容丢失，不能承诺此类情况下恢复。

服务端长期保留仅含操作编号、意图 SHA-256、版本和时间的回执，避免旧浏览器再次上线后重复执行；不保存操作正文。后端须先于或与前端同版本部署；旧服务未返回操作编号时，前端保留暂存并报告版本不兼容。示例见 `examples/task-writes-v01.json`，结构见 `schemas/task-writes-v0.1.schema.json`。

## 待核对操作

`POST /api/recognition/candidates/{id}/resolve` 保留旧请求兼容，新增可选 `operation`。稳定意图为 `{kind:"review",candidateId,action,expectedCandidateVersion,workspaceGeneration,draft?,targetId?,expectedTarget?,newTaskId?,complete?,createdAt?,completedAt?,previewSource?}`。支持 create/update/postpone/cancel/ignore/reopen；请求的 action/version/draft/target_id/complete 必须与意图一致。`reviewed:true` 仍代表用户已经确认原文；自动加入不使用浏览器待核对队列。

`tasks_version` 在每条操作实际发送前取队列最新已确认任务版本，不计入稳定意图摘要；仍严格检查服务器任务版本，遇到 409 不自动覆盖。update/postpone/cancel 必须携带目标编辑前值 `{title,description,course,content_type,due,status,completedAt}`，空文本归一为空串、空课程和截止为 null、默认类型 task、默认状态 not_started；目标已回收或这些字段发生变化时拒绝，独立来源元数据保留。

create 提供稳定 `newTaskId`（16–100 位字母数字、下划线或连字符），服务端拒绝覆盖当前账号已有任务 ID；可提供带时区 ISO 创建时间 `createdAt`。直接完成时 `complete:true` 必须携带用户操作当天的 `completedAt` 日期，避免延迟或跨午夜后破坏后续状态比较。`previewSource` 仅用于本地预览，不决定服务器保存的真实来源。

新操作在同一事务内提交候选、任务与既有回执表，不另建日志表；确认、忽略和撤回都记录回执。新客户端响应 `{operationId,replayed,tasks,candidates,generation}`，不重复传输整批通知正文；无 operation 的旧客户端仍收到原 batches 响应。只允许候选意图通过 resolve 提交，普通任务 PUT 拒绝 review 意图。

恢复时 reconcile 返回涉及候选的当前轻量状态；候选已不存在时为 `{id,state:"missing",version:0}`。`applied` 表示该稳定操作已在历史中成功提交，不能根据候选当前状态再次执行旧操作：例如忽略后撤回，再重放旧忽略回执，只确认旧操作而不再次忽略。未执行操作的候选版本或目标字段不符时保留本地内容并提示核对；账号清空代次阻止旧未执行操作复活数据。

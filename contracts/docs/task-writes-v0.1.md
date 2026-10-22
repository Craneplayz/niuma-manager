# 任务保存与浏览器恢复

普通任务操作先写入按当前账号分隔的 IndexedDB，再更新页面；恢复时使用当前账号会话核对回执及服务器最新任务，按原顺序应用尚未确认的单条操作，不重放旧的整个工作区。

`PUT /api/data/tasks` 保留 `value` 与 `version`，新增可选 `operation: {id, owner, intent}`；`id` 是 16–100 位字母、数字、下划线或连字符，`owner` 必须等于会话用户，`intent.kind` 为 `save/status/deadline/trash/restore`。`intent.workspaceGeneration` 来自 `GET /api/workspace` 的 `taskWriteGeneration`，默认 0；测试账号清空后增加，清空前未执行的操作拒绝恢复。

成功返回 `{version, operationId}`；同账号同编号同意图的重复请求先查回执，不再次写任务、增加版本或触发副作用，并返回 `{version, value, operationId, replayed:true}`，其中任务和版本是当前值。同编号不同意图、账号不符、旧版本和已清空的账号代次返回 409；任务写入和回执在同一个事务提交。

`POST /api/tasks/reconcile` 接收 `{owner, operations:[{id,owner,intent}]}`，最多 500 条；返回 `{applied:[id], tasks:{value,version}, generation}`。回执与任务读取来自同一个 SQLite 快照；仅当前会话账号可查询，响应禁止缓存。客户端只移除已确认的本地编号，再检查各操作原本编辑的字段是否仍然匹配；不匹配时保留本地操作并提示重新读取。

本地仅保存必要操作字段（修改的标题、说明、日期、状态或任务编号及比较前值），不保存密码、Cookie、附件或整个任务列表；操作使用 AES-GCM 加密，账号各有一个不可导出的 WebCrypto 密钥，密钥与密文由同源 IndexedDB 保存。需要 HTTPS 或 localhost；存储禁用、损坏、容量不足或不支持加密时拒绝暂存，不提前更改任务。加密不能防止已经能运行同源脚本或控制浏览器的攻击者；共享设备应使用独立浏览器资料并退出账号。

已确认操作立即删除；正常退出/切换账号先保存当前队列，再清除空队列的密钥，其他窗口仍有待保存内容时不静默清除。会话过期或强制关闭时保留待保存内容，必须重新登录原账号恢复；不按时间静默淘汰。显式“重新读取”放弃本页已加载的待保存操作；浏览器清理站点数据、私密窗口结束或浏览器自行回收存储仍会使未送达内容丢失，不能承诺此类情况下恢复。

服务端长期保留仅含操作编号、意图 SHA-256、版本和时间的回执，避免旧浏览器再次上线后重复执行；不保存操作正文。后端须先于或与前端同版本部署；旧服务未返回操作编号时，前端保留暂存并报告版本不兼容。示例见 `examples/task-writes-v01.json`，结构见 `schemas/task-writes-v0.1.schema.json`。

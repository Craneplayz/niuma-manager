# 后台来源同步 v0.1

唯一契约为 `contracts/source_sync.py`，`root_schema(kind)` 提供无外部注册依赖的 JSON Schema；`check` 同时校验身份唯一和计数。模型输入仍使用教学网 v0.4，本契约仅管理业务运行。

| 接口 | 请求 / 响应 | 用途 |
| --- | --- | --- |
| POST `/api/sources/sync-runs` | start / run | consent=true、选定支持来源、operation_id；立即返回 202，后台执行 |
| GET `/api/sources/sync-runs` | runs | 当前账号最近 20 轮运行 |
| GET `/api/sources/sync-runs/{id}` | run | 持久化阶段、逐条结果、计数与错误 |
| POST `/api/sources/sync-runs/{id}/stop` | stop / run | 停止派发，已发起调用可能消耗额度，迟到结果不再落库 |

start 示例：`{"contract_version":"source-sync/0.1","operation_id":"manual:example","sources":["teaching_web"],"consent":true}`；stop 只传 contract_version。

同一账号、operation_id 返回同一运行；同账号同时只允许一轮同步，重复开始返回活动运行。全服务至多 4 轮活动同步，每轮最多 4 条识别，仍受共享模型队列限制。优先派发顺序沿用采集器的课程作业、课程通知、教学内容；执行完成顺序不保证相同。

queued → crawling → recognizing → succeeded / partial_success / failed；任何活动阶段可变为 stopped，进程重启后变为 interrupted，不自动重放不确定是否收费的请求。中断、停止只阻止后续结果写入，不删除已保存任务。每条识别结果、明确任务入库与进度在同一事务中保存，失败不回滚其他记录。

运行只包含来源标题、课程、服务端 request_id、耗时和安全错误，不包含原文、凭据或文件路径。每次读取/停止按登录用户校验；保存前再次核验来源快照、凭据版本和运行状态，不能用运行编号替代权限。

任务是否可自动入库沿用现有识别和用户决定校验；后台同步不改变任务/候选分类、不替代跨来源任务合并。前后端需一起部署新入口，原同步接口暂保留兼容。

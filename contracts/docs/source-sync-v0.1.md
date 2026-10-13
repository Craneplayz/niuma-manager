# 后台来源同步 v0.1

唯一契约为 `contracts/source_sync.py`，`root_schema(kind)` 提供无外部注册依赖的 JSON Schema；`check` 同时校验身份唯一和计数。模型输入仍使用教学网 v0.4，本契约仅管理业务运行。

| 接口 | 请求 / 响应 | 用途 |
| --- | --- | --- |
| POST `/api/sources/sync-runs` | start / run | consent=true、选定支持来源、operation_id；立即返回 202，后台执行 |
| GET `/api/sources/sync-runs` | runs | 当前账号最近 20 轮运行 |
| GET `/api/sources/sync-runs/{id}` | run | 持久化阶段、逐条结果、计数与错误 |
| POST `/api/sources/sync-runs/{id}/stop` | stop / run | 停止派发，已发起调用可能消耗额度，迟到结果不再落库 |
| POST `/api/sources/sync-runs/{id}/retry` | retry / run | 仅重新识别可恢复失败和未完成记录，不重新抓取来源 |
| GET / PUT `/api/sources/sync-schedule` | schedule_update / schedule | 当前账号定时配置，修改携带 revision 防止覆盖另一页面 |

start 示例：`{"contract_version":"source-sync/0.1","operation_id":"manual:example","sources":["teaching_web"],"consent":true}`；stop 只传 contract_version。

retry 传 contract_version、全新 operation_id 和 consent=true；运行中的批次不能重试。来源待补全、凭据改变和不可重试错误需重新获取来源，不能通过再次调用模型补齐。成功记录与用户已修改、确认或忽略的结果不重放。网络响应丢失后重复相同 operation_id 安全返回原运行。

前端进入通知导入或来源设置时恢复账号运行进度；活动运行每 2 秒查询，切换账号会清空旧页面状态并拒收迟到响应。退出页面或关闭浏览器不停止服务端运行。停止和继续按钮都针对服务端运行，不依赖浏览器保存的循环。

同一账号、operation_id 返回同一运行；同账号同时只允许一轮同步，重复开始返回活动运行。全服务至多 4 轮活动同步，每轮最多 4 条识别，仍受共享模型队列限制。优先派发顺序沿用采集器的课程作业、课程通知、教学内容；执行完成顺序不保证相同。

queued → crawling → recognizing → succeeded / partial_success / failed；任何活动阶段可变为 stopped，进程重启后变为 interrupted，不自动重放不确定是否收费的请求。中断、停止只阻止后续结果写入，不删除已保存任务。每条识别结果、明确任务入库与进度在同一事务中保存，失败不回滚其他记录。

运行只包含来源标题、课程、服务端 request_id、耗时和安全错误，不包含原文、凭据或文件路径。每次读取/停止按登录用户校验；保存前再次核验来源快照、凭据版本和运行状态，不能用运行编号替代权限。

任务是否可自动入库沿用现有识别和用户决定校验；后台同步不改变任务/候选分类、不替代跨来源任务合并。前后端需一起部署新入口，原同步接口暂保留兼容。

定时设置的共享样例见 `contracts/examples/source-sync-v01.json`。间隔为 15～10080 分钟的整数；只支持显式选定 teaching_web，不能用未知来源冒充已接入。启用时必须 consent=true 且存在教学网凭据；关闭无需重新同意，并停止当前定时批次，手动批次继续。

环境 `scheduled_sync` 默认 false（底层 `NIUMA_ENABLE_SCHEDULED_SYNC=0`）；开启环境能力后仍需账号自行启用。配置持久化，首次运行安排在设定间隔后；调度每 30 秒检查，重启不补跑全部错过周期。撞上手动 / 重试 / 定时活动运行时顺延一个间隔；全服务队列满时 60 秒后再检查。采集或识别失败保留本轮错误，不无限即时重试，下个周期正常增量获取。凭据变化会暂停配置，重新核对后才能启用。

同一数据库仅运行一个业务 API 进程；SQLite 事务保证重复调度不创建重复运行，但多 API 进程的任务恢复及模型并发需要独立协调器。关闭浏览器不影响定时执行，停止 API 会暂停调度。next_run_at 为 Unix 秒，前端按用户本地时区显示。

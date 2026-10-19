```text
server/
├── README.md                 本目录的文件说明与启动命令。
├── .gitignore               排除本地凭据加密密钥。
├── app.py                    账号、工作区与规划的本地 HTTP API。
├── usage.py                  无正文行为记录、用户反馈与管理员聚合接口。
├── error_reports.py          用户主动错误报告、受限字段、去重、限流与保留。
├── test_error_reports.py     真实 HTTP 提交、匿名反馈、权限与私人字段拒绝回归。
├── test_usage.py             后台权限、反馈评分、去重、数据最小化与保留期测试。
├── runtime_environment.py    环境身份、数据目录归属、会话名称及版本信息。
├── manage_environment.py     初始化、检查并启动开发 / 测试 / 生产环境。
├── release_images.py         记录测试实际运行镜像，并在生产启动前核对、固定镜像 ID。
├── test_release_images.py    验证缺失验收记录、镜像替换及不安全标签不能用于发布。
├── test_environment_isolation.py 验证真实三 API 的账号、数据、附件、配置与会话隔离。
├── test_container_environment.py 用临时 Docker 卷验证三环境、HTTPS 与重建持久化。
├── recognition.py            文本识别批次与候选确认接口。
├── reprocessing.py           已保存教学网通知的定向运行、停止与持久进度。
├── test_reprocessing.py      来源分页、幂等、账号隔离、停止与旧结果保留测试。
├── test_teaching_reprocessing.py  原生快照重识别、自动入库、停止及既有决定保护测试。
├── planning.py               规划直调/HTTP 切换、配置读取及并发保护。
├── model_queue.py            所有模型入口共用的 20 并发与有界 FIFO 排队。
├── test_model_queue.py       验证并发上限、先来先服务、超时与断连取消。
├── wechat_media.py           微信附件上传、账号凭证、异步导入与结果查询。
├── manual_materials.py       登录用户主动上传材料的隔离入口，不接受采集器凭证。
├── test_manual_materials.py  主动上传、加密拒绝、会话鉴权与旧作业隔离测试。
├── task_decomposition.py     AI 任务拆解、追问预览与确认写入接口。
├── source_sync.py            教学网同步、邮箱凭据与邮件读取 API。
├── email_source.py           北大邮箱 IMAP TLS 只读预览及所选正文读取。
├── email_import.py           邮箱同意与缓存策略、账号保护及后台导入编排。
├── email_import_routes.py    邮件导入 HTTP 校验、路由及公开响应。
├── email_import_store.py     邮件表结构、事务、作业与逐项进度持久化。
├── test_email_import_store.py 旧快照迁移、原子回滚及进度计数一致性测试。
├── email_header_cache.py     邮件标题快照、分页缓存与并发刷新合并。
├── email_scheduler.py        账号公平派发、执行上限与进程所有权。
├── email_mailbox_pool.py     IMAP 会话与正文缓冲容量限制。
├── email_db_gate.py          邮件进度写入与用户控制的优先调度。
├── diagnose_email_response.py 显式授权后单封重放的无正文诊断工具。
├── benchmark_email_concurrency.py 合成多账号负载、缓存与延迟基准。
├── EMAIL_TESTS.md            邮件测试逐项说明、运行命令与验证边界。
├── test_email_cache_routes.py 缓存预览 API、排除项及范围恢复回归。
├── test_email_header_cache.py 标题快照、UID 变化与并发刷新回归。
├── test_email_import_integration.py 合成 HTTP 导入、核对、保存与恢复。
├── test_email_import_parallel.py 并发计数、停止、限流与失败重试回归。
├── test_email_import_safety.py 模型排队期间的授权与账号保护回归。
├── test_email_list_filters.py 全范围筛选、排序及状态投影回归。
├── test_email_reprocess.py   缓存更新、审核决定保护与范围重识别回归。
├── test_email_scheduler.py   公平调度、取消与所有权恢复回归。
├── email_telemetry.py        无正文的邮件阶段耗时与数量指标。
├── email_test_support.py     共享合成 IMAP、模型结果与独立账号 HTTP 测试夹具。
├── test_email_capacity.py    邮箱/模型容量、限流及数据库写入调度回归。
├── test_email_recognition.py 邮件模型设置、耗时与 token 传递回归。
├── test_email_telemetry.py   无正文指标、诊断、token 聚合与保留期回归。
├── test_email_source.py      邮件主机、只读读取、解析限制及身份校验测试。
├── test_email_source_routes.py 邮箱凭据加密、预览、同意门槛与增量同步 API 测试。
├── source_jobs.py            教学网后台同步、逐条持久进度、受控并发与自动入库。
├── source_schedule.py        账号定时配置、服务端调度、凭据变化暂停及环境开关。
├── test_source_schedule.py   定时恢复、互斥派发、关闭及迟到结果保护测试。
├── test_source_jobs.py       HTTP 同步幂等、4 路并发、停止、账号变化与重启回归。
├── teaching.py               核验当前账号快照、调用 v0.4 并适配任务/通知卡片。
├── teaching_assets.py        暂存收据校验、账号隔离原件、读取缓存与证据补全。
├── test_teaching_assets.py   文件同步到自动入库、重试、缓存、变更与隔离回归。
├── benchmark_teaching_store.py 内存数据库的合成附件清理基准，不接触账号数据。
├── run_teaching_attachment_smoke.py  合成 PDF/扫描 PDF/图片/DOCX 的真实 API 验收。
├── submission_runtime.py    提交状态永久行动关联、完成/重开、持久幂等与原子更新。
├── test_submission_runtime.py 提交生命周期、独立行动、手动重开及陈旧操作回归。
├── test_submission_transitions.py 候选撤回、更新任务、合并历史和暂停恢复的 HTTP 回归。
├── public_sources.py        公开活动后台采集、账号预览和显式通知导入。
├── test_public_sources.py   公开活动幂等、版本冲突、限定来源与失败可见测试。
├── run_interface_fixture.py 浏览器验收专用临时 HTTP 服务，外部服务全为合成替身。
├── teaching_submission.py   可信原生状态、未知行动关联保护及持续来源核对。
├── test_teaching_submission.py  共享提交策略一致性和旧自动撤出记录恢复测试。
├── test_teaching.py          教学网同步、自动入库、重试和来源变化的 HTTP 回归。
├── run_teaching_smoke.py     合成网页经真实 API 到任务入库的手动验收脚本。
├── start-local-test.ps1      Windows 统一环境启动入口，Ctrl+C 停止本次服务。
├── requirements.txt          定义账号凭据加密所需的 Python 依赖。
├── requirements-test.txt     安装完整业务测试及共享附件读取依赖。
├── text_contract.py          文本识别结果的服务端契约校验。
├── test_api.py               账号与业务 API 测试。
├── test_deployment.py        公网来源、HTTPS Cookie、健康检查与本机兼容测试。
├── deploy/                   Docker 全栈、HTTPS 代理与持久数据部署配置。
│   ├── ENVIRONMENTS.md        三环境启动、旧生产接管、验收与回滚步骤。
│   ├── TEAM_GUIDE.md          团队本机开发、SSH 测试和协作发布的简明指南。
│   └── environments/         本机私有环境配置，不提交也不进入镜像。
├── test_source_sync.py       教学网同步权限与请求转换测试。
├── test_incremental_recognition.py  增量筛选、逐条识别与失败重试回归。
├── test_recognition_runtime.py  直调识别、Windows 子进程、HTTP 回退与并发限制测试。
├── test_planning_runtime.py   规划直调/HTTP、配置、并发、异常与确认入库测试。
├── test_wechat_media.py       微信附件鉴权、导入幂等、失败及候选入库测试。
├── test_text_contract.py     文本识别契约测试。
├── docs/email-capacity.md    邮件容量、资源边界、恢复与监控操作说明。
├── docs/archive/email-capacity-30-users-2026-10-20.json 30 账号合成持续负载结果。
├── docs/archive/email-capacity-stress-2026-10-20.json   短生成合成压力结果。
├── docs/archive/email-cleanup-capacity-2026-10-20.json  模块拆分后的合成负载结果。
├── docs/archive/2026-10-01-issue69-direct-planning.json  Windows 无独立识别/规划服务的合成实测。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

邮件导入并发由环境私有 `server/deploy/environments/<环境>/config.json` 的 `email_concurrency` 控制，省略时按 `2` 处理，可设为整数 `1` 至 `4`。统一环境入口将 profile 值传给本机 API 和 Compose API 的 `NIUMA_EMAIL_CONCURRENCY`，不会读取终端中同名设置。邮件作业队列最多运行 8 个 worker，邮件与其他入口仍共用最多 20 个模型调用名额；作业排队、模型排队、生成、校验和整段识别耗时各自描述不同但可能重叠的阶段，不能相加成总工作量。受限原因码和聚合诊断只向授权管理员提供并保留 90 天，不记录邮件正文、标题、身份或凭据。成功结果使用缓存；停止作业阻止后续项目启动，撤销/移除邮箱连接会阻止后续处理，在途远端请求可能完成。见[邮件导入约定](../contracts/docs/email-import.md)。

## 错误报告

`POST /api/error-reports` 接收用户点击提交的受限报告，沿用 [error-report/0.1](../contracts/docs/error-reports-v0.1.md)；允许未登录反馈登录故障，限制请求大小和频率、按问题编号去重，不收集原始异常或私人正文。现有管理员后台按收到日期展示最近 100 条报告，保留 90 天，普通用户无列表读取权限。

## 使用数据后台

- 后台位于 `/admin.html`，普通应用导航不显示入口，统计 API 仅允许服务器授予权限的已登录用户。
- 后台登录使用独立 `/api/admin` Cookie，登录或退出后台不切换普通页面账号，避免跨标签页混入数据。
- 部署人执行 `docker exec niuma-production-api-1 python server/usage.py --grant <用户ID> --db /data/niuma.sqlite3` 授权，将 `--grant` 改为 `--revoke` 撤销，角色随环境数据卷持久化，不通过注册、昵称或客户端字段授权。
- 本机直启可用 `NIUMA_ADMIN_USER_IDS` 空格分隔用户 ID，管理员操作默认从统计中排除；不要将管理员配置放入模型 `.env`。
- `NIUMA_ANALYTICS_EXCLUDED_USER_IDS` 是空格分隔的测试账号 ID，设置后统计与反馈列表排除这些账号。
- 记录注册/登录、来源同步、识别、拆解、规划生成/采纳和任务新增/编辑/完成，保存匿名标识、次数、结果和请求总耗时，不保存任务正文、邮件账号、凭据、附件或 IP。
- 请求总耗时含排队，不作为纯模型耗时；识别返回 HTTP 200 但业务失败计为失败，分页读取不计功能调用。
- 后台同步在实际终结时记录一轮结果，逐条识别和自动入库随提交事务记录；重复查询/重试原操作不重复计数，部分失败、停止与中断不计整轮成功，后台耗时从创建至终结（含等待）。
- 中位耗时使用常规样本中位数，偶数样本取中间两项均值；P95 使用最近秩 `ceil(0.95×n)`，不足20项时可能等于最大值。
- 任务按时完成按本次服务端完成变更与 DDL 比较，仅统计启用后的有 DDL 完成事件，不补造旧数据或实际线下完成时刻。
- 行为明细和反馈在记录新事件时清理90天之前的数据，后台日期范围最多90天，使用数据与各环境数据库一同隔离、备份。
- 主动反馈允许用户输入最多2000字，后台只展示反馈内容和评分，不展示用户账号；用户自行填写的反馈不作为匿名文本保证。
- 连续两个北京时间自然日使用时原子领取一次评分提示，关闭不再提醒，刷新、多窗口及后续日期不重复领取。
- 第一版展示活跃趋势、功能使用/成功率/请求耗时、任务变更计数、按时完成计数及反馈评分；不以独立事件数量冒充同批任务转化漏斗、模型准确率、工时偏差或逐用户追踪。

### 邮件来源遥测

邮件流程沿用同一 `usage_events` 表和管理员聚合接口，不创建单独的平台或内容明细。后端 `email_telemetry.record(path, uid, stage, duration_ms=0, success=True, quantity=1)` 可在邮件业务事务结束后写入 `email_<stage>` 事件；`email_telemetry.measure(path, uid, stage)` 是记录执行耗时、正常退出成功状态并在异常退出记失败后重新抛出原异常的上下文管理器。仅接受 `headers`、`queue`、`fetch`、`parse`、`recognition`、`model_queue`、`generation`、`validation`、`job`、`cache_hit`、`excluded`、`failed` 阶段。写入使用独立 SQLite 连接，锁等待最多约 250 毫秒；失败会丢弃遥测记录，不影响邮件业务。不要在持有业务写事务时调用，避免争抢 SQLite 写锁。

遥测只保存现有匿名用户哈希、阶段、耗时、结果和数量，不记录邮件地址、主题、发件人、正文、`input_id`、`request_id` 或异常文本。数量用于汇总命中、排除及失败条目；每条事件数仍表示遥测记录数。所有指标自动复用管理员账号/排除账号过滤、中位数、最近秩 P95 和 90 天保留。整项 `job` 耗时应覆盖创建到结束并包含排队；`queue` 只在能单独测量真实排队时记录。`recognition` 是包含模型准入排队、生成、校验及进程开销的总耗时，不应与组成阶段相加。被强制终止且未返回测量的子进程不填充虚假的零耗时或成功记录；其耗时仍包含在识别总耗时中。不要同时把同一封邮件流程再计入通用同步或识别事件。

后台教学网同步接口见 [source-sync/0.1](../contracts/docs/source-sync-v0.1.md)：每轮最多 4 条识别，全服务最多 4 轮活动同步，共用原模型队列；采集和模型期间不持有数据库写锁。刷新不影响执行，停止拒绝迟到写入，服务重启保留结果并标记中断。重试可选 request_ids，服务端按最新尝试、已保存结果及来源有效性筛选，已恢复或提交的旧记录不重跑；相同操作编号不能更换选择。运行 `python -m unittest discover -s server -p test_source_jobs.py -v` 使用临时数据库和合成采集/模型验证。

同步终态压缩为有效结果：保留新增、待核对、任务更新与错误，移除无任务 / 过滤成功明细；没有有效结果的运行从历史列表省略，只保留 24 小时的精简操作回执用于网络重试，随后随启动、查询或调度清理。最近检查每账号只存一份，来源指纹、识别缓存、候选决定和任务不随回执清理；旧历史在初始化时按同一规则压缩。历史列表在当前账号及凭据范围内，按稳定来源身份与错误类型只展示最新相同异常；已恢复异常隐藏，混合运行保留实际新增、待核对及更新。先合并再截取最近 20 条，原始运行与恢复 / 幂等回执保留，查询列表不改工作区或来源数据。异常统一处理通过 `/api/sources/sync-runs/{id}/recover` 按原运行的异常身份限定来源注册、附件存储和识别范围；纯识别重试沿用 retry。成功项、正常待核对项和无关新内容不重放；补齐后换快照成功会精确恢复旧异常，未补齐仍保留错误。`GET /api/recognition/batches` 补充 `sourceSyncManaged` 布尔标记，从账号内完整同步回执及导入归属计算；隐藏、已恢复及不在最近 20 条可见历史中的旧批次仍归同步管理，不能在旧失败入口再次显示或重试。该查询不改识别批次、候选或工作区。每条结果保存真实任务 / 候选名称及稳定身份回执，前端可按新增 / 待核对 / 更新 / 错误数量切换紧凑名称列表并打开原详情或核对窗口；旧历史仅在身份与数量可核实时补齐名称。采集导致的任务更新在同一事务中比较用户可见字段并记录实际变化的字段名，避免把采集等待期间的手动修改误记为同步更新。

定时同步默认关闭；在对应环境 `config.json` 加 `"scheduled_sync": true` 并重启，账号在来源设置保存所选来源和 15～10080 分钟间隔后生效；只支持教学网，须使用单业务 API 进程。验收运行 `python -m unittest discover -s server -p test_source_schedule.py -v`，包括持久配置、手动撞期、失败重试及关闭时拒绝迟到写入，不发送私人数据。

开发和测试优先使用 [统一环境入口](deploy/ENVIRONMENTS.md)：仓库根运行 `python server/manage_environment.py init all`，再运行 `python server/manage_environment.py local development` 或 `local test`；识别和规划由业务 API 直接调用，不必启动 1333/1334。
原 `frontend/` 的 `npm run dev:api` 和 `npm run dev` 保留旧本地配置兼容，不代表环境已隔离；已上线安装须按迁移步骤保留原数据后接管。
Docker 公网部署使用 [部署说明](deploy/README.md)，`NIUMA_HOST` 设置容器监听地址，`NIUMA_ALLOWED_ORIGINS` 指定允许的完整来源，`NIUMA_COOKIE_SECURE=1` 仅经 HTTPS 发送会话；未设置时保留 localhost:1331 的本机行为。
Windows 整合测试在仓库根运行 `powershell -ExecutionPolicy Bypass -File server/start-local-test.ps1`；默认 test 环境为网页 1531、API 1532，数据库在 `server/data/test/`，旧测试数据库不迁移、不删除。
教学网同步使用原生 v0.4：按课程作业、课程通知、教学内容顺序识别，来源账号和快照由后端核验；明确任务自动加入，引用所需材料缺失时保留审核，详见 [运行视图](../contracts/docs/teaching-workspace-v0.1.md)。
已读部分无任务但内容不完整时标记“来源待补全”，相同快照不重复调用模型；补全正文/附件后重新同步，模型失败仍可重试。
真实 API 合成验收在仓库根运行 `.venv/Scripts/python.exe server/run_teaching_smoke.py --output local-data/teaching-smoke.json`；只发送合成内容，独立临时数据库，不访问校园网账号。
追加 `--env-file <测试模型配置文件> --reprocess` 验证旧批次显式重识别及自动入库；输出路径必须不存在，包含两次真实模型输出和最终任务结果，不启动生产服务。
原生作业识别输出逐行动 `submission_relation`，后端只在当前账号、精确原生来源及有证据的新增行动上建立永久关联：已提交作业的提交行动自动撤出候选或完成任务，独立阅读等行动保留；未知关系继续核对，不跨来源猜测。已提交后状态反转、用户决定及手动重开保护沿用 [采集状态约定](../contracts/docs/teaching-capture-v0.1.md)。旧待核对结果可通过重识别接口更新，已有人工决定不被覆盖。
系统首次自动撤下的候选可在重识别时替换并重新过滤；用户撤回忽略会持久暂停该行动的自动处理，重新采集、重启及转成任务后仍保留。
候选可通过现有操作接口恢复暂停或改关联为 other/unknown；显式恢复后不因版本较高而重新暂停，之后再次撤回忽略则重新暂停。重识别按账号内持久操作审计保护人工关联/控制，包括 unknown 和未变关联，不能只凭系统撤下标记替换候选。
撤回忽略在写入事务中直接保存暂停，不依赖中间读取；候选更新已有任务时一并转移保护，新增独立、未知或其他来源行动则将原任务关联降为 unknown，避免整项被误完成。

同一来源累计行动及历史删除标记超过 100 项时，保留识别结果并将关联范围降为 unknown，暂停自动绑定；正常识别与重识别均不会因此回滚。
教学网 DeepSeek 调用显式关闭 thinking；真实调用用原文片段编号填回证据，错误区分输出截断、JSON、字段与证据错误，不记录私人模型响应。
原生教学网可在来源管理中定向重识别已保存通知。同步时下载同源教师附件，识别时自动读取 PNG/JPEG、DOCX（含内嵌图片）及需要视觉阅读的 PDF；完整 PDF 文本层和 UTF-8 TXT 仍在采集器提取。文件内容与正文、课程名共同参与模型分类，截止日期只在附件中也能进入任务清单。
附件与读取缓存按本系统用户和教学网账号隔离，保存在当前环境 SQLite 的 `teaching_asset_files` / `teaching_asset_refs` 中；模型只接收清理后的图片或文本，不接收 Cookie、凭据、私有文件路径。切换或移除教学网账号会清除对应文件缓存，旧版本原件随快照替换清理；已保存的任务证据保留。
失败记录可直接重试，成功读取的附件会复用缓存；来源下载失败需要重新同步。文件内容改变也会产生新快照；旧记录未保存原件时先重新同步一次。单文件 5 MiB、单次暂存 100 MiB、账号文件合计 200 MiB；爬虫已标为 complete 且有正文的 PDF 直接复用文字，partial/unread 文件仍补读视觉内容。视觉 PDF 每批最多 6 页（像素/编码大小上限不变），单批渲染最多 15 秒、模型调用最多 60 秒，每批校验后持久保存断点，同一后台任务自动续读；只有模型明确报告输出截断时才缩到 3 页、1 页，普通错误不自动重试。附件批次各有 75 秒预算，最终任务识别另有 90 秒，附件总耗时不再挤占识别时间；停止/凭据变化在批次间及模型调用前检查。单份最多 128 页且转录最多 50000 字；真实超时/重启后重试从未完成页面继续，模糊、加密、容量超限及不支持的格式保留缺口，不当作完整读完。长扫描件仍可能耗时数分钟，推荐使用现有后台同步入口；同步 HTTP 调用受客户端连接时限影响。
缓存写入在短事务中比较当前版本和页数：同版本进度不倒退、完整结果不被断点覆盖，旧配置的迟到写入不覆盖已替换缓存。
本机需安装 `python -m pip install -r ddl_text/media_requirements.txt`（仓库根执行）；容器已包含这些依赖。沿用七牛云模型配置和密钥，当前服务支持 `image_url` 输入。附件读取与正文识别共用原有队列和单条 90 秒预算；暂时失败不会被标成无任务。

视觉转录补充已采集的 PDF 文本层，不覆盖后续页已有文字；补充后超出输入预算时保留原文和部分读取状态。附件模型调用在可回收进程中执行，总预算到期会终止并回收，即使 HTTP 持续缓慢返回也不会无限占用调用线程。同一暂存原件供多条通知引用时只读取和累计一次文件大小，逐条绑定与完整性检查仍保留。
合成文件端到端验收：安装 `ddl_text/media_test_requirements.txt`，然后运行 `python server/run_teaching_attachment_smoke.py --env-file <测试模型配置文件> --output <新的本地结果路径>`。覆盖真实 PDF/扫描 PDF/PNG/DOCX、采集导出、同步 API、真实模型及任务入库；不访问私人课程或校园网，结果包含输入、输出、用时和最终任务。离线回归运行 `python -m unittest discover -s server -p 'test_teaching_assets.py' -v`。
文本识别默认由业务 API 直接调用 `ddl_text.pipeline.process_request`，读取当前环境的 `.env.model` 或容器环境变量，无需启动 1333；未设置环境身份的旧本地入口仍兼容根目录 `.env`。
在启动业务 API 的终端设置 `TEXT_HARNESS_URL`（及可选 `TEXT_HARNESS_TOKEN`）时，识别改走原 HTTP 服务；连接失败不会自动切换模型或重复调用。
同一后端进程的文本、教学网、附件识别、重识别、规划及 AI 拆解合计最多 20 个模型请求执行，额外最多 100 个按到达顺序排队，等待上限 180 秒；队列满或等待超时返回 503，不开始模型调用。
同步请求排队时客户端断连会撤销等待；已经开始的模型调用不因浏览器取消而保证中止，生成预览仍不写入任务或日程；该限制为单进程共享，多进程部署须另行提供跨进程控制。
规划默认调用 `task_planning.planning_harness.generate`，同样读取当前环境的模型配置，沿用独立规划服务的七牛配置要求；未配置密钥返回 503，不用本地假排程代替模型。
显式导出 `HARNESS_URL` 时走原 HTTP 通道，仍支持 `HARNESS_TOKEN`；HTTP 失败不会转而再调用本地模型。恢复直调需清除此环境变量并重启，`.env` 中的路由变量不生效。
规划使用同一模型队列，异常后释放名额；排队不占模型执行预算，模型网络超时保持 20 秒、HTTP 等待保持 25 秒，无自动重试。
任务拆解同样读取当前环境的模型配置并进入共享队列。`POST /api/decomposition/proposals` 只生成追问或预览，复杂任务的方案须通过 `POST /api/decomposition/apply` 确认后才替换任务步骤；任务版本变化时旧方案拒绝写入；回应携带任务 ID，确认时必须提交同一任务 ID，公开格式见 [拆解契约](../contracts/task-decomposition/README.md)。
两种规划通道仍使用规划 2.0 和同一结果校验，预览只保存提案，用户确认后按账号与任务版本写日历；DDL、任务状态和已有计划不因生成预览而改变。
`POST /api/planning/proposals` 可带 `taskIds` 限定本账号有 DDL、未完成且未安排的新增任务；无匹配任务返回 400，提案仍须通过 `/api/planning/apply` 确认。
同一接口可带 `replan: true` 纳入已有安排的任务，格式见 [规划接口约定](../contracts/task-planning/README.md)；使用 `taskIds` 时只重新规划选中任务。共享规划 2.0 输入输出不变，服务端记录待替换的时段 ID，确认时原子替换有新时段的任务尚未开始的安排；无新时段、模型失败、取消预览或版本冲突保留原安排，等待期间原时段已经开始时拒绝应用。
在 `frontend/` 运行 `npm test` 执行前端测试；业务 API 在仓库根运行 `python -m unittest discover -s server -p 'test_*.py' -q`；规划模块另运行 `python -m unittest discover -s task_planning/tests -t . -q`。旧规划 API 保留，当前新版界面不提供规划入口。
完整业务测试需 Python 3.10+，`npm test` 安装共享附件运行依赖，Windows/Linux CI 均实际执行图片读取测试。
已完成任务的未来安排不占用新规划的可用时间，原计划保留在任务历史中；任务完成更新版本后拒绝应用此前生成的旧提案。
直调识别保留 90 秒整批预算，HTTP 传输保持 95 秒；前端识别、规划和拆解最多等待 280 秒，包含最长 180 秒排队，代理 330 秒等待保持不变；独立 Harness 和模型供应商的并发/额度限制仍由对应服务决定。
直调识别按记录保存 `text_policy_manifest` 的规则、实际模型配置及运行环境版本；失败记录重试只更新该记录的版本，不重标此前成功结果。
历史记录与外部 HTTP Harness 未提供的版本保持未知，不用当前规则回填；版本仅存业务数据库，不新增 v0.2 字段或自动重放旧内容。
定向重识别使用已合入的 [业务契约](../contracts/docs/reprocessing-v0.1.md)，来源目录默认恢复已有记录，显式选择最多 100 条，运行及停止状态保存在 SQLite，重启中断不自动再次调用模型。
旧记录没有跨版本的明确行动关联时，保留全部用户决定；新差异以“行动待核对”候选进入工作台，用户明确选择新增、更新或忽略，不按标题、数组位置或模型候选编号自动覆盖。
每个进程最多接纳四个活动重识别运行，其模型调用进入共享队列；等待或失败保留旧结果并允许用户显式启动新运行。
后端启动后规则文件发生变化时拒绝继续识别并提示重启，防止将旧进程代码标为新规则版本；版本清单不保存密钥或私人正文。
教学网同步最多等待 300 秒，真实账号采集实测约 2 分钟。
教学网同步需安装 `server/requirements.txt` 与 `crawler/` 依赖；用户在来源管理页保存一次教学网凭据，密码按账号加密入库，密钥位于数据库同目录的 `credential.key` 且不会提交；采集时使用权限为 0600 的临时文件并在结束后删除。
备份数据库时需同时安全备份 `credential.key`，密钥丢失后须重新保存教学网凭据。
Windows 不支持 POSIX 0600 权限；请将 `credential.key` 所在目录设为仅本人可访问。

## 教学网导入排障

“暂时无法从教学网获取通知，需要维护人员处理。你可以先粘贴通知内容。”对应前端 `crawler_dependency` 分类，通常是采集依赖缺失，并非模型识别失败。后端在启动采集前检查 `crawler/node_modules/tsx` 或 `.bin/tsx` 是否存在，缺失即返回“教学网采集依赖未安装”；前端也会将匹配的模块加载错误归入此类。仅凭页面提示不能确定具体机器或安装步骤，应先核对实际后端版本和本轮错误。

### 先确认失败阶段

1. 记录出错时间、使用的开发 / 测试 / 生产环境以及 `/api/version` 的 `revision`；点击“反馈此问题”提交错误分类与版本，维护者在 `/admin.html` 查看报告。反馈不含原始异常、课程正文或密码。
2. 已登录用户可在浏览器开发者工具的 Network 中查看本轮 `GET /api/sources/sync-runs/{id}` 响应的 `state`、`error.code` 和 `error.message`；采集失败可能是 `collection_failed`，具体原因在 message 中，不能只看 HTTP 200 或该错误码。旧版同步接口则查看 `POST /api/sources/sync` 的错误响应。
3. 向维护者提供上述有限字段即可；不要公开整份网络记录、Cookie、凭据或课程内容。“已连接”只表示凭据已保存，保存时没有验证教学网登录。

| 后端错误 / 页面现象 | 排查与处理 |
| --- | --- |
| `教学网采集依赖未安装` / 上述“需要维护人员处理”提示 | 在**实际运行后端的检出目录**安装 [crawler 依赖及 Chromium](../crawler/README.md#安装与检查)；重点检查新工作树未安装依赖、只安装 `frontend/`、`--prod` 漏掉 `tsx`、后端仍运行旧目录。 |
| `教学网采集无法启动或超时` | 检查后端进程的 Node PATH、浏览器运行条件、服务器到教学网的网络；采集子进程等待上限为 300 秒，不能仅凭此提示认定是缺依赖。 |
| `教学网采集失败` | 爬虫已启动但非正常退出；浏览器文件 / 系统库缺失、账号登录、校园网连接和页面变化都可能导致此错误。先检查浏览器安装，再检查账号与网络；当前 API 不返回爬虫 stderr，不能从这句话确定是密码错误。 |
| `教学网识别配置不可用` / “暂时无法整理教学网通知，需要维护人员处理” | 来源已经进入识别，但教学网识别的配置或规则检查未通过，尚未调用本条模型；按下方步骤检查当前环境。 |
| 单条模型输出失败 | 已尝试识别；根据该记录的错误分类排查连接、超时、输出或证据问题，不与启动配置错误混为一类。 |
| `定时同步暂不可用，可进行单次识别` | 环境未开放定时同步；独立于采集依赖故障，单次识别仍可用。见下方说明。 |

### 已取得来源但全部报“识别配置不可用”

这个错误由 `server/teaching.py` 在读取模型配置、构造 Settings 和核验识别规则时产生。安装 Python / 爬虫依赖不会填入模型密钥；`manage_environment.py check` 通过也不表示模型已就绪。

1. 先确认**实际启动 API 的工作目录和命令**。统一入口 `local development` 读取该检出的 `server/deploy/environments/development/.env.model`；`local test` 使用 `test/.env.model`。新工作树不会自动带上这些被 Git 忽略的文件，`init` 生成的模板密钥为空。不要只修改仓库根 `.env`；仅未设置环境身份和 `NIUMA_MODEL_ENV` 的旧入口才回退读取根 `.env`。
2. 当前教学网入口要求 `DDL_PROVIDER=qiniu`、有效的 `QINIU_API_KEY`、受支持的 `DDL_MODEL`，以及以 `/v1` 结尾的 HTTPS `DDL_ENDPOINT`（项目模板为 `https://api.qnaigc.com/v1`）。`DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 不能代替 `QINIU_API_KEY`。模型文件只保留模板支持的字段，避免重复键、`export` 前缀、未知字段或引号不配对；不要把完整终端环境导出粘入此文件。
3. 使用与启动 API 相同的 Python，在仓库根执行下方**统一本机入口**自检（测试环境将路径中的 `development` 改成 `test`）。它在短时子进程里排除继承的模型变量，模拟统一入口读取指定文件；只输出检查布尔值，不显示密钥，也不请求模型。任何异常或 `False` 都须先处理。配置文件可读且语法有效不代表 key 有额度、模型可用或网络连通。

```bash
python -c "import os,sys; from pathlib import Path; from ddl_text.codex_client import ENV_KEYS,live_options; p=Path(sys.argv[1]); print('model_file_exists:',p.is_file()); [os.environ.pop(k,None) for k in ENV_KEYS]; o=live_options(p); print('provider_is_qiniu:',o['provider']=='qiniu'); print('key_present:',bool(o['key'].strip())); print('endpoint_syntax_valid: True')" server/deploy/environments/development/.env.model
```

4. 配置修正后停止原 API，再按同一环境重新启动；AI 或开发者修改了识别 skill / 契约 / 规则文件却保留旧 Python 进程，也会因规则版本不一致触发这个提示，需要重启后端。对于旧入口，终端中继承的模型变量（包括空值）优先于文件；改用统一入口，或核对其实际继承配置。
5. 修正配置并重启后，重新“开始识别”。配置错误在当前版本属于不可直接重试的失败，不要依赖“重试本条”按钮；普通同步会重新处理失败来源，并按增量规则复用已成功的未变更来源，无需清空账号。仍失败时仅向维护者提供 `/api/version`、启动命令、上述布尔结果，以及一个失败记录的 `records[].error.code/message`，不要发送整份配置、HAR 或网络响应。

容器使用 Compose 注入的模型变量，不能用上面清除变量的命令检查容器；由部署者核对该环境的 `.env.model` 和当前 API 镜像，配置变更后按统一入口重建对应容器，单纯重启旧容器不会重新读取 Compose 的 env_file。若自检配置正确且后端已重启，仍需核对部署文件和规则版本，不能据此断定是 API key 故障。

### 本机与容器分别处理

本机按 [crawler 安装与检查](../crawler/README.md#安装与检查)执行安装和无网络自检，Python 依赖另见[团队启动指南](deploy/TEAM_GUIDE.md)。`manage_environment.py check` 只检查环境配置，不验证爬虫依赖、浏览器或登录；运行成功不等于教学网可用。安装后沿用当前环境重启必要服务，再用“重新获取来源”或“开始识别”进行一次同步；仅刷新页面或重试已保存内容不会补装依赖。

服务器由维护者在部署工作目录运行 `python3 server/manage_environment.py status test`（生产用 `production`）确认当前容器，再用 `docker exec -it <API容器ID> sh` 进入 **API 容器**，切到 `/app/crawler` 执行上述无网络自检，结束后 `exit`。只使用线上网页的用户无需在自己的电脑安装 Node、pnpm 或浏览器。

仓库 [Dockerfile](deploy/Dockerfile) 已在 API 镜像中安装 Node、完整爬虫依赖、Chromium 和 Linux 系统库；容器仍缺依赖时核对是否运行旧 / 自建镜像，以及额外目录挂载是否遮住 `/app/crawler` 或 `node_modules`。在宿主机安装依赖或只更新网页镜像不会修复 API 容器；按[环境部署流程](deploy/ENVIRONMENTS.md)构建并验收测试镜像后再发布，保留原数据卷，不用删库或 `down -v` 排障。

定时同步由当前环境 `server/deploy/environments/<环境>/config.json` 中的 `"scheduled_sync": true` 开放，统一入口注入 `NIUMA_ENABLE_SCHEDULED_SYNC=1`；修改后重启本机服务或通过环境入口重建对应容器，再核对 `/api/version` 的 `scheduled_sync_available`。账号还须自行启用并选择周期；打开此开关不会安装依赖或修复采集错误，详见[后台同步约定](../contracts/docs/source-sync-v0.1.md)。

## 微信材料处理边界

Docker 部署禁用 `/api/sources/wechat/`；`NIUMA_ENABLE_MANUAL_MATERIALS=1` 仅开放 `/api/materials/` 登录会话入口，允许用户主动提交未加密 JSON 和附件，不获取微信内容、不解密、不恢复旧采集任务。
主动上传回执使用业务版本 `manual-materials/0.1`，识别请求和结果沿用共享 v0.3.1；原件类型、大小和哈希由服务器核验，未支持格式明确标为无法读取。
材料列表额外返回每项 `retry_remaining`（最多 3 次重试）；用尽后保留结果，界面提示检查原因并仅重新上传失败文件。

本业务 API 接收材料并进行账号鉴权、存储、识别调用和候选管理，不启动或登录微信客户端，不读取微信聊天数据库、不提取客户端密钥；独立来源采集模块为 `wechat/`，模型识别模块为 `ddl_text/`。
业务导入凭证仅授权本项目接口，不证明上传材料的来源合法、第三方已同意或腾讯已许可相应采集方式。
开发与演示默认使用合成材料，不使用维护者或用户的个人微信实测；未经确认的真实采集方式不得因“本地运行”“仅供学习”或免责声明而启用。
只处理有权提供且满足必要授权和平台规则的材料；识别内容会发送至配置的模型服务，不应上传无关的私人会话、联系人、账号凭据或敏感附件。
来源删除仅作用于本服务的托管存储，已确认任务仍保留，无法据此保证撤回第三方模型服务已接收的内容。
模块分工说明技术边界，不替代对实际行为的判断，也不构成合规认证、腾讯授权或任何参与者的免责保证；平台规则见[官方微信协议入口](https://weixin.qq.com/cgi-bin/readtemplate?t=weixin_agreement&s=default&lang=zh_CN)。

微信附件由外部提供方使用账号来源设置中签发的一次显示凭证调用 `/api/sources/wechat/assets` 上传原件，再调用 `/api/sources/wechat/imports` 提交 v0.3.1 JSON；请求、回执、结果和查询均使用新版结构及语义校验，真实来源接入另须核对授权与平台规则。
上传只接受一个 `file` 文件 part，拒绝额外文字字段和多文件；回执的类型、长度与哈希由原件计算。
附件接口接收请求体最多等待 30 秒，拒绝非法或重复长度、分块传输及不完整 multipart；JSON/字段/数量超限返回 413，格式错误返回 400，均使用正式错误对象。
来源凭据查询返回当前文件大小、账号配额、队列容量与未引用保留时间，前端在“附件限制与保留”中折叠展示。
附件候选保存到账号候选库，文件模型证据须经确认后入库；忽略、确认与刷新状态持久化，重试保留已有候选及用户决定。
相同 request_id 的采集时间或引用变化返回 409；每次 retry_id 及其选定记录持久化，历史重放返回 200，识别请求级失败保留此前的部分成功结果。
同账号的内容缓存包含消息身份、上下文、原始发布时间、附件哈希、读取器代码/规则和模型配置；采集时间、请求编号和重签句柄不触发重识别，新请求保留自己的回执/查询身份并复用原候选处理状态。
PDF/DOCX 读取器代码、限制及实际 PDFium、Pillow、defusedxml 版本进入缓存指纹；依赖未安装时不复用语义缓存。
原件保存在数据库同目录的 `wechat-assets/`，不公开文件 URL；每次读取重新核验账号、有效句柄、存储位置、长度和 SHA-256，并限制读取时间与字节数。
未引用原件默认保留 24 小时，可用 `WECHAT_UNREFERENCED_TTL_SECONDS` 调整（最短 300 秒）；启动、上传、导入或查询时清理过期原件，作业引用的原件保留用于查询和重试。
登录用户通过来源设置确认调用 `POST /api/sources/wechat/remove` 删除原件、导入记录和候选，已确认任务保留；凭证及句柄立即失效，处理中迟到结果不重新写入，账号删除后的遗留内容在启动时清理。
被占用原件保存到持久化删除队列，在启动或后续上传/导入/查询时重试清理；接口用 `cleanup_pending` 如实报告剩余原件数量，待删字节继续计入 200 MiB 账号配额。
`DELETE /api/sources/wechat/assets/{file_ref}` 返回无响应体的 204，仅原账号可幂等撤销自己的句柄；撤销后导入和新重试返回 404，重新上传签发新句柄，不复活旧引用。
图片识别调用 `ddl_text.media_pipeline.process_media_request`；使用 Python 3.10+ 安装 `ddl_text/media_requirements.txt`，模型配置沿用当前环境的模型配置，须选用支持图片的七牛模型。
附件处理最多 20 个后台作业执行，每个数据库最多接纳 120 个未完成作业；模型阶段与其他功能共用 20 并发队列，读取器的解码、记录和批次预算不变；支持范围及 PDF/DOCX 的覆盖限制以 `ddl_text/docs/image-input.md` 为准。
附件读取结束或异常均关闭 SQLite 连接，测试先等待本数据库后台作业退出再删除临时目录，不遗留 Windows 文件占用。
附件作业逐记录保存执行标记与校验后的结果，查询返回真实已处理数量；每次执行共享 180 秒批次预算，单记录最多 70 秒。
重启复用已保存结果并继续未开始记录；请求已发出但结果未保存的记录报告中断，用户定向重试，避免自动重复模型请求。
恢复批次不写入当前版本内容缓存，避免把旧部署的部分结果标成新规则结果；全部结果已保存时无需模型配置即可完成恢复。
部分结果已保存但模型配置不可用时保留已完成记录，其余记录返回可定向重试的失败，不重复调用已完成记录。
单条适配器异常或结果校验失败保存为该记录的失败，不遮住同批已完成候选；数据库检查点异常仍停止执行。
首次导入缺少模型配置时保存各记录的可重试失败；配置完成后沿用原作业定向重试，不必重新采集。
保存单条结果前按正式契约检查整批容量并预留剩余记录的失败说明，超限记录提示拆分导入，保留此前候选，不截断任务或证据。
结果入库前在同一事务重新检查原件句柄；运行中撤销的记录报告 `file_ref_unavailable`，不发布新候选，其他有效记录及此前已保存结果保留。
启动时回收写文件后尚未提交回执的托管原件；被占用的残留进入持久化删除队列并计入配额，不扫描链接目录或删除非托管文件名。
业务 API 先成功绑定端口再恢复队列，端口占用退出不改动运行中作业；模拟账号 seed 只初始化数据，不恢复或重复执行微信作业。
导入和重试先提交数据库并启动作业再发送回执，客户端断开不阻止处理；丢失回执时重放原编号查询同一作业，不重复调用。
来源映射保留主消息的附件文件名，不被后续消息引用覆盖；证据标签使用候选自身经校验的来源。

教学网采集后按应用账号、教学网账号及通知 ID 保存内容版本；只向模型发送新增、修改、未处理或识别失败的记录，忽略采集时间变化，首次启用增量索引仍处理历史通知。
每条通知生成独立 v0.2 请求并保留首次采集时间作为重试的时间基准；成功（含无任务）且没有待自动创建的任务时跳过。识别成功后若页面关闭或创建失败，再次同步复用已保存的候选补建任务，不重复调用模型；已加入、已忽略、撤回后待核对及有疑点的候选不自动补建。修改后生成新候选，不自动改动已确认任务。
`POST /api/sources/sync` 返回 `requests`（单条记录的 v0.2 请求列表）及 `summary`（total / skipped / pending），并附带 `updated_tasks` 表示采集事务实际修改的已有任务；爬虫输出契约不变。

### 北大邮箱来源

仅支持 `@stu.pku.edu.cn` 与 `@pku.edu.cn`。按域名使用固定 IMAP TLS 主机，只读打开收件箱并使用 `BODY.PEEK`。默认列出最新 50 封标题元数据，不再限制 30 天；可指定最近 N 封或起始日期，并按页预览。正文需当前应用账号及邮箱的持久同意；可单独排除敏感邮件，批量模式同样尊重排除项，附件不下载。候选始终由用户核对，不自动创建任务。

凭据仍由 `source_sync.py` 加密保存；连接表与持久偏好、排除项、任务运行记录隔离于其他应用账号。`email_import.py` 负责后台任务、失败重试、缓存投影和重启中断恢复，沿用 `recognition_batches`、候选存储及全局模型队列。成功的有任务/无任务结果不会因刷新、再次导入、忽略候选或服务重启而再次读取正文或调用模型；旧邮箱增量索引中的成功结果也直接复用。移除连接不删除已生成候选或用户任务。

接口、状态和手工验收说明见 [邮件导入](../contracts/docs/email-import.md)。旧 `POST /api/sources/sync` 邮箱分支返回 410，避免绕过持久授权及排除项。逐项说明见[邮件测试目录](EMAIL_TESTS.md)。离线验证：`python -m unittest discover -s server -p 'test_email*.py' -v`；全部使用临时数据、模拟 IMAP 和模型，不能代替真实邮箱验收。

`POST /api/recognition/retry` 接收原 v0.2 请求，仅重新识别失败记录；保留成功候选及确认状态，成功请求保持幂等，原 `/batches` 重放行为不变。
业务 API 直接使用模型侧校验过的 v0.2 时间字段；无疑点的外部新增行动可自动入库，无日期任务保留空 DDL 并在工作台补充，需核实及手动输入的候选仍待用户确认。
重试由用户点击或再次同步触发，不在同次同步内无限重试；采集本身的超时独立于每条识别的 90 秒预算。

教学网原生 v0.4 使用 model-v1 分类：task 自动新增，candidate 等待核实，filtered 留存依据，no_tasks 与 incomplete 由模型区分；不在展示或入库时重算正文完整性/补年/历史审核。账号快照、提交状态关联和既有任务操作保护仍生效；旧成功批次通过来源管理显式重新识别，普通 retry 仍返回旧结果，详见 [识别入口](../ddl_text/docs/teaching-draft.md)。

重识别目录同时支持旧 v0.2 与当前校园账号的 v0.4，使用独立来源 ID；新增 `reprocessing_teaching_sources`、`reprocessing_request_sources` 表由启动初始化，无需改写旧数据。v0.4 新候选和自动任务在同一事务保存，失败/中断保留旧结果，用户决定按账号及来源版本关联保留；部署前备份数据库，回滚不得删除已有任务或新表。
教学网重识别保留原始采集正文，并在同一事务迁移附件绑定；读取结果只写入识别批次，连续重识别、正常同步清理和重启仍可读取原件，换号或移除凭据才清理对应附件。
启动时自动创建按账号、请求编号查询来源及按账号、文件摘要查询附件引用的索引，减少同步保存和附件清理的重复扫描；已有数据库无需手动迁移，不修改原件、读取缓存或账号隔离规则。
在仓库根目录运行 `python server/benchmark_teaching_store.py`，测量内存库中 1000 份合成附件的 70 次清理（3 轮取中位数）；加 `--without-indexes` 仅在该临时内存库移除新索引以比较基线。基准不读取用户数据库，不代表整轮同步耗时。

提交控制路由见 [submission-operations/0.1](../contracts/docs/submission-operations-v0.1.md)：原生状态只影响已精确关联的提交行动，独立行动、删除/忽略和手改内容保留。每次同步在增量缓存前处理状态；正文或附件摘要变化会解除整份来源确认，新行动不按标题猜测关联；控制变化时拒绝迟到模型结果。原文条件读取在同一事务中核对快照与控制版本，变化时返回 409；自动完成日期使用北京时间，审计保留 UTC。完整来源无法提供时不接受整份 `submission_only` 确认，逐项控制仍可用。

公开活动路由见 [public-import/0.1](../contracts/docs/public-import-v0.1.md)：仅开放三个现有网站，不接受任意 URL；最多 2 轮公开采集并行、每账号 1 轮，每轮最多 6 页/150 秒。预览不建任务，用户选中后按任务版本原子写入通知；重复导入不复制、不复活回收站记录。

接线回归：`python -m unittest server.test_submission_runtime server.test_public_sources server.test_source_jobs server.test_source_schedule server.test_teaching_reprocessing server.test_manual_materials -q`（`PYTHONPATH` 包含仓库根及 `server`）；浏览器完整入口验收按 [前端说明](../frontend/README.md) 执行，合成外部服务与真实模型联调须分别记录。

### 单封邮件模型响应诊断（测试环境）

`python3 -m server.diagnose_email_response --live --request-id <已保存的单封邮件请求编号> --output /tmp/email-response-diagnostic.json`

此命令从 `server/data/test/niuma.sqlite3` 只读加载正文快照，仅使用 `server/deploy/environments/test/.env.model`，忽略外部 shell 模型凭据。`--live` 会把该封已保存邮件再次发送至测试配置的 Qiniu 服务一次，应在明确授权该次发送后运行。不会访问 IMAP、更新识别缓存或创建任务。使用与邮件识别相同的模型参数、提示、8192 输出 token 上限及默认 270 秒超时（可用 `--timeout-seconds` 调整，最多 300 秒），不自动重试。

报告文件必须不存在，以 0600 权限创建；只保存响应字节数、JSON 外层有效性、选择数量、完成原因、正文及推理文本长度、token 用量、固定错误码、校验是否到达及阶段耗时，不记录邮件、模型正文、推理文本、密钥或 provider 错误正文。`output_truncated` 表示输出达到 token 上限，`provider_response` 表示外层响应不完整或不支持；进入校验后可区分 JSON、schema、evidence 等原因。退出码 1 同时用于诊断捕获失败及设置失败，应结合报告 `status` 判断。新的回放只能诊断本次响应，不能恢复历史模型原始响应。

离线回归：`python3 -m unittest ddl_text.tests.test_email_response_diagnostic ddl_text.tests.test_chat_client ddl_text.tests.test_validation_diagnostics -q`。

### 邮件模型 Token 遥测

正常邮箱识别的每次模型调用会把提供方报告的 `prompt_tokens`、`completion_tokens`、`total_tokens` 和 `reasoning_tokens`，以及固定完成原因、模型响应成功状态和时间写入 `email_model_token_usage`。包含被截断或后续校验失败的调用；缓存命中不新增调用记录。缺失字段为 NULL，不以 0 代替，也不估算历史用量。推理 token 属于输出 token，不能与输出重复相加。此处响应成功不等于任务识别结果通过校验。

数据沿用 90 天留存、账号哈希、管理员及分析排除账号过滤；遥测写入失败不影响识别。仅保存计数及固定枚举，不保存正文、模型回答或推理文本。旧数据库在首次遥测写入时创建新表；读取管理报表不做数据库变更。

管理后台 `/api/admin/usage` 的 `emailTokens` 提供调用数、响应失败数、长度截断数，以及每类计数的累计值和有报告值的调用数；界面同时展示平均值及覆盖率。仅从新代码启用后统计。Codex/未提供 usage 的响应保留未知值；HTTP 失败、被终止且未返回 usage 的调用不能据此估算实际计费。

Qiniu DeepSeek 邮箱识别现在显式发送 `thinking: {"type":"disabled"}`，保留 8192 输出 token 上限，邮箱识别及诊断默认等待上限为 270 秒。设置由识别、当前缓存规则版本及单封诊断共用，避免新结果被误标为旧版；不自动重跑缓存。此关闭思考开关只用于 Qiniu DeepSeek 邮箱调用，不改变手动文本或其他提供方/模型的思考模式。合成 A/B 验收运行 `python -m ddl_text.tests.run_email_thinking_smoke --live --output /tmp/email-thinking.json`，只使用测试环境模型配置，对 4 个合成邮箱场景分别调用默认和关闭思考模式，不读取邮箱或写入任务。

邮件后台识别的 provider 调用及整次模型执行预算均为 270 秒（排队等待独立计算），可选 Harness HTTP 传输等待为 275 秒；手动文本等非邮箱直调继续使用 90 / 95 秒。独立部署的 Harness 自身预算需由该服务配置，调用方等待增加不会自动改变远端上限。

邮箱 Qiniu 模型首次调用使用 8192 输出 token；仅提供方明确返回 `finish_reason=length` 时，自动以 16384 输出 token 重试一次。不是扩大输入上下文。第二次仍失败则结束；超时、证据或 JSON 校验失败不触发此升级。两次调用共用每封邮件的 270 秒模型执行预算，并在下一次调用前重查停止、授权与排除状态；每次调用的 token 和耗时分别记录。手动文本默认不启用该策略。单封诊断工具仍遵守一次 replay 只调用一次的约定。

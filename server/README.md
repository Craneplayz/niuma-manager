```text
server/
├── README.md                 本目录的文件说明与启动命令。
├── .gitignore               排除本地凭据加密密钥。
├── app.py                    账号、工作区与规划的本地 HTTP API。
├── email_verification.py     邮箱验证码、一次性消费、持久限流及已有账号验证状态。
├── mail_delivery.py          阿里云邮件推送 TLS SMTP 适配，不记录凭据或验证码。
├── test_email_verification.py 注册、补验证、重放、并发、限流和旧数据保留回归。
├── test_mail_delivery.py     SMTP TLS、凭据隔离、地址校验与错误脱敏测试。
├── test_mail_environment.py  三环境邮件配置隔离、缺省兼容及 Compose 字面量测试。
├── auth_test_support.py      仅用于测试和合成验收的验证码投递替身与旧账号夹具。
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
├── source_sync.py            调用教学网采集并保存 v0.4 来源快照。
├── source_jobs.py            教学网后台同步、逐条持久进度、受控并发与自动入库。
├── source_schedule.py        账号定时配置、服务端调度、凭据变化暂停及环境开关。
├── test_source_schedule.py   定时恢复、互斥派发、关闭及迟到结果保护测试。
├── test_source_jobs.py       HTTP 同步幂等、4 路并发、停止、账号变化与重启回归。
├── teaching.py               核验当前账号快照、调用 v0.4 并适配任务/通知卡片。
├── teaching_assets.py        暂存收据校验、账号隔离原件、读取缓存与证据补全。
├── test_teaching_assets.py   文件同步到自动入库、重试、缓存、变更与隔离回归。
├── run_teaching_attachment_smoke.py  合成 PDF/扫描 PDF/图片/DOCX 的真实 API 验收。
├── submission_runtime.py    提交状态永久行动关联、完成/重开、持久幂等与原子更新。
├── test_submission_runtime.py 提交生命周期、独立行动、手动重开及陈旧操作回归。
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
│   ├── EMAIL.md               阿里云发信准备、配置、验收与旧账号升级说明。
│   └── environments/         本机私有环境配置，不提交也不进入镜像。
├── test_source_sync.py       教学网同步权限与请求转换测试。
├── test_incremental_recognition.py  增量筛选、逐条识别与失败重试回归。
├── test_recognition_runtime.py  直调识别、Windows 子进程、HTTP 回退与并发限制测试。
├── test_planning_runtime.py   规划直调/HTTP、配置、并发、异常与确认入库测试。
├── test_wechat_media.py       微信附件鉴权、导入幂等、失败及候选入库测试。
├── test_text_contract.py     文本识别契约测试。
├── docs/archive/2026-10-01-issue69-direct-planning.json  Windows 无独立识别/规划服务的合成实测。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

## 邮箱注册与验证

新账号创建前必须通过邮箱验证码；旧账号保持原有登录和数据权限，在账号入口补验证当前邮箱。本次不提供找回密码或邮箱换绑；接口见 [email-auth/0.1](../contracts/docs/email-auth-v0.1.md)，阿里云发信及环境配置见 [邮箱服务部署](deploy/EMAIL.md)。未配置邮件时发码返回 503，不提供跳过验证的注册模式。

运行 `python -m unittest discover -s server -p 'test_email_verification.py' -v` 与 `python -m unittest discover -s server -p 'test_mail*.py' -v` 使用临时数据库和合成邮件，不会真实发信。

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

后台教学网同步接口见 [source-sync/0.1](../contracts/docs/source-sync-v0.1.md)：每轮最多 4 条识别，全服务最多 4 轮活动同步，共用原模型队列；采集和模型期间不持有数据库写锁。刷新不影响执行，停止拒绝迟到写入，服务重启保留结果并标记中断。重试可选 request_ids，服务端按最新尝试、已保存结果及来源有效性筛选，已恢复或提交的旧记录不重跑；相同操作编号不能更换选择。运行 `python -m unittest discover -s server -p test_source_jobs.py -v` 使用临时数据库和合成采集/模型验证。

定时同步默认关闭；在对应环境 `config.json` 加 `"scheduled_sync": true` 并重启，账号在来源设置保存所选来源和 15～10080 分钟间隔后生效；只支持教学网，须使用单业务 API 进程。验收运行 `python -m unittest discover -s server -p test_source_schedule.py -v`，包括持久配置、手动撞期、失败重试及关闭时拒绝迟到写入，不发送私人数据。

开发和测试优先使用 [统一环境入口](deploy/ENVIRONMENTS.md)：仓库根运行 `python server/manage_environment.py init all`，再运行 `python server/manage_environment.py local development` 或 `local test`；识别和规划由业务 API 直接调用，不必启动 1333/1334。
原 `frontend/` 的 `npm run dev:api` 和 `npm run dev` 保留旧本地配置兼容，不代表环境已隔离；已上线安装须按迁移步骤保留原数据后接管。
Docker 公网部署使用 [部署说明](deploy/README.md)，`NIUMA_HOST` 设置容器监听地址，`NIUMA_ALLOWED_ORIGINS` 指定允许的完整来源，`NIUMA_COOKIE_SECURE=1` 仅经 HTTPS 发送会话；未设置时保留 localhost:1331 的本机行为。
Windows 整合测试在仓库根运行 `powershell -ExecutionPolicy Bypass -File server/start-local-test.ps1`；默认 test 环境为网页 1531、API 1532，数据库在 `server/data/test/`，旧测试数据库不迁移、不删除。
教学网同步使用原生 v0.4：按课程作业、课程通知、教学内容顺序识别，来源账号和快照由后端核验；明确任务自动加入，引用所需材料缺失时保留审核，详见 [运行视图](../contracts/docs/teaching-workspace-v0.1.md)。
已读部分无任务但内容不完整时标记“来源待补全”，相同快照不重复调用模型；补全正文/附件后重新同步，模型失败仍可重试。
真实 API 合成验收在仓库根运行 `.venv/Scripts/python.exe server/run_teaching_smoke.py --output local-data/teaching-smoke.json`；只发送合成内容，独立临时数据库，不访问校园网账号。
追加 `--env-file <测试模型配置文件> --reprocess` 验证旧批次显式重识别及自动入库；输出路径必须不存在，包含两次真实模型输出和最终任务结果，不启动生产服务。
原生 submitted 来源尚无永久行动映射时继续识别并保留候选，提交范围待核对，不能整条跳过或自动入库；submitted 后状态变化会持久暂停模型及自动入库，再次 submitted/重启不解除，已有候选仍可人工处理。逐行动映射、手动重开暂停、恢复来源自动完成与显式解除核对已接入导入页，边界见 [采集状态约定](../contracts/docs/teaching-capture-v0.1.md)。
教学网 DeepSeek 调用显式关闭 thinking；真实调用用原文片段编号填回证据，错误区分输出截断、JSON、字段与证据错误，不记录私人模型响应。
原生教学网可在来源管理中定向重识别已保存通知。同步时下载同源教师附件，识别时自动读取 PNG/JPEG、DOCX（含内嵌图片）及需要视觉阅读的 PDF；完整 PDF 文本层和 UTF-8 TXT 仍在采集器提取。文件内容与正文、课程名共同参与模型分类，截止日期只在附件中也能进入任务清单。
附件与读取缓存按本系统用户和教学网账号隔离，保存在当前环境 SQLite 的 `teaching_asset_files` / `teaching_asset_refs` 中；模型只接收清理后的图片或文本，不接收 Cookie、凭据、私有文件路径。切换或移除教学网账号会清除对应文件缓存，旧版本原件随快照替换清理；已保存的任务证据保留。
失败记录可直接重试，成功读取的附件会复用缓存；来源下载失败需要重新同步。文件内容改变也会产生新快照；旧记录未保存原件时先重新同步一次。单文件 5 MiB、单次暂存 100 MiB、账号文件合计 200 MiB；视觉 PDF 每份最多读取前 6 页，超页、模糊、加密、动态内容及不支持的格式保留读取缺口，由模型判断是否影响任务，不能当作已完整读完。
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
`POST /api/sources/sync` 返回 `requests`（单条记录的 v0.2 请求列表）及 `summary`（total / skipped / pending）；爬虫输出契约不变。
`POST /api/recognition/retry` 接收原 v0.2 请求，仅重新识别失败记录；保留成功候选及确认状态，成功请求保持幂等，原 `/batches` 重放行为不变。
业务 API 直接使用模型侧校验过的 v0.2 时间字段；无疑点的外部新增行动可自动入库，无日期任务保留空 DDL 并在工作台补充，需核实及手动输入的候选仍待用户确认。
重试由用户点击或再次同步触发，不在同次同步内无限重试；采集本身的超时独立于每条识别的 90 秒预算。

教学网原生 v0.4 使用 model-v1 分类：task 自动新增，candidate 等待核实，filtered 留存依据，no_tasks 与 incomplete 由模型区分；不在展示或入库时重算正文完整性/补年/历史审核。账号快照、提交状态关联和既有任务操作保护仍生效；旧成功批次通过来源管理显式重新识别，普通 retry 仍返回旧结果，详见 [识别入口](../ddl_text/docs/teaching-draft.md)。

重识别目录同时支持旧 v0.2 与当前校园账号的 v0.4，使用独立来源 ID；新增 `reprocessing_teaching_sources`、`reprocessing_request_sources` 表由启动初始化，无需改写旧数据。v0.4 新候选和自动任务在同一事务保存，失败/中断保留旧结果，用户决定按账号及来源版本关联保留；部署前备份数据库，回滚不得删除已有任务或新表。
教学网重识别保留原始采集正文，并在同一事务迁移附件绑定；读取结果只写入识别批次，连续重识别、正常同步清理和重启仍可读取原件，换号或移除凭据才清理对应附件。

提交控制路由见 [submission-operations/0.1](../contracts/docs/submission-operations-v0.1.md)：原生状态只影响已精确关联的提交行动，独立行动、删除/忽略和手改内容保留。每次同步在增量缓存前处理状态；正文或附件摘要变化会解除整份来源确认，新行动不按标题猜测关联；控制变化时拒绝迟到模型结果。原文条件读取在同一事务中核对快照与控制版本，变化时返回 409；自动完成日期使用北京时间，审计保留 UTC。完整来源无法提供时不接受整份 `submission_only` 确认，逐项控制仍可用。

公开活动路由见 [public-import/0.1](../contracts/docs/public-import-v0.1.md)：仅开放三个现有网站，不接受任意 URL；最多 2 轮公开采集并行、每账号 1 轮，每轮最多 6 页/150 秒。预览不建任务，用户选中后按任务版本原子写入通知；重复导入不复制、不复活回收站记录。

接线回归：`python -m unittest server.test_submission_runtime server.test_public_sources server.test_source_jobs server.test_source_schedule server.test_teaching_reprocessing server.test_manual_materials -q`（`PYTHONPATH` 包含仓库根及 `server`）；浏览器完整入口验收按 [前端说明](../frontend/README.md) 执行，合成外部服务与真实模型联调须分别记录。

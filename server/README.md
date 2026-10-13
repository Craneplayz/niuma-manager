```text
server/
├── README.md                 本目录的文件说明与启动命令。
├── .gitignore               排除本地凭据加密密钥。
├── app.py                    账号、工作区与规划的本地 HTTP API。
├── runtime_environment.py    环境身份、数据目录归属、会话名称及版本信息。
├── manage_environment.py     初始化、检查并启动开发 / 测试 / 生产环境。
├── release_images.py         记录测试实际运行镜像，并在生产启动前核对、固定镜像 ID。
├── test_release_images.py    验证缺失验收记录、镜像替换及不安全标签不能用于发布。
├── test_environment_isolation.py 验证真实三 API 的账号、数据、附件、配置与会话隔离。
├── test_container_environment.py 用临时 Docker 卷验证三环境、HTTPS 与重建持久化。
├── recognition.py            文本识别批次与候选确认接口。
├── reprocessing.py           已保存教学网通知的定向运行、停止与持久进度。
├── test_reprocessing.py      来源分页、幂等、账号隔离、停止与旧结果保留测试。
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
├── teaching.py               核验当前账号快照、调用 v0.4 并适配现有任务卡片。
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
├── docs/archive/2026-10-01-issue69-direct-planning.json  Windows 无独立识别/规划服务的合成实测。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

后台教学网同步接口见 [source-sync/0.1](../contracts/docs/source-sync-v0.1.md)：每轮最多 4 条识别，全服务最多 4 轮活动同步，共用原模型队列；采集和模型期间不持有数据库写锁。刷新不影响执行，停止拒绝迟到写入，服务重启保留结果并标记中断。运行 `python -m unittest discover -s server -p test_source_jobs.py -v` 使用临时数据库和合成采集/模型验证。

定时同步默认关闭；在对应环境 `config.json` 加 `"scheduled_sync": true` 并重启，账号在来源设置保存所选来源和 15～10080 分钟间隔后生效；只支持教学网，须使用单业务 API 进程。验收运行 `python -m unittest discover -s server -p test_source_schedule.py -v`，包括持久配置、手动撞期、失败重试及关闭时拒绝迟到写入，不发送私人数据。

开发和测试优先使用 [统一环境入口](deploy/ENVIRONMENTS.md)：仓库根运行 `python server/manage_environment.py init all`，再运行 `python server/manage_environment.py local development` 或 `local test`；识别和规划由业务 API 直接调用，不必启动 1333/1334。
原 `frontend/` 的 `npm run dev:api` 和 `npm run dev` 保留旧本地配置兼容，不代表环境已隔离；已上线安装须按迁移步骤保留原数据后接管。
Docker 公网部署使用 [部署说明](deploy/README.md)，`NIUMA_HOST` 设置容器监听地址，`NIUMA_ALLOWED_ORIGINS` 指定允许的完整来源，`NIUMA_COOKIE_SECURE=1` 仅经 HTTPS 发送会话；未设置时保留 localhost:1331 的本机行为。
Windows 整合测试在仓库根运行 `powershell -ExecutionPolicy Bypass -File server/start-local-test.ps1`；默认 test 环境为网页 1531、API 1532，数据库在 `server/data/test/`，旧测试数据库不迁移、不删除。
教学网同步使用原生 v0.4：按课程作业、课程通知、教学内容顺序识别，来源账号和快照由后端核验；明确任务自动加入，引用所需材料缺失时保留审核，详见 [运行视图](../contracts/docs/teaching-workspace-v0.1.md)。
已读部分无任务但内容不完整时标记“来源待补全”，相同快照不重复调用模型；补全正文/附件后重新同步，模型失败仍可重试。
真实 API 合成验收在仓库根运行 `.venv/Scripts/python.exe server/run_teaching_smoke.py --output local-data/teaching-smoke.json`；只发送合成内容，独立临时数据库，不访问校园网账号。
原生 submitted 来源尚无永久行动映射时继续识别并保留候选，提交范围待核对，不能整条跳过或自动入库；submitted 后状态变化会持久暂停模型及自动入库，再次 submitted/重启不解除，已有候选仍可人工处理。逐行动映射与显式解除来源核对仍待 #110，边界见 [采集状态约定](../contracts/docs/teaching-capture-v0.1.md)。
教学网 DeepSeek 调用显式关闭 thinking；真实调用用原文片段编号填回证据，错误区分输出截断、JSON、字段与证据错误，不记录私人模型响应。
此测试版尚未接入定向重新识别、教学网图片或 DOCX 原件阅读；PDF/TXT 沿用采集器已有能力，网页结构变化和没有来源时间的普通旧材料仍可能需要核查。
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
在 `frontend/` 运行 `npm test` 执行前端与业务 API 测试；规划模块另在仓库根运行 `python -m unittest discover -s task_planning/tests -t . -q`。
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

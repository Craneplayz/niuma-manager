```text
server/
├── README.md                 本目录的文件说明与启动命令。
├── .gitignore               排除本地凭据加密密钥。
├── app.py                    账号、工作区与规划的本地 HTTP API。
├── recognition.py            文本识别批次与候选确认接口。
├── planning.py               规划直调/HTTP 切换、配置读取及并发保护。
├── wechat_media.py           微信附件上传、账号凭证、异步导入与结果查询。
├── task_decomposition.py     AI 任务拆解、追问预览与确认写入接口。
├── source_sync.py            调用教学网采集并返回 v0.2 识别请求。
├── requirements.txt          定义账号凭据加密所需的 Python 依赖。
├── text_contract.py          文本识别结果的服务端契约校验。
├── test_api.py               账号与业务 API 测试。
├── test_source_sync.py       教学网同步权限与请求转换测试。
├── test_incremental_recognition.py  增量筛选、逐条识别与失败重试回归。
├── test_recognition_runtime.py  直调识别、Windows 子进程、HTTP 回退与并发限制测试。
├── test_planning_runtime.py   规划直调/HTTP、配置、并发、异常与确认入库测试。
├── test_wechat_media.py       微信附件鉴权、导入幂等、失败及候选入库测试。
├── test_text_contract.py     文本识别契约测试。
├── docs/archive/2026-10-01-issue69-direct-planning.json  Windows 无独立识别/规划服务的合成实测。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

在 `frontend/` 运行 `npm run dev:api` 启动业务 API，另开终端运行 `npm run dev`；识别和规划默认都由业务 API 直接调用，不必启动 1333/1334。
文本识别默认由业务 API 直接调用 `ddl_text.pipeline.process_request`，读取仓库根目录 `.env` 的模型配置，无需启动 1333；已有环境变量优先于 `.env`。
在启动业务 API 的终端设置 `TEXT_HARNESS_URL`（及可选 `TEXT_HARNESS_TOKEN`）时，识别改走原 HTTP 服务；连接失败不会自动切换模型或重复调用。
后端同一进程只允许一个识别请求执行，跨账号和两种调用方式共用锁，忙碌返回 503；模型配置缺失返回 503，不影响登录、工作区和协议演示。
该锁适用于当前单进程本地 API，多进程部署需另外共享并发控制。
规划默认调用 `task_planning.planning_harness.generate`，同样读取根目录 `.env`，沿用独立规划服务的七牛配置要求；未配置密钥返回 503，不用本地假排程代替模型。
显式导出 `HARNESS_URL` 时走原 HTTP 通道，仍支持 `HARNESS_TOKEN`；HTTP 失败不会转而再调用本地模型。恢复直调需清除此环境变量并重启，`.env` 中的路由变量不生效。
规划有独立的进程内锁，同一时间只执行一个规划请求，忙碌返回 503，异常后释放；识别和规划各自限流，不互相占锁。模型网络超时保持 20 秒、HTTP 等待保持 25 秒，无自动重试。
任务拆解同样读取根目录 `.env` 的模型配置，并使用自己的进程内锁。`POST /api/decomposition/proposals` 只生成追问或预览，复杂任务的方案须通过 `POST /api/decomposition/apply` 确认后才替换任务步骤；任务版本变化时旧方案拒绝写入；回应携带任务 ID，确认时必须提交同一任务 ID，公开格式见 [拆解契约](../contracts/task-decomposition/README.md)。
两种规划通道仍使用规划 2.0 和同一结果校验，预览只保存提案，用户确认后按账号与任务版本写日历；DDL、任务状态和已有计划不因生成预览而改变。
`POST /api/planning/proposals` 可带 `taskIds` 限定本账号有 DDL、未完成且未安排的新增任务；无匹配任务返回 400，提案仍须通过 `/api/planning/apply` 确认。
同一接口可带 `replan: true` 纳入已有安排的任务，格式见 [规划接口约定](../contracts/task-planning/README.md)；使用 `taskIds` 时只重新规划选中任务。共享规划 2.0 输入输出不变，服务端记录待替换的时段 ID，确认时原子替换有新时段的任务尚未开始的安排；无新时段、模型失败、取消预览或版本冲突保留原安排，等待期间原时段已经开始时拒绝应用。
在 `frontend/` 运行 `npm test` 执行前端与业务 API 测试；规划模块另在仓库根运行 `python -m unittest discover -s task_planning/tests -t . -q`。
直调识别与独立识别服务均保留 90 秒整批预算，HTTP 调用等待 95 秒，前端等待 100 秒；本设置不改变规划接口的超时。
教学网同步最多等待 300 秒，真实账号采集实测约 2 分钟。
教学网同步需安装 `server/requirements.txt` 与 `crawler/` 依赖；用户在来源管理页保存一次教学网凭据，密码按账号加密入库，密钥位于数据库同目录的 `credential.key` 且不会提交；采集时使用权限为 0600 的临时文件并在结束后删除。
备份数据库时需同时安全备份 `credential.key`，密钥丢失后须重新保存教学网凭据。
Windows 不支持 POSIX 0600 权限；请将 `credential.key` 所在目录设为仅本人可访问。

微信附件由本机采集器使用账号来源设置中签发的一次显示凭证调用 `/api/sources/wechat/assets` 上传原件，再调用 `/api/sources/wechat/imports` 提交 v0.3.1 JSON；请求、回执、结果和查询均使用新版结构及语义校验。
上传只接受一个 `file` 文件 part，拒绝额外文字字段和多文件；回执的类型、长度与哈希由原件计算。
附件候选保存到账号候选库，文件模型证据须经确认后入库；忽略、确认与刷新状态持久化，重试保留已有候选及用户决定。
相同 request_id 的采集时间或引用变化返回 409；每次 retry_id 及其选定记录持久化，历史重放返回 200，识别请求级失败保留此前的部分成功结果。
同账号的内容缓存包含消息身份、上下文、原始发布时间、附件哈希、读取器代码/规则和模型配置；采集时间、请求编号和重签句柄不触发重识别，新请求保留自己的回执/查询身份并复用原候选处理状态。
原件保存在数据库同目录的 `wechat-assets/`，不公开文件 URL；每次读取重新核验账号、有效句柄、存储位置、长度和 SHA-256，并限制读取时间与字节数。
未引用原件默认保留 24 小时，可用 `WECHAT_UNREFERENCED_TTL_SECONDS` 调整（最短 300 秒）；启动、上传、导入或查询时清理过期原件，作业引用的原件保留用于查询和重试。
登录用户通过来源设置确认调用 `POST /api/sources/wechat/remove` 删除原件、导入记录和候选，已确认任务保留；凭证及句柄立即失效，处理中迟到结果不重新写入，账号删除后的遗留内容在启动时清理。
被占用原件保存到持久化删除队列，在启动或后续上传/导入/查询时重试清理；接口用 `cleanup_pending` 如实报告剩余原件数量，待删字节继续计入 200 MiB 账号配额。
`DELETE /api/sources/wechat/assets/{file_ref}` 返回无响应体的 204，仅原账号可幂等撤销自己的句柄；撤销后导入和新重试返回 404，重新上传签发新句柄，不复活旧引用。
图片识别调用 `ddl_text.media_pipeline.process_media_request`；使用 Python 3.10+ 安装 `ddl_text/media_requirements.txt`，模型配置沿用根目录 `.env`，须选用支持图片的七牛模型。
图片识别与文本识别共用并发锁，读取器分别限制解码、记录和批次预算；当前图片入口仅支持静态 JPG/PNG，PDF/DOCX 明确报告不支持。

教学网采集后按应用账号、教学网账号及通知 ID 保存内容版本；只向模型发送新增、修改、未处理或识别失败的记录，忽略采集时间变化，首次启用增量索引仍处理历史通知。
每条通知生成独立 v0.2 请求并保留首次采集时间作为重试的时间基准；成功（含无任务）且没有待自动创建的任务时跳过。识别成功后若页面关闭或创建失败，再次同步复用已保存的候选补建任务，不重复调用模型；已加入、已忽略、撤回后待核对及有疑点的候选不自动补建。修改后生成新候选，不自动改动已确认任务。
`POST /api/sources/sync` 返回 `requests`（单条记录的 v0.2 请求列表）及 `summary`（total / skipped / pending）；爬虫输出契约不变。
`POST /api/recognition/retry` 接收原 v0.2 请求，仅重新识别失败记录；保留成功候选及确认状态，成功请求保持幂等，原 `/batches` 重放行为不变。
业务 API 直接使用模型侧校验过的 v0.2 时间字段；无疑点的外部新增行动可自动入库，无日期任务保留空 DDL 并在工作台补充，需核实及手动输入的候选仍待用户确认。
重试由用户点击或再次同步触发，不在同次同步内无限重试；采集本身的超时独立于每条识别的 90 秒预算。

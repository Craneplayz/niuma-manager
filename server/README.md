```text
server/
├── README.md                 本目录的文件说明与启动命令。
├── .gitignore               排除本地凭据加密密钥。
├── app.py                    账号、工作区与规划的本地 HTTP API。
├── recognition.py            文本识别批次与候选确认接口。
├── source_sync.py            调用教学网采集并返回 v0.2 识别请求。
├── requirements.txt          定义账号凭据加密所需的 Python 依赖。
├── text_contract.py          文本识别结果的服务端契约校验。
├── planning_harness.py       调用模型生成规划 2.0 提案的本地服务。
├── test_api.py               账号与业务 API 测试。
├── test_source_sync.py       教学网同步权限与请求转换测试。
├── test_incremental_recognition.py  增量筛选、逐条识别与失败重试回归。
├── test_text_contract.py     文本识别契约测试。
├── test_planning_harness.py  规划模型服务测试。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

在 `frontend/` 运行 `npm run dev:api` 启动业务 API，运行 `npm run dev:planning` 启动规划服务。
在 `frontend/` 运行 `npm test` 执行前端、业务 API 与规划服务测试。
文本识别请求等待 95 秒，配合识别服务 90 秒预算及前端 100 秒等待；本设置不改变规划接口的超时。
教学网同步最多等待 300 秒，真实账号采集实测约 2 分钟。
教学网同步需安装 `server/requirements.txt` 与 `crawler/` 依赖；用户在来源管理页保存一次教学网凭据，密码按账号加密入库，密钥位于数据库同目录的 `credential.key` 且不会提交；采集时使用权限为 0600 的临时文件并在结束后删除。
备份数据库时需同时安全备份 `credential.key`，密钥丢失后须重新保存教学网凭据。
Windows 不支持 POSIX 0600 权限；请将 `credential.key` 所在目录设为仅本人可访问。

教学网采集后按应用账号、教学网账号及通知 ID 保存内容版本；只向模型发送新增、修改、未处理或识别失败的记录，忽略采集时间变化，首次启用增量索引仍处理历史通知。
每条通知生成独立 v0.2 请求并保留首次采集时间作为重试的时间基准；成功（含无任务）跳过，修改后生成新候选，不自动改动已确认任务。
`POST /api/sources/sync` 返回 `requests`（单条记录的 v0.2 请求列表）及 `summary`（total / skipped / pending）；爬虫输出契约不变。
`POST /api/recognition/retry` 接收原 v0.2 请求，仅重新识别失败记录；保留成功候选及确认状态，成功请求保持幂等，原 `/batches` 重放行为不变。
业务 API 直接使用模型侧校验过的 v0.2 时间字段；无疑点的外部新增行动可自动入库，无日期任务保留空 DDL 并在工作台补充，需核实及手动输入的候选仍待用户确认。
重试由用户点击或再次同步触发，不在同次同步内无限重试；采集本身的超时独立于每条识别的 90 秒预算。

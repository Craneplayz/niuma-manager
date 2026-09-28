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
├── test_text_contract.py     文本识别契约测试。
├── test_planning_harness.py  规划模型服务测试。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

在 `frontend/` 运行 `npm run dev:api` 启动业务 API，运行 `npm run dev:planning` 启动规划服务。
在 `frontend/` 运行 `npm test` 执行前端、业务 API 与规划服务测试。
教学网同步需安装 `server/requirements.txt` 与 `crawler/` 依赖；用户在识别审核页保存一次教学网凭据，密码按账号加密入库，密钥位于数据库同目录的 `credential.key` 且不会提交；采集时使用权限为 0600 的临时文件并在结束后删除。
备份数据库时需同时安全备份 `credential.key`，密钥丢失后须重新保存教学网凭据。

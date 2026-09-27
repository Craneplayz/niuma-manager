```text
server/
├── README.md                 本目录的文件说明与启动命令。
├── app.py                    账号、工作区与规划的本地 HTTP API。
├── recognition.py            文本识别批次与候选确认接口。
├── text_contract.py          文本识别结果的服务端契约校验。
├── planning_harness.py       调用模型生成规划 2.0 提案的本地服务。
├── test_api.py               账号与业务 API 测试。
├── test_text_contract.py     文本识别契约测试。
├── test_planning_harness.py  规划模型服务测试。
└── data/                     本地 SQLite 数据目录，不提交数据库文件。
```

在 `frontend/` 运行 `npm run dev:api` 启动业务 API，运行 `npm run dev:planning` 启动规划服务。
在 `frontend/` 运行 `npm test` 执行前端、业务 API 与规划服务测试。

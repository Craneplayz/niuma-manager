# 模块交接契约

```text
contracts/
├── README.md                         # 交接方向、样例用途及校验入口
├── requirements-dev.txt              # 离线契约校验依赖，不加入应用运行依赖
├── check_v03_examples.py             # v0.3 Schema、引用、覆盖/状态与进度检查
├── tests/test_v03_contract.py        # 合成正例及非法输入/输出、部分失败回归
├── docs/text-attachments-v0.3.md     # 微信图片/文件、异步导入和兼容规则
├── docs/archive/2026-10-01-file-input-probe.json # 当前模型合成附件试探及限制
├── schemas/common-v0.3.schema.json   # v0.3 记录、附件、证据、候选等共享定义
├── examples/text-attachments-v03-cases.json # v0.3 边界场景 request/expected 成对样例
├── source-to-app/                   # 微信本地采集交给业务后端的异步导入契约
│   ├── wechat-import-v0.3.schema.json # 文件回执、job、错误与重试请求定义
│   ├── wechat-asset-v03-example.json # 文件上传后取得的句柄与校验元数据
│   └── wechat-import-v03-examples.json # 主样例排队、运行、完成的查询结果
├── source-to-text/                   # 来源模块交给文本识别的请求
│   ├── request-v0.3.schema.json      # v0.3 请求 Schema，引用共享定义
│   ├── text-attachments-v03-request.json # 无正文图片与前文截止要求的合成输入
│   ├── text-frontend-v02-request.json # 两条通知输入，覆盖来源、发布时间及一条通知多任务
│   └── text-year-inference-request.json # 缺年份、跨年及显式年份的五条合成输入
└── text-to-app/                      # 文本识别交给后端及前端的候选结果
    ├── result-v0.3.schema.json       # v0.3 输出 Schema，引用共享定义
    ├── text-attachments-v03-output.json # 主样例的人工期望，含附件证据
    ├── text-contract-v0.2.json       # request/expected 成对样例，覆盖日期精度、空字段及部分失败
    ├── text-frontend-v02-output.json # 与前端输入配套的三项候选，含参考时间、无日期及明确截止
    ├── text-year-inference-output.json # 补年输出，保留来源新旧不明及逾期等独立疑点
    └── text-classification-v02.json # F3 分类成对样例：新增、无日期、资讯、变更和历史过滤
```

## 交接方式

- 来源模块按 [输入样例](source-to-text/text-frontend-v02-request.json) 提供原文及元数据，文本模块接收完整 v0.2 请求。
- `platform` 标明来源：教学网 `teaching_web`、微信 `wechat` 的结果可直接进日历；对话框手动输入一律填 `manual`，结果须用户确认。`unknown` 已停用，新代码不得再送；旧数据中的 `unknown` 按 `manual` 处理。
- 文本模块输出 [候选结果](text-to-app/text-frontend-v02-output.json)，后端校验保存候选，前端供用户核对后确认成任务。
- [成对契约样例](text-to-app/text-contract-v0.2.json) 是测试包装：调用识别服务时取 `request`，校验人工期望时取 `expected`，不能把整个文件当作 API 请求。
- [补年输出](text-to-app/text-year-inference-output.json) 对应 [补年输入](source-to-text/text-year-inference-request.json)：仅省略年份且按规则可确定的截止输出 explicit，保留原文及补年依据，不单独触发 needs_review。
- [F3 分类样例](text-to-app/text-classification-v02.json) 使用既有 v0.2 字段：明确新增可自动入库，无日期行动 due=null；needs_review=true 和变更通知人工处理；资讯和历史不输出候选，过滤说明保存在 record_results.warnings。外部通知只凭采集时间补年时需核对原通知新旧，近期逾期需核对是否仍应执行，这两项独立疑点也反映在补年输出样例中。
- v0.2 字段定义及确认接口见 [共享协议](../docs/backend-harness-alignment.md)；v0.3 新增规则以 [附件规约](docs/text-attachments-v0.3.md) 和相应 Schema 为准，不改变 v0.2。
- v0.3 目前仅定义契约：默认上传文件后在 JSON 中提供 file_ref，由识别侧读取，不要求微信端 OCR；已有文字块仍可输入。纯附件无需虚构正文，读取失败不能伪装无任务，入口须等 #94–#97 联调后开放。
- v0.3 Schema 使用本地 URN 引用，须加载全部四份并启用格式校验；导入 Schema 需指定 asset_ack/ack/job/error/retry 定义，具体入口见规约表格。
- 交接样例均为合成期望；docs/archive 单独标明真实能力试探，不能将格式验证或单次调用当作模型质量评测。

## 校验与回放

在仓库根目录执行，均不调用外部模型：

```powershell
python -m unittest discover -s ddl_text/tests -t . -q
python -m unittest discover -s server -p 'test_*.py' -q
python scripts/check_frontend_integration.py
python -m ddl_text --input contracts/source-to-text/text-frontend-v02-request.json --drafts ddl_text/tests/fixtures/text-frontend-v02-drafts.json
```

输入校验入口为 `ddl_text.contract_v02.validate_request`；业务后端的契约测试直接读取这里的成对样例，联调脚本使用这里的输入样例检查真实后端。

旧 v0.1 契约、阶段 C 样例、运行配置及离线模型草稿放在 [文本测试 fixtures](../ddl_text/tests/fixtures/)，不作为来源模块或前端的新接口；旧 CLI 的 17 个样例可用 `python scripts/check_text_contract_examples.py` 校验。

v0.3 合成样例与反例单独执行，不调用模型或现有 v0.2 应用：

```powershell
python -m pip install -r contracts/requirements-dev.txt
python contracts/check_v03_examples.py
python -m unittest discover -s contracts/tests -p 'test_*.py' -v
```

这些检查验证格式、引用和状态一致性；文字块引用可核对子串，直接文件引用仅标记 model_reported，无法独立核对引文。它们不验证真实读取、语义、鉴权或幂等；后续各模块需读取共享样例验证相应行为。

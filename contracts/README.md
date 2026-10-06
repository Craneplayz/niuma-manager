# 模块交接契约

```text
contracts/
├── README.md                         # 交接方向、样例用途及校验入口
├── task-decomposition/              # AI 拆解公开 v0.1 请求、回应 Schema 及合成样例
├── source-to-text/                   # 爬虫、微信等来源交给文本识别的 v0.2 请求
│   ├── text-frontend-v02-request.json # 两条通知输入，覆盖来源、发布时间及一条通知多任务
│   └── text-year-inference-request.json # 缺年份、跨年及显式年份的五条合成输入
└── text-to-app/                      # 文本识别交给后端及前端的候选结果
    ├── result-v0.3.schema.json       # v0.3 输出 Schema，引用共享定义
    ├── result-v0.3.1.schema.json     # 当前结果 Schema 源文件
    ├── text-attachments-v031-output.json # 文件模型引用强制待审核的当前期望
    ├── text-attachments-v03-output.json # 主样例的人工期望，含附件证据
    ├── text-contract-v0.2.json       # request/expected 成对样例，覆盖日期精度、空字段及部分失败
    ├── text-frontend-v02-output.json # 与前端输入配套的三项候选，含参考时间、无日期及明确截止
    ├── text-year-inference-output.json # 补年输出，保留来源新旧不明及逾期等独立疑点
    └── text-classification-v02.json # F3 分类成对样例：新增、无日期、资讯、变更和历史过滤
```

## 交接方式

- AI 拆解接口以 [拆解契约](task-decomposition/README.md)、[Schema](task-decomposition/schema-v0.1.json) 和 [样例](task-decomposition/examples-v0.1.json) 为准；内部模型草稿由后端包装成带任务编号的公开回应，确认须再核对任务编号。
- 来源模块按 [输入样例](source-to-text/text-frontend-v02-request.json) 提供原文及元数据，文本模块接收完整 v0.2 请求。
- `platform` 标明来源：教学网 `teaching_web`、微信 `wechat` 的结果可直接进日历；对话框手动输入一律填 `manual`，结果须用户确认。`unknown` 已停用，新代码不得再送；旧数据中的 `unknown` 按 `manual` 处理。
- 文本模块输出 [候选结果](text-to-app/text-frontend-v02-output.json)，后端校验保存候选，前端供用户核对后确认成任务。
- [成对契约样例](text-to-app/text-contract-v0.2.json) 是测试包装：调用识别服务时取 `request`，校验人工期望时取 `expected`，不能把整个文件当作 API 请求。
- [补年输出](text-to-app/text-year-inference-output.json) 对应 [补年输入](source-to-text/text-year-inference-request.json)：仅省略年份且按规则可确定的截止输出 explicit，保留原文及补年依据，不单独触发 needs_review。
- [F3 分类样例](text-to-app/text-classification-v02.json) 使用既有 v0.2 字段：明确新增可自动入库，无日期行动 due=null；needs_review=true 和变更通知人工处理；资讯和历史不输出候选，过滤说明保存在 record_results.warnings。外部通知只凭采集时间补年时需核对原通知新旧，近期逾期需核对是否仍应执行，这两项独立疑点也反映在补年输出样例中。
- v0.2 字段定义及确认接口见 [共享协议](../docs/backend-harness-alignment.md)；附件当前标准为 [v0.3.1 规约](docs/text-attachments-v0.3.1.md)。原 v0.3 Schema/样例冻结供旧草案回归，不应用于新运行端。
- v0.3 目前仅定义契约：默认上传文件后在 JSON 中提供 file_ref，由识别侧读取，不要求微信端 OCR；已有文字块仍可输入。纯附件无需虚构正文，读取失败不能伪装无任务，入口须等 #94–#97 联调后开放。
- v0.3.1 bundle 无外部引用，用 `root_schema("ack")` 等七种根入口直接校验；仍须调用 check_request/check_pair/check_job 完成边界和跨字段语义校验，不能只检查结构。
- v0.3.1 中 attachment_file/model_reported 候选必须待审核；部分读取和质量不确定也独立强制审核。#103 等消费方须显式迁移版本和校验入口，不能只改版本字符串或继续自动入库。
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

附件合成样例与反例单独执行，不调用模型或现有 v0.2 应用：

```powershell
python -m pip install -r contracts/requirements-dev.txt
python contracts/build_v031_bundle.py --check
python contracts/check_v031_examples.py
python contracts/check_v03_examples.py
python -m unittest discover -s contracts/tests -p 'test_*.py' -v
```

这些检查验证格式、引用和状态一致性；文字块引用可核对子串，直接文件引用为 model_reported 且必须待审核。它们不验证真实读取、模型语义、鉴权或持久化幂等；后续各模块需读取共享样例验证相应行为。URL 抓取还需真实 DNS/连接校验，文件 TTL/删除须有存储层测试。

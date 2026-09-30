# 模块交接契约

```text
contracts/
├── README.md                         # 交接方向、样例用途及校验入口
├── source-to-text/                   # 爬虫、微信等来源交给文本识别的 v0.2 请求
│   ├── text-frontend-v02-request.json # 两条通知输入，覆盖来源、发布时间及一条通知多任务
│   └── text-year-inference-request.json # 缺年份、跨年及显式年份的五条合成输入
└── text-to-app/                      # 文本识别交给后端及前端的 v0.2 候选结果
    ├── text-contract-v0.2.json       # request/expected 成对样例，覆盖日期精度、空字段及部分失败
    ├── text-frontend-v02-output.json # 与前端输入配套的三项候选，含参考时间、无日期及明确截止
    ├── text-year-inference-output.json # 补年输出，保留来源新旧不明及逾期等独立疑点
    └── text-classification-v02.json # F3 分类成对样例：新增、无日期、资讯、变更和历史过滤
```

## 交接方式

- 来源模块按 [输入样例](source-to-text/text-frontend-v02-request.json) 提供原文及元数据，文本模块接收完整 v0.2 请求。
- `platform` 标明来源：教学网 `teaching_web`、微信 `wechat` 的结果可直接进日历；对话框手动输入一律填 `manual`，结果须用户确认；平台不明填 `unknown`，同样须确认。
- 文本模块输出 [候选结果](text-to-app/text-frontend-v02-output.json)，后端校验保存候选，前端供用户核对后确认成任务。
- [成对契约样例](text-to-app/text-contract-v0.2.json) 是测试包装：调用识别服务时取 `request`，校验人工期望时取 `expected`，不能把整个文件当作 API 请求。
- [补年输出](text-to-app/text-year-inference-output.json) 对应 [补年输入](source-to-text/text-year-inference-request.json)：仅省略年份且按规则可确定的截止输出 explicit，保留原文及补年依据，不单独触发 needs_review。
- [F3 分类样例](text-to-app/text-classification-v02.json) 使用既有 v0.2 字段：明确新增可自动入库，无日期行动 due=null；needs_review=true 和变更通知人工处理；资讯和历史不输出候选，过滤说明保存在 record_results.warnings。外部通知只凭采集时间补年时需核对原通知新旧，近期逾期需核对是否仍应执行，这两项独立疑点也反映在补年输出样例中。
- 字段定义及确认接口以 [共享协议](../docs/backend-harness-alignment.md) 为准；本次仅迁移样例，不修改字段或合并现有校验器。
- 所有文件均为合成样例；人工期望及离线输出不代表真实模型的质量评测。

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

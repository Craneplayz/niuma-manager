# 模块交接契约

待核对任务通过普通完成和编辑操作确认的接口约定见 [待核对任务用户操作](docs/review-task-actions.md)，包含可选 `complete`、原子保存及重试规则。

账号头像上传、恢复默认、用户返回字段及图片边界见 [账号头像](docs/account-avatar.md)。

```text
contracts/
├── docs/text-import-jobs-v0.1.md      # 文本后台作业、幂等、停止与刷新恢复约定
├── schemas/text-import-jobs-v0.1.schema.json # 文本后台作业状态信封 Schema
├── examples/text-import-jobs-v01.json # 前后端共用的排队、识别及全部终态样例
├── schemas/producer-observation-v0.1.schema.json # 下载计时与模型实际用量的无正文事件
├── examples/producer-observations-v01.json # 已知用量、未知用量及失败事件共享样例
├── tests/test_producer_observations.py # 观测契约及敏感字段拒绝检查
├── docs/producer-observation-v0.1.md # 可选生产方回调、CLI 及服务端接收边界
├── collection_failure.py             # 采集失败阶段与固定原因码的受限契约及解析
├── examples/collection-failures-v01.json # 凭据、浏览器、登录、页面与导出失败共享样例
├── tests/test_collection_failure.py   # 旧生产方兼容、边界和敏感字段拒绝回归
├── README.md                         # 交接方向、样例用途及校验入口
├── docs/account-avatar.md            # 当前账号头像、服务端持久化与图片校验约定
├── docs/email-auth-v0.1.md            # 邮箱验证码注册、补验证、密码找回及会话字段约定
├── schemas/email-auth-v0.1.schema.json # 邮箱验证/密码找回请求、验证码句柄及公开响应独立根
├── examples/email-auth-v01.json      # 新旧账号、密码找回、IDNA邮箱、限流及投递错误样例
├── tests/test_email_auth.py          # 邮箱接口共享正例、字段缺失和敏感响应字段反例
├── public_import.py                  # 公开活动采集、预览、选择导入的独立根及一致性校验
├── schemas/public-import-v0.1.schema.json # 公开活动自包含 Schema 包
├── examples/public-import-v01.json   # 生产方与前端共用的合成公开活动样例
├── tests/test_public_import.py       # 信封、状态、重复活动和来源检查
├── docs/public-import-v0.1.md         # 公开活动权限、幂等和导入边界
├── schemas/error-report-v0.1.schema.json # 用户主动提交的受限错误报告请求
├── examples/error-reports-v01.json    # 错误分类、操作及缺少响应的共享合成样例
├── docs/error-reports-v0.1.md         # 点击提交、去重、限流、权限及后台展示
├── tests/test_error_reports.py       # 正例、字段白名单与私人信息反例
├── source_sync.py                    # 后台来源同步的独立请求/响应 Schema 与计数语义校验
├── docs/source-sync-v0.1.md           # 同步幂等、账号隔离、并发、停止与进度约定
├── examples/source-sync-v01.json     # 后台运行、重试和定时配置的共享合成样例
├── tests/test_source_sync.py         # 独立根 Schema、记录唯一性、计数与终态检查
├── requirements-teaching-dev.txt     # 教学网政策校验依赖，由可选教学网入口显式引用
├── build_teaching_v04.py             # 生成/核对自包含教学网批次请求与结果根 Schema
├── teaching_io.py                    # 教学网批次身份、状态、来源和基础政策校验
├── schemas/teaching-request-v0.4.schema.json # 独立教学网批次请求
├── schemas/teaching-result-v0.4.schema.json # 独立教学网批次结果
├── examples/teaching-model-routing-v04-cases.json # 新模型分类的八组真实合成输入/输出
├── examples/teaching-content-types-v04.json # 任务、通知、活动时间与混合内容的合成回放契约（非真实模型结果）
├── examples/teaching-v04-cases.json   # 7 组教学网公开批次边界与多任务样例
├── tests/test_teaching_io.py          # 教学网批次、信任与非法状态反例
├── docs/teaching-v0.4.md              # 教学网显式接入、消费复核及重识别规则
├── teaching_review_draft.py          # 教学网字段证据及学期门禁的离线参考校验
├── schemas/teaching-request-v0.4.schema.json # #124 原版正式请求根，用于爬虫独立证据导出
├── schemas/teaching-review-pair-v0.4-draft.schema.json # 自包含配对草案，不是 API 请求
├── examples/teaching-review-v0.4-draft-cases.json # 23 个教学网审核与年份判断样例
├── tests/test_teaching_review_draft.py # 草案证据、身份绑定、边界与反例检查
├── submission_policy.py              # #110 提交状态、可信来源及精确行动的纯决策校验
├── schemas/submission-policy-v0.1.schema.json # 后端内部观察与永久行动映射根 Schema
├── schemas/submission-control-v0.1.schema.json # 用户重开、恢复自动完成及解除核对的操作根
├── examples/submission-policy-v01-cases.json # 首次提交、多行动、状态变化及不可信输入
├── tests/test_submission_policy.py    # 同源顺序、精确关联、用户决定和授信边界反例
├── docs/submission-policy-v0.1.md     # 已提交作业退出候选与完成状态交接规约
├── submission_operations.py          # #110 公开查询/操作根及复用内部控制的纯计划
├── schemas/submission-operations-v0.1.schema.json # 无外部引用的五类业务信封
├── examples/submission-operations-v01-cases.json # 关联、重开、恢复及确认的共享期望
├── tests/test_submission_operations.py # 请求边界、并发版本与控制状态保留反例
├── docs/submission-operations-v0.1.md  # 登录用户操作、回执和持久幂等接入约定
├── docs/teaching-review-v0.4-draft.md  # #108/#109 阶段 A 的字段及接入边界
├── task-planning/                   # 业务排程及重新规划的请求、回应和确认语义
├── task-decomposition/              # AI 拆解公开 v0.1 请求、回应 Schema 及合成样例
├── requirements-dev.txt              # 离线契约校验依赖，不加入应用运行依赖
├── reprocessing.py                   # #112 独立操作根、状态转换与永久行动关联参考
├── schemas/reprocessing-v0.1.schema.json # 定向重识别自包含业务信封
├── examples/reprocessing-v01-cases.json # 查询、运行、停止、失败和用户决定共享样例
├── tests/test_reprocessing.py         # 幂等选择、版本、迟到结果与保留决定反例
├── docs/reprocessing-v0.1.md          # 重识别操作、来源授权和队列交接规约
├── check_v03_examples.py             # v0.3 Schema、引用、覆盖/状态与进度检查
├── build_v031_bundle.py              # 生成/检查无外部引用的 v0.3.1 Schema 包
├── check_v031_examples.py            # v0.3.1 独立根入口、边界、审核及状态机校验
├── tests/test_v03_contract.py        # 合成正例及非法输入/输出、部分失败回归
├── tests/test_v031_contract.py       # v0.3.1 审查意见对应的结构与语义反例
├── docs/teaching-source-snapshot-v0.2.md # 教学网来源引用、读取状态及支持边界
├── docs/text-attachments-v0.3.md     # 微信图片/文件、异步导入和兼容规则
├── docs/text-attachments-v0.3.1.md   # 当前契约：安全边界、强制审核、幂等与生命周期
├── docs/archive/2026-10-01-file-input-probe.json # 当前模型合成附件试探及限制
├── schemas/teaching-source-snapshot-v0.2.schema.json # 教学网采集 hints 独立校验
├── tests/test_teaching_source_snapshot.py # 教学网采集样例与读取状态反例
├── source-to-text/teaching-source-snapshot-v02-cases.json # 合成 HTML 与采集输出
├── schemas/common-v0.3.schema.json   # v0.3 记录、附件、证据、候选等共享定义
├── schemas/common-v0.3.1.schema.json # v0.3.1 收紧字段边界与审核规则的共享定义
├── schemas/bundle-v0.3.1.schema.json # 七种信封的自包含 Schema 包（生成文件）
├── examples/text-attachments-v03-cases.json # v0.3 边界场景 request/expected 成对样例
├── examples/text-attachments-v031-cases.json # 当前版本成对样例及 unsupported 格式
├── source-to-app/                   # 微信本地采集交给业务后端的异步导入契约
│   ├── wechat-import-v0.3.schema.json # 文件回执、job、错误与重试请求定义
│   ├── wechat-import-v0.3.1.schema.json # 当前文件/导入回执、错误及进度定义
│   ├── wechat-asset-v03-example.json # 文件上传后取得的句柄与校验元数据
│   ├── wechat-asset-v031-example.json # 当前文件回执样例
│   ├── wechat-import-v031-examples.json # 当前排队、运行、完成状态样例
│   └── wechat-import-v03-examples.json # 主样例排队、运行、完成的查询结果
├── source-to-text/                   # 来源模块交给文本识别的请求
│   ├── request-v0.3.schema.json      # v0.3 请求 Schema，引用共享定义
│   ├── request-v0.3.1.schema.json    # 当前请求 Schema 源文件
│   ├── text-attachments-v031-request.json # 当前文件引用及上下文输入
│   ├── text-attachments-v03-request.json # 无正文图片与前文截止要求的合成输入
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

- 教学网爬虫 `--format=teaching-v04` 按 #124 原版请求 Schema 输出独立正文和逐引用证据；当前学期适用性未知，账号与完整 entry 授信由 server 核验。
- 教学网采集的 v0.2 开放 hints 扩展见 [来源快照约定](docs/teaching-source-snapshot-v0.2.md)，仅记录读取事实，不改变审核、学期授信或 v0.3.1 文件强制审核规则。
- #112 [定向重识别业务契约](docs/reprocessing-v0.1.md) 提供选择旧记录、运行/停止、持久进度和用户决定关联的共享校验；供 server/UI 显式实现，不表示路由已开放，也不改变 v0.2 模型信封。

- 业务排程与重新规划以 [规划接口约定](task-planning/README.md) 和 [请求 / 提案样例](task-planning/api-examples.json) 为准；确认后仅替换选中任务尚未开始的安排。
- AI 拆解接口以 [拆解契约](task-decomposition/README.md)、[Schema](task-decomposition/schema-v0.1.json) 和 [样例](task-decomposition/examples-v0.1.json) 为准；内部模型草稿由后端包装成带任务编号的公开回应，确认须再核对任务编号。
- 教学网 #108/#109 的 [v0.4 批次契约](docs/teaching-v0.4.md) 复用已审查的 [覆盖/学期政策组件](docs/teaching-review-v0.4-draft.md)；新结果显式标记 model-v1，由模型分类，消费端仅校验格式/证据/身份和一致性；旧结果可读，不改变以下 v0.2 或微信 v0.3.1 规则。

- #110 [已提交状态决策契约](docs/submission-policy-v0.1.md) 区分来源事实和永久行动对应，提供纯校验与参考决策；后端显式接入后才生效，不凭模型或 imported hints 自动完成。
- #110 [公开操作契约](docs/submission-operations-v0.1.md) 提供来源查询、人工关联、重开、恢复自动完成和来源确认的独立根；运行端仍须实现登录鉴权、持久幂等和事务。暂停候选可恢复自动处理或纠正关联，转成任务时继承暂停；重识别按持久操作审计保护人工决定，仅未人工处理的系统首次撤下可替换。

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

# 文本识别模块

```text
ddl_text/
├── README.md             # 模块用途、运行和检查入口
├── __init__.py           # Python 包入口
├── __main__.py           # CLI 参数、输入文件和输出
├── adapters.py           # 调用协议及离线回放适配器
├── chat_client.py        # 七牛兼容 API 请求和错误处理
├── classification.py    # F3 历史有效性过滤、待核实判断和过滤说明
├── codex_client.py       # 环境配置读取及可选 Codex 适配器
├── config.py             # 超时、重试和运行参数校验
├── contract_v02.py       # 前后端 v0.2 契约转换和校验
├── contracts.py          # 输入及候选任务的基础校验
├── dates.py              # 合并时间提案、催促语和冲突处理
├── draft_schema.py       # 模型草稿的 JSON Schema
├── extraction.py         # 加载规则、校验原文证据、生成候选
├── http_server.py        # 本机 /recognize 和 /health 服务
├── image_reader.py       # JPG/PNG 字节核对、解码与元数据清理
├── media_pipeline.py     # 供后端显式调用的 v0.3.1 图片与文字块识别入口
├── media_schema.py       # 从共享契约派生模型草稿并校验
├── media_worker.py       # 图片解码与模型调用的限时子进程
├── media_requirements.txt # 可选图片识别及契约校验依赖
├── pipeline.py           # 批量输入处理和结果汇总
├── provider.py           # 选择模型客户端
├── render.py             # 可选文字输出
├── runner.py             # 工作进程、超时和重试
├── time_validation.py    # 模型标准时间的日期、时区和基准校验
├── skills/               # 程序直接加载的识别规则，无须个人安装
│   ├── image-task-extraction/
│   │   └── SKILL.md      # 图片行动、日期、证据和质量判断规则
│   └── text-task-extraction/
│       ├── SKILL.md      # 任务、来源、日期及更新意图的识别规则
│       └── references/
│           ├── draft-schema.md # 草稿字段解释
│           ├── examples.md     # 识别正反例
│           └── time-sources.md # 日期规则的参考资料
├── tests/                # 离线单元测试及本机模拟服务测试
│   ├── fixtures/         # 模块内部回放数据，跨模块 v0.2 样例另见 contracts/
│   │   ├── text-contract-v0.1.json # 旧 CLI 的 17 个契约回归样例
│   │   ├── text-runner-config.json # CLI 运行参数样例
│   │   ├── text-stage-c-request.json # 阶段 C 合成输入
│   │   ├── text-stage-c-drafts.json # 阶段 C 离线模型草稿
│   │   ├── text-frontend-v02-drafts.json # 与共享 v0.2 输入配套的离线模型草稿
│   │   └── text-classification-drafts.json # 与共享 F3 分类样例配套的人工草稿
│   ├── __init__.py       # 测试包入口
│   ├── fake_adapters.py  # 可跨进程运行的模拟适配器
│   ├── media_cases.py    # 共享附件样例、合成图片与模型替身
│   ├── test_media_pipeline.py # 文件/证据/质量/超时与兼容验证
│   ├── test_media_transport.py # 本机模拟服务验证图片传输和重定向隔离
│   ├── run_media_smoke.py # 显式启用的非私人图片真实模型验证
│   ├── time_cases.py     # 时间语义的固定合成输入和人工期望值
│   ├── classification_cases.py # F3 分类的固定合成输入及人工期望值
│   ├── run_classification_smoke.py # 显式启用的真实模型分类验证入口
│   ├── test_classification.py # 分类、历史过滤、证据及跨进程透传测试
│   ├── run_time_smoke.py # 显式启用的真实模型时间验证入口
│   ├── test_chat_client.py      # API 协议、凭据隔离及错误测试
│   ├── test_codex_client.py     # 配置和可选 Codex 适配测试
│   ├── test_extraction.py      # 草稿编译、时间合并和原文证据测试
│   ├── test_normalized_time.py # 标准时间草稿及基准的独立校验测试
│   ├── test_frontend_contract.py # v0.2 契约及本机 HTTP 测试
│   ├── test_output_validation.py # 非法模型输出测试
│   ├── test_pipeline.py        # 批量处理、超时和重试测试
│   └── test_render.py          # 文字输出测试
└── docs/                 # 当前配置说明与历史归档
    ├── api-setup.md      # 供应商配置及故障定位
    ├── image-input.md    # v0.3.1 图片入口、受限读取回调和预算说明
    └── archive/          # 带日期的阶段记录、旧设计及合成验证结果
        ├── 2026-10-06-issue94-images.json # 最初五组生成图片的真实模型输入、草稿与输出
        └── 2026-10-06-issue118-year-review.json # 修复多余补年理由后的同图真实模型验证
```

接收预处理文本及上下文，输出带来源证据、日期状态及错误信息的 JSON 候选。HTTP 使用 v0.2，CLI 兼容 v0.1/v0.2，默认 JSON，可选 `--format text`。F1 手动输入仍需用户确认；F3 外部来源按下表分类，由账户后端决定入库，文本模块不负责采集、定时同步或写入正式任务。

新增可选 [v0.3.1 图片识别入口](docs/image-input.md)：支持静态 JPG/PNG 和已有文字块，正式 HTTP/CLI 路由仍为旧版本；后端需接账号受限的文件读取器后使用，PDF/DOCX 尚未开放，图片证据依正式契约保留待审核。

## 输入输出与规则

- [共享接口协议](../docs/backend-harness-alignment.md)：v0.2 输入输出及确认流程。
- [输入样例](../contracts/source-to-text/text-frontend-v02-request.json)、[离线草稿](tests/fixtures/text-frontend-v02-drafts.json)、[输出样例](../contracts/text-to-app/text-frontend-v02-output.json)；交接方向与文件说明见 [contracts](../contracts/README.md)。
- [识别规则](skills/text-task-extraction/SKILL.md)：行动要求、来源和日期判断；未知、推断、冲突的时间保留不确定性，参考时间不直接当作明确截止时间。

模型按语义直接生成标准时间及原文证据，程序校验 ISO 值、日历、时区与基准，不再按原话格式白名单决定是否接受。内部草稿新增 `times[].normalized`，旧草稿需迁移；对外 v0.2 不变。结构校验不能证明语义一定正确；农历、校历和节假日等缺少依据时仍需核实。

单纯省略年份的明确截止按最近有效年份补全，通过校验后输出 explicit，不单独触发待核实；原话和补年依据仍保留。缺基准、冲突、活动参考时间或其他独立疑点仍需核实。共享样例见 [补年输出](../contracts/text-to-app/text-year-inference-output.json)。

### F3 分类对接

| 识别结果 | 已有 v0.2 表达 | 消费方处理要求 |
| --- | --- | --- |
| 明确新增行动 | create、needs_review=false、截止 explicit 或 not_provided | 可自动新增；无日期保存 due=null，不能补造期限。 |
| 适用对象不明、时间冲突、近期逾期、来源新旧不明 | needs_review=true，保留具体 review_reasons | 人工核对，不能只凭日期存在就自动入库。 |
| 修改、延期、取消 | update/postpone/cancel、needs_review=true | 核对原任务，不能自动修改或删除。 |
| 普通资讯 | tasks=[]，warnings 中 no_action 说明 | 不生成待办。 |
| 明确历史行动 | 不输出该候选，warnings 中 history_filtered 说明及原文引用 | 不进入待办或待核实区；原记录及过滤依据仍可查。 |

历史过滤仅用于 v0.2 的 teaching_web/wechat/email/web 输入；unknown 来源及 v0.1 保持旧流程。模型在内部草稿的 relevance 中提供 current/ongoing/historical/uncertain 判断和逐字证据，程序验证后执行；对外不增加字段。支持读取缺少 relevance 的旧草稿回放。单条输入中历史行动与新行动分别处理，调用失败仍返回错误，不能冒充普通资讯。

以输入 collected_at 为本次判断时点，明确截止超过 30 天且没有继续执行要求才按过期历史排除（集中常量 `HISTORY_GRACE_DAYS`）；近期逾期保留待核实。日期精确到天时当天不过期。旧通知有未来截止、补交/仍需完成要求或变更通知时保留。只有旧发布时间而没有确定期限时不自动删除；已结束的行动可凭语义证据排除。缺少采集时点时不调用系统时钟判过期。

外部通知缺原始发布时间、仅按采集时间补年时标注来源新旧待核实；可信发布时间支持的正常补年不需要确认。结构和引文校验不能证明语义必然正确，没有可靠历史证据时保留行动。

自动入库必须由消费者同时检查真实外部来源、notice_type、needs_review/review_reasons 和期限状态；手动输入、demo、reference/inferred/unknown/conflict 不可自动入库。文本侧不会绕过这些标志主动保存。

规则只作用于新识别请求，不回溯删除已保存任务或缓存候选；旧批次需由后端重新识别或由用户处理，不能假定其已经过历史过滤。当前仅记录级采集时点参与判断，不代替日历中的实时逾期状态。

示例效果见 [F3 共享输入/输出](../contracts/text-to-app/text-classification-v02.json) 和 [真实模型合成测试输出](docs/archive/2026-09-30-issue68-classification-final.json)；后者保留原始草稿、标准 JSON、人工期望与耗时，不代表独立召回率评测。补交通知缺少用户任务上下文时，新增补交行动或更新原任务均可保留待核实，但不能沿用已失效的旧截止作为新期限。

## 运行与测试

以下命令均在**仓库根目录**执行。需要 Python 3.9+，模块使用标准库；Windows 缺少时区数据库时安装 `tzdata`。

图片入口和新增图片测试另需 Python 3.10+，先运行 `python -m pip install -r ddl_text/media_requirements.txt`；旧 v0.2 运行入口不会自动加载这些依赖。

```powershell
# 人工草稿回放：不访问模型，不代表真实模型识别成功
python -m ddl_text --input contracts/source-to-text/text-frontend-v02-request.json --drafts ddl_text/tests/fixtures/text-frontend-v02-drafts.json
# 全部文本测试；-t . 保持测试包及子进程导入路径一致
python -m unittest discover -s ddl_text/tests -t . -q
python scripts/check_text_contract_examples.py
# 临时数据库内的后端集成检查，默认离线
python scripts/check_frontend_integration.py --frontend-dir .
```

回放样例应包含加群（参考时间）、学习计划（无日期）和实验报告（明确截止）三个候选。测试包含本机模拟 HTTP；可选 Codex 检查未启用时跳过。

真实调用前按 [API 配置](docs/api-setup.md) 配置根目录 `.env`。真实识别、服务收到识别请求及 `--live` 回归会调用供应商，可能计费：

```powershell
python -m ddl_text --check-connection
python -m ddl_text --mode real --input contracts/source-to-text/text-frontend-v02-request.json
python -m ddl_text.http_server --real
# 时间语义回归：20条固定合成通知，保留模型草稿和输出；输出文件须不存在
python -m ddl_text.tests.run_time_smoke --live --output local-data/time-smoke-new.json
# F3 分类回归：14 条固定合成通知，保留模型草稿及过滤说明
python -m ddl_text.tests.run_classification_smoke --live --output local-data/classification-smoke-new.json
```

账户后端默认直接调用 `process_request`，读取根目录 `.env`，无需单独启动识别服务；网页与后端启动方式见 [frontend/README.md](../frontend/README.md)。Windows 后端直调的真实合成结果见 [验证 JSON](docs/archive/2026-10-01-issue69-backend-direct.json)。
独立识别服务仍可选用：默认 `127.0.0.1:1333`，健康检查为 `/health`；在账户后端终端导出 `TEXT_HARNESS_URL=http://127.0.0.1:1333/recognize` 即可切换。更改环境变量后重启相应后端或服务；配置检查及健康检查不证明模型权限或额度可用。

## 维护与历史记录

修改规则后先运行上述离线检查；需要真实回归时显式执行 `python scripts/run_text_live_smoke.py --live --output local-data/live-regression-new.json`，输出路径不得已存在。使用合成通知，记录提交、模型、错误码及耗时；密钥、私人原文和本机数据库不提交仓库。

当前文档基于原交付说明整理。旧需求、阶段 A–E 记录及旧 API 说明保存在 [archive](docs/archive/)；以 `2026-09-26-` 开头的文件日期表示归档日期，文件内部日期才是原记录时间。历史验证文件保留原日期，不能视为当前提交的新测试结果。

# 文本识别模块

```text
ddl_text/
├── README.md             # 模块用途、运行和检查入口
├── __init__.py           # Python 包入口
├── __main__.py           # CLI 参数、输入文件和输出
├── adapters.py           # 调用协议及离线回放适配器
├── chat_client.py        # 七牛兼容 API 请求和错误处理
├── codex_client.py       # 环境配置读取及可选 Codex 适配器
├── config.py             # 超时、重试和运行参数校验
├── contract_v02.py       # 前后端 v0.2 契约转换和校验
├── contracts.py          # 输入及候选任务的基础校验
├── dates.py              # 日期解析、推断和不确定性处理
├── draft_schema.py       # 模型草稿的 JSON Schema
├── extraction.py         # 加载规则、校验原文证据、生成候选
├── http_server.py        # 本机 /recognize 和 /health 服务
├── pipeline.py           # 批量输入处理和结果汇总
├── provider.py           # 选择模型客户端
├── render.py             # 可选文字输出
├── runner.py             # 工作进程、超时和重试
├── skills/               # 程序直接加载的识别规则，无须个人安装
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
│   │   └── text-frontend-v02-drafts.json # 与共享 v0.2 输入配套的离线模型草稿
│   ├── __init__.py       # 测试包入口
│   ├── fake_adapters.py  # 可跨进程运行的模拟适配器
│   ├── test_chat_client.py      # API 协议、凭据隔离及错误测试
│   ├── test_codex_client.py     # 配置和可选 Codex 适配测试
│   ├── test_extraction.py      # 规则、日期和原文证据测试
│   ├── test_frontend_contract.py # v0.2 契约及本机 HTTP 测试
│   ├── test_output_validation.py # 非法模型输出测试
│   ├── test_pipeline.py        # 批量处理、超时和重试测试
│   └── test_render.py          # 文字输出测试
└── docs/                 # 当前配置说明与历史归档
    ├── api-setup.md      # 供应商配置及故障定位
    └── archive/          # 带日期的阶段记录、旧设计及合成验证结果
```

接收预处理文本及上下文，输出带来源证据、日期状态及错误信息的 JSON 候选。HTTP 使用 v0.2，CLI 兼容 v0.1/v0.2，默认 JSON，可选 `--format text`。候选需核对后由账户后端保存，文本模块不负责采集、定时同步或自动修改正式任务。

## 输入输出与规则

- [共享接口协议](../docs/backend-harness-alignment.md)：v0.2 输入输出及确认流程。
- [输入样例](../contracts/source-to-text/text-frontend-v02-request.json)、[离线草稿](tests/fixtures/text-frontend-v02-drafts.json)、[输出样例](../contracts/text-to-app/text-frontend-v02-output.json)；交接方向与文件说明见 [contracts](../contracts/README.md)。
- [识别规则](skills/text-task-extraction/SKILL.md)：行动要求、来源和日期判断；未知、推断、冲突的时间保留不确定性，参考时间不直接当作明确截止时间。

明确时刻支持中英文括号及 `HH:mm:ss`（例如 `2026.10.11（23:59:00）`）；秒数和原文证据保留，无效日期、未知时区仍待核实，参考时间不升级为截止时间。

## 运行与测试

以下命令均在**仓库根目录**执行。需要 Python 3.9+，模块使用标准库；Windows 缺少时区数据库时安装 `tzdata`。

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

真实调用前按 [API 配置](docs/api-setup.md) 配置根目录 `.env`。以下第二、三条会调用供应商，可能计费：

```powershell
python -m ddl_text --check-connection
python -m ddl_text --mode real --input contracts/source-to-text/text-frontend-v02-request.json
python -m ddl_text.http_server --real
```

识别服务默认 `127.0.0.1:1333`，健康检查为 `/health`。账户后端设置 `TEXT_HARNESS_URL=http://127.0.0.1:1333/recognize`；网页与账户服务的启动方式由其模块说明维护。更改环境配置后重启识别服务。配置检查及健康检查不证明模型权限或额度可用。

## 维护与历史记录

修改规则后先运行上述离线检查；需要真实回归时显式执行 `python scripts/run_text_live_smoke.py --live --output local-data/live-regression-new.json`，输出路径不得已存在。使用合成通知，记录提交、模型、错误码及耗时；密钥、私人原文和本机数据库不提交仓库。

当前文档基于原交付说明整理。旧需求、阶段 A–E 记录及旧 API 说明保存在 [archive](docs/archive/)；以 `2026-09-26-` 开头的文件日期表示归档日期，文件内部日期才是原记录时间。历史验证文件保留原日期，不能视为当前提交的新测试结果。

# 文本识别模块：交付使用说明

此文面向接手或对接的队友。当前完成“预处理文本及上下文 → 模型识别 → 校验 → JSON 候选输出”，可从 CLI 或本地 HTTP 使用。识别结果经用户核对后，由账户后端保存为正式任务。

## 先看哪几个文件

| 入口 | 内容 |
| --- | --- |
| [协作协议](backend-harness-alignment.md) | 前端使用的 v0.2 输入、输出和确认流程；当前接口基线 |
| [输入 JSON](examples/text-frontend-v02-request.json) | 教学网与邮箱合成通知；原文、发布时间、平台、来源及上下文字段 |
| [离线输出 JSON](examples/text-frontend-v02-output.json) | 三个候选，分别展示参考时间、无日期和明确截止 |
| [七牛真实输出 JSON](validation/qiniu-frontend-live-2026-09-25.json) | `result` 为识别返回对象，另含合成输入和验证信息 |
| [API 配置](text-api-setup.md) | 密钥、供应商切换、失败排查 |
| [阶段 E 验收](text-stage-e-review.md) | 回归结果、网页检查和剩余边界 |

v0.1 文档和样例继续兼容旧 CLI；HTTP 与前端统一使用 v0.2。CLI 默认 JSON，`--format text` 可查看文字版。

## 文件用途

| 文件/目录 | 职责 |
| --- | --- |
| `ddl_text/__main__.py` | CLI 参数、输入文件和输出 |
| `ddl_text/contracts.py`、`contract_v02.py` | 输入规范化和版本化校验 |
| `ddl_text/pipeline.py`、`runner.py` | 批量处理、状态、超时、失败与重试 |
| `ddl_text/provider.py`、`chat_client.py` | 选择客户端，七牛直接 API 调用 |
| `ddl_text/codex_client.py` | `.env` 配置读取及保留的 DeepSeek + Codex 适配器 |
| `ddl_text/extraction.py`、`draft_schema.py` | 加载共享规则、草稿格式及来源证据核对 |
| `ddl_text/dates.py` | 相对日期、精度、冲突与不确定性处理 |
| `ddl_text/http_server.py` | 本机 `/recognize` 和 `/health` 接口 |
| `skills/text-task-extraction/` | 识别规则与正反例；程序显式加载，不要求安装到个人 Codex |
| `tests/` | 文本模块离线及本机模拟测试 |
| `scripts/check_text_contract_examples.py` | 检查 17 个合成契约样例的一致性 |
| `scripts/run_text_live_smoke.py` | 明确传入 `--live` 后运行固定真实模型场景，可能计费 |
| `scripts/check_frontend_integration.py` | 临时数据库中的账户后端/识别服务集成检查，默认离线 |
| `server/`、`frontend/` | 团队账户后端与前端，负责最终确认、存储和界面 |

## 1. 不填密钥也能运行

需要 Python 3.9+；文本模块使用标准库。Windows 如缺 IANA 时区数据库，安装 `tzdata`。在仓库根目录执行：

```powershell
python -m ddl_text --input docs/examples/text-frontend-v02-request.json --drafts docs/examples/text-frontend-v02-drafts.json
python -m unittest discover -s tests -q
python scripts/check_text_contract_examples.py
```

第一条使用人工草稿，真实执行校验、日期处理和 JSON 生成，不调用模型。预期三个任务：加群（参考时间）、学习计划（无日期）、实验报告（明确 DDL）。`--demo`、`--drafts`、`--replay` 都是离线方式，不能用于声称模型识别成功。

## 2. 连接七牛真实模型

首次复制模板；已有 `.env` 则保留：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 填写 `QINIU_API_KEY`。模板已有 `DDL_PROVIDER=qiniu`、`DDL_MODEL=deepseek/deepseek-v4.1-flash`、`DDL_ENDPOINT=https://api.qnaigc.com/v1`。不需要安装 Codex。

```powershell
# 仅检查配置，不访问模型
python -m ddl_text --check-connection
# 使用合成通知进行真实调用，可能计费
python -m ddl_text --mode real --input docs/examples/text-frontend-v02-request.json
```

不同 `.env` 可通过 `--env-file` 指定。系统同名环境变量优先；更换密钥后重启识别服务。密钥不写入 JSON 输入、浏览器、Git 或共享报告。

## 3. 在网页演示

需要 Node.js 20.19+。首次在 `frontend/` 执行 `npm ci`。之后打开三个终端，以下命令均从仓库根目录开始：

终端一，识别服务：

```powershell
python -m ddl_text.http_server --real
```

终端二，隔离的演示数据库与账户后端：

```powershell
New-Item -ItemType Directory -Force local-data | Out-Null
$env:NIUMA_DB = Join-Path (Get-Location) 'local-data/text-demo.sqlite3'
$env:TEXT_HARNESS_URL = 'http://127.0.0.1:1333/recognize'
python server/app.py --seed
python server/app.py
```

终端三，网页：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:1331/`，使用开发演示账号 `demo@niuma.test` / `Niuma-Test-2026!` 登录。进入“待确认”，粘贴：

```text
【阶段E合成验收】请于2026年9月30日18:00前提交阶段E演示报告。另外，请加入阶段E讨论群，通知没有说明加群截止时间。
```

点击“识别为候选”，检查两项任务。报告应显示明确截止，加群应显示无日期。分别打开核对表单，核对原文后勾选确认并保存；在任务列表搜索“阶段E”，应看到一项有 DDL、一项无 DDL。只有人工确认后才新增正式任务。

网页输入日期为空时，不用今天推算“明天”；手填发布时间标为待核实来源。来源完整元数据和上下文由上游通过 v0.2 JSON 传递。前端表单不是完整采集器。

## 4. 团队回归与评审演示

普通回归不调用付费 API：

```powershell
python -m unittest discover -s tests -q
python scripts/check_text_contract_examples.py
python scripts/check_frontend_integration.py --frontend-dir .
cd frontend
npm test
npm run build
```

更改供应商、规则或模型后，如需真实固定回归，从仓库根目录执行（每个场景会请求模型，报告路径不得已存在）：

```powershell
python scripts/run_text_live_smoke.py --live --output local-data/live-regression-new.json
python scripts/check_frontend_integration.py --frontend-dir . --live --output local-data/frontend-live-new.json
```

会议演示顺序：先展示输入 JSON 和输出；演示网页核对与确认；指出参考时间/未知日期不会自动当 DDL；展示失败记录与成功/无任务的区别；最后说明测试结论和范围限制。已有真实记录可直接用于说明结果，无需每次开会重复收费调用。

## 5. 错误处理与维护

| 现象 | 处理 |
| --- | --- |
| 配置检查显示缺密钥 | 检查 `QINIU_API_KEY` 与系统环境变量；不要填在 `DEEPSEEK_API_KEY` 下 |
| `agent_unavailable` | 检查供应商、模型权限、额度和网络；错误正文已脱敏 |
| `agent_timeout` | 先用短通知检查；HTTP 整批最多 20 秒，CLI 默认单条 120 秒；失败不是无任务，人工重试可能再次计费 |
| `invalid_agent_output` | 模型返回格式或原文证据不符；保留错误记录，用获准的最小合成例定位 |
| 日期为 unknown / conflict | 保留候选，让用户查看原通知并核实；不要在上游补造年份或默认时刻 |
| 网页未接入/端口占用 | 检查三个服务、`TEXT_HARNESS_URL` 与端口 1331/1332/1333；`/health` 仅验证识别服务存活 |

问题复现时记录代码提交、规则摘要、模型 ID、合成输入、错误码和耗时。私人原文、Key、演示数据库和本机日志不提交仓库。新规则先跑离线和固定真实场景，再评审合并；遇到供应商波动不反复批量重试。

当前交付是本地开发与联调方案。实际采集、定时同步、生产部署、独立召回率评测及 PDF/图像不在本轮交付内；完整限制和后续事项见[阶段 E 验收记录](text-stage-e-review.md)。

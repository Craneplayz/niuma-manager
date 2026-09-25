# 文本识别 API 配置与测试

当前推荐使用课程提供的七牛 MaaS API，直接调用 DeepSeek V4.1 Flash，无需安装 Codex 或额外 Python SDK。程序继续加载同一份识别 skill，校验原文证据和日期，并输出原来的 v0.1 / v0.2 JSON。

## 现在只需要填密钥

本机仓库根目录 `.env` 的非敏感配置已准备好，只需填写 `QINIU_API_KEY`。换电脑时复制 `.env.example` 为 `.env`：

```dotenv
DDL_PROVIDER=qiniu
QINIU_API_KEY=在这里填写课程提供的完整密钥
DDL_MODEL=deepseek/deepseek-v4.1-flash
DDL_ENDPOINT=https://api.qnaigc.com/v1
```

旧 `DEEPSEEK_API_KEY` 可以保留，七牛模式不会读取它，也不会因七牛密钥为空而退回旧密钥。密钥不要填入前端或任何 `VITE_*` 配置。`.env` 已被 Git 忽略。

先在仓库目录检查配置，**不联网、不计费**：

```text
python -m ddl_text --check-connection
```

显示“七牛 API 直连；配置格式：通过；密钥：已配置”只表示配置齐全，不证明密钥有效、具有模型权限或有额度。缺密钥退出码为 1，配置错误为 2。

填写后，可用以下合成输入验证真实调用（会访问七牛 API，可能计费）：

```text
python -m ddl_text --mode real --input docs/examples/text-frontend-v02-request.json
```

样例包含加群、提交学习计划和实验报告三个候选。加群时间应为参考时间，学习计划没有明确日期，实验报告有明确截止时间；模型文字表达可能不同。CLI 默认输出 JSON，添加 `--format text` 可查看文字版。没有有效模型结果时会明确失败，不回放预置答案。

前端服务启动方式仍是 `python -m ddl_text.http_server --real`，详见[前端对接说明](text-frontend-integration.md)。**更改 `.env` 后要重新启动识别服务**；已运行的进程不会自动刷新密钥或供应商。CLI 每次启动重新读取配置。

## 两种接入方式

| 配置 | 七牛直连（推荐） | 原 DeepSeek + Codex |
| --- | --- | --- |
| `DDL_PROVIDER` | `qiniu` | `deepseek` |
| 密钥变量 | `QINIU_API_KEY` | `DEEPSEEK_API_KEY` |
| `DDL_MODEL` | `deepseek/deepseek-v4.1-flash` | `deepseek-flash` |
| `DDL_ENDPOINT` | `https://api.qnaigc.com/v1` | `https://api.deepseek.com` |
| 协议 | Chat Completions | Responses，经 Codex CLI |
| Codex 安装 | 不需要 | 需要；`DDL_CODEX_BIN` 可指定路径，否则从 PATH 查找 |

旧 `.env` 未配置 `DDL_PROVIDER` 时继续使用 DeepSeek，以保持兼容。切换供应商时同时切换模型和地址；程序会拒绝明显混用的官方域名。JSON `--config` 覆盖地址时也检查这一点。七牛地址应止于 `/v1`，不要再填写 `/chat/completions`。

`.env` 按字面读取，支持整行注释和引号，不执行命令、展开变量或接受行尾注释；只接受模板中的已知字段。同名系统环境变量优先于 `.env`。前端后端的 `HARNESS_*`、`TEXT_HARNESS_*` 和 `NIUMA_DB` 字段可共存，识别模块忽略它们；前端后端仍须自行导出环境变量。

## 运行边界

- 七牛模式：规则放在 system 消息，单条通知及其上下文放在 user 消息；请求 JSON Output，并附带草稿 schema。程序随后严格校验草稿和来源证据。JSON Output 本身不保证语义正确。
- 不开放工具调用、自动网页访问或历史会话。单次输出上限为 8192 tokens；截断、工具调用和空回复均按失败处理。
- 密钥仅用于服务端请求头。错误显示固定分类，不记录正文、密钥或供应商错误正文；拒绝 HTTP 重定向，避免携带凭据转发。
- CLI 默认单条 120 秒、0 次重试；`--config` 可调整，最多 300 秒和 2 次重试。调用由独立工作进程执行，超时后回收进程及连接。401 等认证错误不重试；429 / 5xx 可按配置重试。
- HTTP 批量仍共用 20 秒预算，账户后端等待 25 秒、网页等待 30 秒。更换七牛是否改善延迟尚待真实测试。超时不意味着供应商不计费。
- DeepSeek + Codex 模式仍使用临时独立配置、空工作目录和禁用工具的执行方式，不修改个人 Codex 设置。

## 文件与检查

| 文件 | 用途 |
| --- | --- |
| `ddl_text/chat_client.py` | 七牛兼容的直接 API 调用、JSON 请求和错误处理 |
| `ddl_text/provider.py` | 按供应商选择客户端 |
| `ddl_text/codex_client.py` | 共用 `.env` 读取、供应商配置，以及原 Codex 客户端 |
| `ddl_text/extraction.py` | 加载 skill、校验证据与草稿、生成任务 |
| `ddl_text/runner.py` | 工作进程、超时和重试边界 |
| `tests/test_chat_client.py` | 模拟 HTTP、密钥隔离、失败和 v0.2 输出测试 |

离线检查，不会调用付费模型：

```text
python -m unittest discover -s tests -q
python scripts/check_text_contract_examples.py
python scripts/check_frontend_integration.py --frontend-dir .
```

测试强制清空两种供应商的真实模式密钥。可选 Codex 本机检查仍通过 `DDL_TEST_CODEX` 开启，仅请求本机模拟接口。合成真实测试脚本 `scripts/run_text_live_smoke.py` 和前端联调脚本的 `--live` 均支持当前供应商；七牛结果报告不包含 Codex 版本或模型中间草稿。

## 文档依据与验证状态（2026-09-25）

- [七牛 API 接入说明](https://developer.qiniu.com/aitokenapi/13379/real-time-ai-interface-api)提供 `https://api.qnaigc.com/v1` 和 Chat Completions 调用方式。
- [七牛模型广场](https://www.qiniu.com/ai/models)列出 DeepSeek V4.1 Flash；本次实际读取[公开模型目录](https://api.qnaigc.com/v1/market/models)，确认 ID 为 `deepseek/deepseek-v4.1-flash`，别名为 `deepseek-v4.1-flash`，并标记支持结构化输出。公开目录不证明课程 Key 的权限。
- [Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)当前仅支持 Responses；[七牛配置工具](https://github.com/qiniu/coding-helper)的 Codex 配置维护 GPT 模型目录。因此按用户最新选择直接调用七牛，不引入协议转换层。

本次检查结果：88 项单元/集成测试中 87 项通过、1 项可选 Codex 检查跳过；17 个契约样例及前端离线联调通过。本机配置检查确认七牛地址、模型和配置格式正确，密钥尚未填写。

本次只完成离线和本机模拟验证，未使用课程密钥发起真实推理。2026-09-24 的 DeepSeek 直连测试属于旧供应商记录，不能当作七牛验收结果。密钥填写后需完成一次合成输入验证，再判断课程账户权限和延迟是否满足前端要求。

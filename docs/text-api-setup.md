# 阶段 D：填写密钥并验证 API

当前已实现 DeepSeek → Codex CLI → 草稿编译/校验 → 文本或 JSON 输出。已在本机模拟 Responses 服务上跑通真实 Codex CLI，尚未调用 DeepSeek 公共服务；填入密钥后才能确认账号权限和真实模型效果。

## 你现在需要做什么

仓库根目录的 `.env` 已为本机准备好（若原来已有则不覆盖）。打开该文件，只填写：

```dotenv
DEEPSEEK_API_KEY=你的密钥
```

其他默认配置是 `DDL_MODEL=deepseek-flash`、`DDL_ENDPOINT=https://api.deepseek.com`。`DDL_CODEX_BIN` 可以填写本机 Codex 可执行文件的绝对路径；为空时从 PATH 查找。Windows 需要 `codex.exe`，不能填 `.cmd` 包装文件。换电脑时复制 `.env.example` 为 `.env` 并补齐密钥和必要路径。

在仓库目录运行以下命令进行本地检查，**不调用模型、不计费**：

```text
python -m ddl_text --check-connection
```

出现“Codex CLI：可用；配置格式：通过；密钥：已配置”只表示本地准备通过，不证明密钥有效。缺密钥时返回退出码 1；配置或 CLI 无法读取时为 2。检查不会显示密钥。

然后用仓库中的合成通知做第一次真实调用（此步骤会请求 API，可能计费）：

```text
python -m ddl_text --mode real --input docs/examples/text-stage-c-request.json
```

如需给前端或其他程序使用，增加 `--format json`。该例预期包含“加入对应小班群”和“提交学习计划”；前者参考时间为 2026-09-16 18:40，后者日期未知。真实模型的文字表达可以不同，需要检查是否漏任务或把参考时间误标为明确截止。

不要加 `--drafts`、`--replay` 或 `--demo`，这些是离线入口。初次只运行合成样例；私人通知仍按团队的数据权限约束处理。密钥留在本地，不发到聊天或 GitHub。

## 配置和运行方式

- `.env` 仅允许模板中的四个键，按字面读取，不执行命令、不展开变量。支持整行注释、可选引号，不支持行尾注释或多行值。
- 同名环境变量优先于 `.env`；JSON `--config` 中的模型、地址和超时优先于默认配置。真实模式默认单条超时 120 秒、重试 0 次；可按原 Settings 约定调整，最多重试 2 次。服务端内部仍可能消耗已发出的请求，超时不等于免计费。
- 地址必须是 HTTPS 且不能夹带凭据、查询参数或片段；本机模拟测试使用内部客户端，不开放 HTTP 给正式 CLI。
- 每次请求创建临时 Codex home 和空工作目录。共享 skill 内容通过 `model_instructions_file` 传入，正文走标准输入，密钥只进入子进程环境。不会切换你日常 Codex 的模型或修改个人配置。
- 禁用命令、文件修改、搜索、插件、子代理及交互提问等工具。当前版本的本机模拟测试已检查实际发出的 `tools=[]`、规则与正文分离。Codex 升级后应重跑此检查。
- 无历史会话复用；禁用历史保存、反馈和分析。退出时清理临时目录和返回文件，错误只显示固定分类，不输出底层含敏感信息的日志。操作系统强制终止 Python 时仍可能留下临时文件，需要本机管理；这不是防取证存储。
- 没有有效输出、格式错误、日期/证据不合法均按失败处理，不自动回放样例。未知课程、日期或归属按既有规则标待核实。

## 常见结果

| 现象 | 检查项 |
| --- | --- |
| 无法读取或解析配置 | `.env` 键名/引号、Codex 路径、JSON 格式、CLI 版本 |
| 密钥未填写 | `.env` 的值是否为空；是否被空环境变量覆盖 |
| Agent 不可用 | 密钥权限、余额、网络、地址或 Codex 启动；为避免泄露正文，不直接展示底层原始错误 |
| Agent 调用超时 | 默认 120 秒；先减小输入，再检查服务响应；不会把部分草稿当成功 |
| Agent 结果未通过校验 | 模型没有返回协议 JSON，或证据/前置步骤等不符合原文；进入后续提示词和真实质量评测 |

## 文件和测试

`ddl_text/codex_client.py` 实现密钥读取、配置检查、独立 Codex 调用及清理。`__main__.py` 选择真实入口；`runner.py` 让此适配器自行管理 Codex 生命周期，避免外层进程被杀后留下计费中的子进程。`.env.example` 可提交，`.env` 已被 Git 忽略。

普通离线回归：

```text
python -m unittest discover -s tests -q
python scripts/check_text_contract_examples.py
```

开发者可以额外运行真实 CLI + 本机模拟服务检查，不使用密钥、没有公网模型请求（PowerShell）：

```powershell
$env:DDL_TEST_CODEX = (Get-Command codex).Source
python -m unittest tests.test_codex_client -v
Remove-Item Env:DDL_TEST_CODEX
```

当前验证环境：Windows、Python 3.9.12、Codex CLI 0.155.0-alpha.16.3。开启本机联调检查后共 67 项测试、17 个契约样例通过；未开启时一项本机联调测试跳过。回归测试强制清空真实 CLI 路径上的密钥，不会因开发者填写 `.env` 而触发付费调用。本机模拟返回人工草稿，不是模型识别效果。规则内容已实际送入 Codex 的模型请求，但没有验证 Codex 自动发现/触发 skill；此项目采用程序显式加载同一份 skill 内容。

配置依据：[Codex 官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[DeepSeek Responses 指南](https://api-docs.deepseek.com/guides/responses_api/)。真实质量、耗时、费用及公共服务连通性仍待填入密钥后验收。

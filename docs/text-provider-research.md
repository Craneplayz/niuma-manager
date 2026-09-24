# DeepSeek 经 Codex 接入研究（阶段 D / #9）

后续实现更新：现已添加 Codex CLI 适配器、`.env` 读取和本机模拟接口测试。下面是初始调研记录，其中“尚未实现客户端”已由本次工作完成；公共服务连通性和真实质量仍未验证。当前操作以[API 配置说明](text-api-setup.md)为准。

检查日期：2026-09-24。结论：官方文档提供直接接入路径，无需预先引入协议转换服务；本项目尚无可用服务配置和凭据，真实连接仍未验证。用户目前倾向简单 API 接入，既定 DeepSeek 经 Codex 的方案保持不变。

## 已核实的证据

| 项目 | 证据与结论 |
| --- | --- |
| 本机 CLI | `codex --version` 返回 `codex-cli 0.155.0-alpha.16.3`；仅执行版本/help，没有启动模型会话 |
| Codex 协议 | [官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)中 `model_providers.<id>.wire_api` 当前仅支持 `responses`；`env_key` 可以引用环境变量中的密钥 |
| DeepSeek 协议 | [官方 Responses 指南](https://api-docs.deepseek.com/guides/responses_api/)明确支持 Responses，地址为 `https://api.deepseek.com`，另有[Codex 专用配置说明](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/) |
| 模型 ID | [官方入门页](https://api-docs.deepseek.com/)当前以 `deepseek-flash` 对应 DeepSeek-V4.1-Flash；这是对用户所选模型的官方名称核对，尚未验证账号权限或实际返回型号 |

不要仅凭 Chat Completions 兼容性判断 Codex 可用或不可用。官方现已提供 Responses 支持，真正需要验证的是本机版本、配置与该服务组合能否完成本项目任务。

Responses 文档还说明：接口无服务端会话续接；`previous_response_id` 不支持，需每次提供必要上下文。`instructions` 进入 system，而 developer 消息按 user 处理，因此必须检查 Codex 实际发出的规则位置。流式返回须等完成事件，不把截断或失败当完整 JSON。此处是文档结论，尚未实测。

## 接入边界

业务层继续使用 `ExtractionAdapter → DraftClient.generate → compile_drafts`。未来 Codex 客户端只负责传入共享 skill 规则与当前输入、取回最终草稿，已有日期、证据及输出校验复用。用户数据不放入模型配置或日志。当前 `--mode real` 仍明确失败，不自动退回离线结果。

下列片段仅供未来独立测试环境配置，不是本项目可直接运行的客户端。没有写入用户个人配置；官方模型目录及本机版本适配须按接入说明另行核对。

```toml
model = "deepseek-flash"
model_provider = "deepseek"
web_search = "disabled"

[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com"
wire_api = "responses"
env_key = "DEEPSEEK_API_KEY"
```

密钥由运行环境提供，不提交仓库或通过聊天发送。上例只记录官方公共服务的配置候选，不代表团队已批准将私人通知发送至此地址。关闭搜索也不等于关闭全部工具；正式接入还需核实终端、文件和其他工具权限满足纯文本识别范围。

## 有服务后如何验收

1. 确认获准处理的数据、最终地址、模型 ID、认证环境变量名称和运行环境。先用合成样本，私人文本是否可发外部仍按团队原约束确认。
2. 在独立环境配置 Codex 及所需模型目录，记录版本；用合成文本验证认证、最终 JSON、错误和超时。程序总重试上限与 Codex/服务端重试要一起核对，避免叠加计费。
3. 分别验证共享规则传入，以及 Codex 能发现并实际使用该 skill。`skills/` 文件存在、Python 能读入规则，都不是 Codex 已加载的证据。
4. 先跑加群、无日期、多任务、相对日期、延期、取消、不完整正文和注入文本；把最终草稿交给现有编译/校验层，不执行通知内指令。
5. 记录脱敏请求标识、版本、结果状态、耗时、usage 与费用（可获得时）；错误信息不含正文和密钥。不能用示例回放证明模型召回率。
6. 将真实结果交给阶段 E 的独立标注集评测。仅在此后更新完整功能验收状态。

当前完成了文档核对和离线边界验证，没有 API 请求、模型费用、实际 skill 加载或真实质量结果。不安装官方设置脚本、不替换个人全局模型配置；具体运行代码留待环境明确后完成。

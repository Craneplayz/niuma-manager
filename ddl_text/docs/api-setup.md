# API 配置与排错

在仓库根目录首次复制 `.env.example` 为 `.env`，已有文件则保留。课程七牛接入配置：

```dotenv
DDL_PROVIDER=qiniu
QINIU_API_KEY=填写课程密钥
DDL_MODEL=deepseek/deepseek-v4.1-flash
DDL_ENDPOINT=https://api.qnaigc.com/v1
```

同名系统环境变量优先；CLI 可通过 `--env-file` 指定其他文件。`.env` 按字面读取，不执行命令、不展开变量、不支持行尾注释。密钥只供服务端使用，不填入 JSON、前端或 `VITE_*` 变量。修改后重启识别服务。

`python -m ddl_text --check-connection` 仅检查配置，不联网；真实运行与离线测试命令见 [模块说明](../README.md)。七牛模式直接调用 API，无须安装 Codex。缺少七牛密钥不会回退到 DeepSeek 密钥。

| 接入 | 七牛直连 | 保留的 DeepSeek + Codex 适配器 |
| --- | --- | --- |
| `DDL_PROVIDER` | `qiniu` | `deepseek` |
| 密钥变量 | `QINIU_API_KEY` | `DEEPSEEK_API_KEY` |
| `DDL_MODEL` | `deepseek/deepseek-v4.1-flash` | 按服务支持情况配置 |
| `DDL_ENDPOINT` | `https://api.qnaigc.com/v1` | 按服务支持情况配置 |
| 依赖 | Python 标准库 | 另需 Codex，可用 `DDL_CODEX_BIN` 指定 |

供应商、模型与地址须配套；七牛地址止于 `/v1`。未设置 `DDL_PROVIDER` 的旧配置默认 DeepSeek，此兼容行为不证明其模型和协议可用。

| 现象 | 处理 |
| --- | --- |
| 缺密钥或配置错误 | 核对对应密钥变量及系统环境变量；不通过日志打印密钥 |
| `agent_unavailable` | 检查供应商、网络、模型权限和额度 |
| `agent_timeout` | 使用短合成通知定位；CLI 默认单条 120 秒、0 次重试，HTTP 整批共用 90 秒；超时仍可能计费 |
| `invalid_agent_output` | 输出结构或证据未通过校验；用最小合成例复现 |
| 日期未知、推断或冲突 | 核对原通知，不在上游补造原文、年份或时刻 |

CLI 的 JSON `--config` 可调整超时（最多 300 秒）和重试（最多 2 次）。失败与“没有任务”是不同结果。程序不开放模型工具调用，规则显式随请求加载，响应仍由程序校验；更改供应商后须重新验证，历史样例不保证当前性能。

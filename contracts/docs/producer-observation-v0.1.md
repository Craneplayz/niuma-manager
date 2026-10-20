# 可选生产方观测

Schema：`schemas/producer-observation-v0.1.schema.json`；共享输入：`examples/producer-observations-v01.json`。

## 生产方

- 爬虫库通过 `ScrapeOptions.downloadObserver(event)` 接收逐次下载观测；CLI 加 `--observe-downloads` 后仅在 stderr 输出这些 JSONL，stdout 仍是原采集契约，未开启时行为不变。
- 下载 `duration_ms` 使用单调时钟围绕实际附件 reader 计时，包含 Cookie 查询、重定向和响应体传输，不含本地解析、文件暂存及缓存命中；一次失败下载也输出一条 `download_failed`，不携带异常文本。
- `ChatClient(key, call_observer=callback)` 每次 `complete_json` 结束发送一条 `model_call`；原方法仍返回字符串、抛出原来的 `AgentError`，不新增调用或重试。
- 模型 `duration_ms` 是该客户端方法的单调时钟耗时，包含请求准备、HTTP 等待、响应读取及外层信封检查；`ok` 只表示外层响应可用，不代表任务证据校验或保存成功。
- `input_tokens`/`output_tokens` 优先取供应商 `prompt_tokens`/`completion_tokens`，兼容同名字段；`total_tokens` 仅取供应商原值，不求和补算；缺失、非整数、布尔、负数或超出安全整数的值均为 `null`，供应商真实返回的 `0` 则保留。
- HTTP 429 为 `rate_limited`，其他 HTTP 错误为 `http_error`，超时为 `timeout`，不可用连接/配置为 `unavailable`，外层响应无效或截断为 `invalid_response`；有合法 usage 的截断响应仍保留实际用量。
- 观测回调异常不改变业务结果；Python 回调应只做有界内存/IPC 写入，不应在回调内访问网络。爬虫不等待异步导出器，其拒绝也被隔离。

## 消费方交接（#189 / #198）

服务端为子进程或客户端调用建立独立观测接收器，在接收上下文中关联自己生成的随机 run/request ID；生产方事件不接受业务请求 ID，也不输出账号、URL、Cookie、凭据、提示词、材料、响应正文或异常文本。不得把整个 stderr 当指标保存，只解析通过本 Schema 校验的事件。

库内 callback 发生在运行客户端的进程；多进程识别需要消费方通过受限 IPC 转送，不能依赖父进程普通列表捕获子进程回调。硬终止的进程可能来不及输出事件；此时阶段耗时/用量不可观测，不补造事件或零值。

旧生产方、未启用观测、硬超时、采集没有下载时可能没有事件；没有事件统一按不可观测处理，不能从采集总耗时反推下载耗时。单次下载事件可累计为已观测下载时间，但采集失败时它只是已观测部分，不能冒充完整总量。usage 逐次保存，存在未知值时不能将已知部分的和标成完整用量。

此接口仅交接生产方观测；server 的接收、持久化、管理员展示及 1/3/5 账号基准由 #189 消费方接入后验收，不改变当前用户操作或环境配置。

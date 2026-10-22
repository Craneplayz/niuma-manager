# 识别错误约定

文本 v0.1/v0.2、文件 v0.3.1、教学网 v0.4 和后台同步共用 `contracts/recognition-errors.json` 中的分类与文案；已有 `code` 和 `retryable` 保持兼容，旧结果没有新增字段仍可读取。

错误对象可增加：

```json
{
  "code": "invalid_agent_output",
  "message": "模型结果缺少必要字段，或字段格式不正确。请重试；若反复失败，请反馈本次识别记录。",
  "retryable": true,
  "failure": {"reason": "draft_schema", "category": "output", "stage": "validate"},
  "diagnostic": {"schema_path": "properties/tasks/items/required", "rule": "required"}
}
```

`failure` 提供固定原因、类别和阶段；`diagnostic` 可选，只包含允许的 Schema 路径和规则，不包含字段值、模型原始回答、课程原文、地址或凭据。`retryable` 决定是否允许再次处理，类别本身不触发重试。

| 类别 | 典型原因 | 处理方向 |
| --- | --- | --- |
| input / source | 输入无效、超限、来源不完整或变化 | 修正输入或重新获取来源 |
| configuration | 依赖、模型或凭据配置不可用 | 维护人员修复配置 |
| attachment / content | 文件不可读、范围缺失、内容不清晰 | 补充可读内容，保留已完成结果 |
| service / timeout | 服务不可用、限流或超时 | 稍后重试失败部分 |
| output | 空白响应、截断、JSON/字段错误、最终契约失败 | 按具体原因纠错、拆分或反馈 |
| evidence | 引用不在原文或未读取内容中 | 拒绝不可信结果，核对来源 |
| storage | 保存未确认 | 先刷新确认，避免重复提交 |

文本和文件草稿校验失败时，可在原有时间预算内纠正一次；超时、配置、传输失败或截断不进入此纠错循环。纠错仍须通过原文和日期校验，不补造证据，不把失败改成 `no_tasks`。文件导入保留记录级进度，手动重试仅处理失败记录；图像证据继续要求人工核对。

测试环境通过 `POST /api/recognition/diagnostics`、请求体 `{"request_id":"本次请求ID"}` 查询当前账号的尝试记录，包含阶段、耗时和受限校验信息；生产环境不开放该接口。诊断不记录原文或模型原始回答。

修改目录后运行 `python -m contracts.build_recognition_errors` 生成协议字段和文件契约包，运行同一命令加 `--check` 校验同步；前端从同一 JSON 目录读取文案。反馈接口接受对应固定原因码，不上传原文。

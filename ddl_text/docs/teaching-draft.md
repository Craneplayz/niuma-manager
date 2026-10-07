# 教学网政策识别预览

`teaching_draft.py` 消费 PR #114 的 `0.4-draft.1` 配对组件，完成 #108/#109 的内部模型草稿、逐任务附件依赖、学期补年及 F3 历史检查；尚不是正式 v0.4 请求/结果接口。

先安装可选依赖：`python -m pip install -r ddl_text/teaching_requirements.txt`。旧服务不导入此模块。

```python
from ddl_text.config import Settings
from ddl_text.chat_client import ChatClient
from ddl_text.teaching_draft import process_teaching_draft

preview = process_teaching_draft(
    record, client=ChatClient(key),
    settings=Settings(mode="real", model=model, endpoint=endpoint,
                      timeout_seconds=45, max_retries=0),
    source_verified=False,
)
```

`record` 使用共享政策 Schema 中的 record；输入在模型调用前校验。当前实现使用 `complete_json` 客户端协议（七牛 ChatClient 支持），不经旧 v0.2 草稿 schema，也不调用 Codex。模型调用在限时子进程中执行，一次预览只尝试一次，不自动重试。

返回 Python `TeachingPreview`，每项 decision 保留共享 `pair`、标题/说明、通知性质、历史证据、`review_codes`、简短 `review_reasons` 和 `recommendation`（task/candidate/filtered）。这些值用于对照试验，不可直接传给 `/api/recognition/*` 或当作允许自动入库的结果。候选 ID 是本次预览内编号，不是跨重识别的永久任务身份。

PR #120 的 text_policy_manifest 只标识旧文本流程，不覆盖此独立草案；正式接入前需另行定义教学网运行策略身份，不能借用旧版本号标记新预览结果。

`no_action_complete` 仅在来源已核验、正文与引用均完整且模型返回空草稿时为 true；有缺口的空草稿附 warnings，不伪装成确定无任务。输入错误抛 invalid_input；结构/证据错误抛 invalid_agent_output；超时和供应商失败沿用 AgentError，不记录私人正文或供应商响应。

`source_verified` 默认 false，只允许受信任 Python 调用方设置。后端必须先从当前账号的受控采集快照恢复并核对完整记录，不能将客户端 ID、字段或 upstream_hints 自报可信直接透传为 true。此适配器不证明账号隔离、不读取 URL/本机附件、不核实来源状态；模型返回信任字段会被拒绝。

正式接入仍需：Ken 审查 #114；#107 提供来源事实与受控快照；定义正式版本信封；server 接入鉴权、双侧校验、入库与 #112 定向重识别；随后完成前端及端到端验收。现有 v0.2 路由及 v0.3.1 微信文件强制审核不变。

离线测试：`python -m unittest ddl_text.tests.test_teaching_draft -v`。真实合成测试：`python -m ddl_text.tests.run_teaching_smoke --live --env-file <本机配置> --output <忽略目录内结果.json>`；只发送固定合成样例，不读取私人课程。

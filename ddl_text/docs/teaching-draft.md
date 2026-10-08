# 教学网 v0.4 识别入口

`teaching_pipeline.process_teaching_request` 提供 #108/#109 的独立批次入口，消费 [共享 v0.4 契约](../../contracts/docs/teaching-v0.4.md)，不修改已有 v0.2 HTTP 路由；部署前须安装 `python -m pip install -r ddl_text/teaching_requirements.txt`。

```python
from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings
from ddl_text.teaching_pipeline import process_teaching_request, validate_teaching_result
from ddl_text.teaching_policy import teaching_policy_manifest

# request 和 verified_digests 由后端从当前账号的受控采集快照恢复。
# 不得直接对客户端提交的任意 JSON 计算 digest 并授予信任。
settings = Settings(mode="real", model=model, endpoint=endpoint,
                    timeout_seconds=45, max_retries=0)
manifest = teaching_policy_manifest(settings)  # 与实际作业配置一致，保存到后端运行记录
result = process_teaching_request(
    request, client=ChatClient(key), settings=settings,
    verified_snapshot_digests=verified_digests, budget_seconds=90,
)
if result["policy_version"] != manifest["version"]:
    raise ValueError("运行策略版本不一致")
validate_teaching_result(request, result, verified_snapshot_digests=verified_digests)
```

每条记录只尝试一次，限时子进程负责终止卡住的调用；批次默认预算 90 秒、最多 180 秒，每次调用上限为配置超时与剩余预算的较小值；失败保留该记录错误，其他成功结果照常返回，未开始记录标记 `attempted=false`。

真实运行使用内置 `ChatClient`；离线回放允许模型替身但 `policy_version=null`，不能冒充真实运行入库；输入不合法抛 `AgentError(invalid_input)`，部署配置或文件变更在执行前拒绝，记录级失败只输出固定错误码和安全消息。

模型判断行动、逐任务附件依赖、期限与历史语义；程序验证引文、完整性、可信学期、日期和 F3 30 天历史规则；明确正文中的任务可以不受补充资料未读影响，缺少关键资料仍待审核。

`validate_teaching_result` 不调用模型，会重算全部政策与历史门禁；消费者须在保存前调用它，不能只看 Schema 或 `recommendation`。

来源课程名按输入 `source.course_or_project` 原样保留；缺少时为 null，不由模型猜测；正文、附件、图片和链接正文保持分离，不能把拼接摘要标为完整正文。

`teaching_policy_manifest` 标识本入口的代码、规则、共享校验、实际模型/地址、超时上限和运行依赖版本，独立于 `text-policy-v1`；它不含密钥、私人原文或账号信息，修改代码后必须重启工作进程；批次预算和耗时另由作业记录保存。

候选 ID 只表示本次记录内序号，不能作为跨重识别永久任务身份；#112 消费端须按实际执行版本及逐条状态保存结果，失败、未尝试和旧结果不能被当前版本覆盖为成功，已确认/忽略/删除的用户决定须独立匹配保留。

仍需后端提供鉴权快照、课程/学期事实、来源状态处理、入库和 UI 适配；当前爬虫 v0.2 的 `upstream_hints` 不能直接冒充 v0.4 可信完整性；本模块不读取 URL、不抓附件、不处理 submitted 状态或自动完成、不承诺网页已切换。

旧 `teaching_draft.process_teaching_draft` 保留为内部单条预览和实测入口，其 Python 返回值不是公开 JSON 或自动入库许可；微信 v0.3.1 的直接文件强制审核规则不受影响。

```powershell
python -m unittest ddl_text.tests.test_teaching_draft ddl_text.tests.test_teaching_pipeline -q
python -m unittest contracts.tests.test_teaching_io -q
python -m contracts.build_teaching_v04 --check
# 显式付费调用，仅发送固定合成样例；不是网页链路验收
python -m ddl_text.tests.run_teaching_smoke --live --env-file <本机配置> --output <忽略目录内结果.json>
```

批次消费者测试读取 [7 组共享输入/输出](../../contracts/examples/teaching-v04-cases.json)，并回放 [8 组此前真实模型草稿](archive/2026-10-07-teaching-policy-live.json)；[诊断归档](archive/2026-10-07-teaching-policy-diagnostics.json) 保留原始失败与预期修正，这些不代表独立召回率或线上验收。

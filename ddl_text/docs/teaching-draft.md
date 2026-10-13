# 教学网 v0.4 模型分类

教学网使用 `teaching_pipeline.process_teaching_request` 接收账号绑定的 [v0.4 快照](../../contracts/docs/teaching-v0.4.md)；同一次调用完成行动、日期、证据与 `task/candidate/filtered` 分类。

## 职责

- [skill](../skills/teaching-task-extraction/SKILL.md) 定义语义：信息是否足够执行、缺失是否影响当前任务、合理补年、过期/仍需补交和非任务。
- `teaching_draft.py` 校验字段、标准日期、原文片段引用及输出自洽，直接采用模型 recommendation，不再调用正文完整性、附件关系、年份或历史审核规则。
- `teaching_pipeline.validate_teaching_result` 校验批次身份、证据与状态，不再次计算业务分类。
- `server/teaching.py` 原样投影模型分类，task 可自动新增、candidate 等待核实、filtered 保留结果及依据；无任务与来源不足分别显示。
- 来源账号权限、过期快照、用户已确认/忽略决定、既有任务修改权限和原生 submitted 状态关联保护仍归后端管理，不由模型解除。

正文 partial、未读补充附件和正常补年本身不要求审核。任务确实无截止时可以自动新增 due=null；任务自称尚有 unresolved 截止却又声明 task 属于不一致输出，作为识别失败处理。

真实模型选择 evidence_catalog 编号，程序恢复连续原文；课程元数据可辅助日期补年，但不能替代正文/已读附件中的行动和时间原话。未知编号、未读资料的伪造引文、非法日期、错误字段等仍报识别失败，不混入待审核。

## 调用与版本

```python
settings = Settings(mode="real", model=model, endpoint=endpoint,
                    timeout_seconds=90, max_retries=0, thinking_mode="disabled")
# verified_digests 必须由后端核验当前账号的受控采集快照，不能信任客户端声明。
result = process_teaching_request(request, client=ChatClient(key), settings=settings,
    verified_snapshot_digests=verified_digests, budget_seconds=90)
validate_teaching_result(request, result, verified_snapshot_digests=verified_digests)
```

安装 `python -m pip install -r ddl_text/teaching_requirements.txt`。每条一次调用，子进程超时回收，失败保留错误且不丢其他成功记录；七牛 DeepSeek 关闭 thinking，避免输出预算被推理耗尽。批次总预算最多180秒，不自动循环重试。

新结果使用 `classification_mode=model-v1`，`policy_version` 记录实际模型与代码规则摘要；模型内部草稿必须提供 recommendation/reason，旧草稿不能作为新调用成功结果。对外请求与 UI 卡片字段不变，结果新增兼容字段；旧结果仍可读取且不会在查看时重新分类。旧记录不能通过刷新页面自动放行或改写用户决定；当前成功的 v0.4 批次重试仍为幂等，现有批量重识别入口只支持旧 v0.2。验收新规则请用隔离测试账号首次同步；已有账号的 v0.4 批量迁移尚未接通，不要为测试清空生产记录。

`teaching_review_draft.review_codes` 只用于旧结果/历史契约回归，新模型路径不调用它；手工 v0.2 与微信 v0.3.1 不受本次变更影响。来源采集缺失内容仍需要采集侧补全，模型不能读取未提供资料。

## 验证

```powershell
python -m unittest ddl_text.tests.test_teaching_draft ddl_text.tests.test_teaching_pipeline -q
python -m unittest contracts.tests.test_teaching_io -q
python -m unittest discover -s server -p test_teaching.py -q
python -m contracts.build_teaching_v04 --check
# 显式付费，仅固定合成内容；使用自己的环境配置，不读取生产数据。
python -m ddl_text.tests.run_teaching_routing_smoke --live --env-file <当前环境配置> --output <忽略目录内新结果.json>
```

[八组真实合成输入与输出](../../contracts/examples/teaching-model-routing-v04-cases.json) 覆盖“尽快+明确日期”、partial 明确期限、无发布时间补年、未读补充资料、必须读取的日期更正、过期任务、纯停课消息和无期限行动。实测不代表独立召回率或生产账号验收；旧入口 `run_teaching_smoke` 转到同一组新测试。

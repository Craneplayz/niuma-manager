# 教学网课程上下文与结构化事实

本能力用于已有教学网识别/后台同步入口，不需要新增环境变量或前端操作。来源必须属于当前用户、教学网账号、原生 course_id 和明确的 course_term.term_id；同名课程、讨论班或其他学期不自动互通。无可信学期时继续原有单条识别。

## 资料与证据

服务器先登记一轮采集的全部原始来源，再检索最多6条相关来源。相关附件按需通过现有读取器读取，所以作业先于大纲识别也能引用大纲。缓存按用户/账号、文件哈希和读取版本隔离。不读取浏览器提交的任意路径或URL，也不接受采集方自行声明的 course_context。

执行快照可增加 `record.course_context`，版本为 `course-context/0.1`，包含 revision、documents、omitted_documents。references 对应项 kind=course_context。每段证据记录原始 input_id、snapshot_id、course_id、term_id、part_id、source_url、source_label、published_at、offset、length。位置按 Unicode 码点计，不能直接按 JavaScript UTF-16 下标解释非BMP字符。摘要绑定片段和出处，结果仍须与请求快照完全一致。

最多12段、合计24000字符，每份正文/附件最多4000字符，并遵守原有100000字符、50个引用总预算。检索同时使用通知正文、文件名和已读附件缓存正文；普通文件名的未读附件可作为候补。长文选最多两段不重叠原文，允许规则和例外位于不同页；不是整门课全量检索保证。截取段标记 partial，未取得附件/超出预算的部分计入 omitted_documents。上下文附件读取共用45秒预算，复用PDF可见性检查和断点；慢/无效文件不使主来源失败。缺口确实影响截止要求时说明具体缺失，不能声称全部资料已读。

新增、过滤和“没有任务”都必须有当前来源证据，上下文只能补充规则、时间和范围，不能在每个作业中重复导入大纲的考试，或借其他作业的提交状态过滤当前作业。资料自身明确的远期考试、lab安排由其自身识别，不按未来30天/90天截断。更正需明确适用关系；仅“发布时间更晚”不足以覆盖冲突规则。

## 增量与用户决定

依赖指纹使用原始内容和完成读取的缓存回执，不使用彼此变化的识别批次ID，避免循环重算。相关资料更新、新材料到达或原本未完成的附件后来读完，会在下一轮同步重识别受影响来源；所选上下文、源内容和已完成缓存回执未变时，重复采集不重算；启发式检索的候选变化可能触发重算。保存前再次检查来源、资料与用户控制状态，变化则拒绝迟到结果。

每轮复用同一份课程目录、分词和缓存回执，避免为每条任务重复解析全课程及读取全部附件索引；完整附件缓存不受新增读取的45秒预算影响。

同一原始快照因上下文变化重算时，成功结果可替换未经操作的初始待核对候选。已保存、修改、忽略、完成、删除或人工关联的内容继续受保护，新解释进入待核对更新，不能再次自动加入。失败保留旧候选。原生已提交作业过滤保留。

本功能不增加跨课程自动关联，不改变自动合并模式，不按标题合并任务或传播提交状态。

课程级资料组织和共享格式约定参考 [AutoPku 的课程同步工作流](https://github.com/Fomalhaut647/AutoPku/blob/459c8ea9c4f486fc5e1a9d525a496b28442c16aa/sub-skills/tasks/sync-notices.md)及[资料抽取脚本](https://github.com/Fomalhaut647/AutoPku/blob/459c8ea9c4f486fc5e1a9d525a496b28442c16aa/assets/speed-notes-kit/extract_sources.py)的设计；此处独立实现服务端鉴权、证据和写回，没有复制其源码。

## 标题与日期

结果可带 `title_facts={kind,channel,sequence,subject,evidence}`。自由文本必须来自引用原文。程序固定格式渲染；有明确批次的 assignment/lab 使用“渠道类别 · 批次”，主题和题号留在说明中；明确的“第四章/第4章/Chapter 4”等批次仅在显示时统一，原文事实和证据保留，不能用作任务合并依据。无可靠结构化事实的历史/新草稿仍兼容原标题。书面、POJ、MOOC等独立渠道分别保留。

结果可带 time_role 和 calendar_rule。后者包含 week_one_monday、week、weekday（周一=1）、offset_days、evidence。程序检查原文完整基准日期、星期及学期，计算日期，输出 derivation=course_rule、anchor=course_term、date精度，并要求核对规则适用性。学期 starts_on 不作为开学第一周；没有时刻不添加23:59。复杂双周循环、假期调整等不能确定时保持未知。明确远期日期走正常识别，不必进入周次核对。

上下文支持的日期在卡片中标为 inferred，保留出处；无疑义且模型允许收录的原生教学网候选仍可自动入库。周次规则及有疑点的候选不会自动入库。

## 前端预留接口

登录后 `POST /api/recognition/course-context`，请求：

```json
{"request_id":"teaching:服务器返回的批次ID"}
```

响应 `{contract_version:"course-context/0.1",request_id,records:[{input_id,context,passages}]}`；passages 为 documents 加 text、read_state。context=null 表示该批没有课程上下文。只查询当前用户的已保存批次；他人/不存在批次返回404，非教学网批次或非法参数返回400。没有模型调用或数据修改。卡片 evidence/sources 同时指向原始材料；本次不新增前端页面。

## 验证

在仓库根运行，PYTHONPATH 包含仓库根和 server：

```sh
python -m contracts.build_teaching_v04 --check
python -m contracts.build_recognition_errors --check
python -m unittest server.test_course_context server.test_course_context_http -v
python -m ddl_text.tests.run_course_context_smoke --live --env-file /path/to/test.env --output /new/path/model-cases.json
python -m server.run_course_context_smoke --live --env-file /path/to/test.env --output /new/path/http-docx.json
```

单元/HTTP回归检查隔离、预算、来源变化、幂等、证据及人工决定；合成模型边界不证明识别质量。两个 --live 工具才调用真实模型：前者检查跨文件规则、冲突、远期日期、三种交付、发布日期、教学周及连续批次标题；后者让两份真实合成DOCX经读取、上下文、真实模型、HTTP、入库与重复同步走完整链路，退出关闭临时API。均不访问私人课程、真实教学网或生产数据。单次通过不保证任意课程和模型输出总是正确，新增失败样例需保留独立预期并回归。

# 教学网课程上下文与结构化事实

本能力用于已有教学网识别/后台同步入口，不需要新增环境变量或前端操作。来源必须属于当前用户、教学网账号、原生 course_id 和明确的 course_term.term_id；同名课程、讨论班或其他学期不自动互通。无可信学期时继续原有单条识别。

## 资料与证据

服务器先登记一轮采集的来源。原文明示引用其他材料时，检索最多 6 条匹配的课程网页；仅写“此前通知”且有多条匹配时，保留这个疑点。自动同步只使用教师网页正文作旁证。

执行快照可增加 `record.course_context`，版本为 `course-context/0.1`，包含 revision、documents、omitted_documents。references 对应项 kind=course_context。每段证据记录原始 input_id、snapshot_id、course_id、term_id、part_id、source_url、source_label、published_at、offset、length。位置按 Unicode 码点计，不能直接按 JavaScript UTF-16 下标解释非BMP字符。摘要绑定片段和出处，结果仍须与请求快照完全一致。

最多选取 12 段、合计 24000 字符，每份网页最多 4000 字符。网页标题或资料类型须匹配原文引用。长文选最多两段不重叠原文，按契约记录截取范围及省略数量。

新增、过滤和“没有任务”都必须有当前来源证据，上下文只能补充规则、时间和范围，不能在每个作业中重复导入大纲的考试，或借其他作业的提交状态过滤当前作业。资料自身明确的远期考试、lab安排由其自身识别，不按未来30天/90天截断。更正需明确适用关系；仅“发布时间更晚”不足以覆盖冲突规则。

真实识别先隔离其他来源，只用本条网页正文和元数据决定行动及适用状态，首轮与纠错都遵守隔离。确认有行动后，旁证阶段仅能更新固定槽位的日期、课程周规则和核对理由，不能重写行动、标题、类型或历史状态；槽位只在本次调用内定位，不能用于跨版本去重。两阶段共享原有模型时间预算，各阶段格式纠错最多一次。旁证阶段超时或持续无效时，保留主来源已验证的行动并在识别记录中保存具体疑点，清单按可确定的字段保存，不把整条记为识别失败，也不伪造日期；主来源识别失败仍报告错误。

主来源仅因规则/适用范围缺失而标为 uncertain 时，旁证可凭证据将其细化为 current/ongoing；不能将已确认的当前行动改成 historical。已经补齐的日期疑点清除旧核对理由，仍有行动不明、规则冲突或变更待关联时继续核对。

行动发现阶段显式说明“旁证已准备、稍后补充”：本条已明确要求执行的行动不能因当前阶段缺少日期材料而返回空的 incomplete，缺少日期/规则时先保留候选；只有行动内容本身不可取得时才视为来源不完整。

低编号、无需提交、没有截止日期或其他作业已经发布，都不构成历史/已完成依据。安装、运行、阅读等明确准备要求按当前来源处理。旁证中的取消或冲突提示核对，不静默过滤当前行动。仍出现仅有旁证的行动或空结果时，报告 `primary_source_evidence`，不绕过当前来源证据校验。

## 增量与用户决定

依赖指纹记录所选网页来源身份和内容，重复采集相同输入时复用识别结果。

升级时，对页面内容和平台状态相同的旧快照保留识别身份；已有任务和人工决定继续受保护。

保存时核对同账号、课程与学期的原始网页目录。新增、删除或内容变动会阻止旧结果写回。

每轮复用课程目录和分词结果。

同一原始快照因上下文变化重算时，成功结果可替换未经操作的初始待核对候选。已保存、修改、忽略、完成、删除或人工关联的内容继续受保护，新增的变更说明保存为通知，由用户选择是否修改已有任务。失败保留旧候选。原生已提交作业过滤保留。

普通同步重算后，若与同账号、同来源最近成功结果的完整已验证决策相等，则复用原处理记录，不新增或替换候选。比较忽略上下文载荷、单次候选编号及结果顺序，保留本条原文/附件、行动、标题说明、日期及证据等差异；仅上下文变更本身不构成重复审核的理由。措辞变化、真实日期更正或新增行动仍保守核对，不按标题、数组下标或“快照相同”强行合并。

本功能不增加跨课程自动关联，不改变自动合并模式，不按标题合并任务或传播提交状态。

## 标题与日期

结果可带 `title_facts={kind,channel,sequence,subject,evidence}`。自由文本必须来自引用原文。程序固定格式渲染；有明确批次的 assignment/lab 使用“渠道类别 · 批次”，主题和题号留在说明中；明确的“第四章/第4章/Chapter 4”等批次仅在显示时统一，原文事实和证据保留，不能用作任务合并依据。无可靠结构化事实的历史/新草稿仍兼容原标题。书面、POJ、MOOC等独立渠道分别保留。

可选标题事实格式、引用编号、逐字证据或原文字段不可靠时丢弃整个 `title_facts`，保留已通过校验的原标题，记录不含正文的 `presentation/fallback` 诊断；不能为修饰标题拒绝整条通知。行动、截止日期、课程日历和来源证据校验保持严格，不通过时仍拒绝结果。

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
python -m unittest ddl_text.tests.test_course_refinement ddl_text.tests.test_course_context_boundaries server.test_teaching_identity -v
python -m ddl_text.tests.run_course_context_smoke --live --env-file /path/to/test.env --output /new/path/model-cases.json
python -m server.run_course_context_smoke --live --env-file /path/to/test.env --output /new/path/http-docx.json
```

单元/HTTP回归检查隔离、预算、来源变化、幂等、证据及人工决定；合成模型边界不证明识别质量。两个 --live 工具才调用真实模型：前者检查跨文件规则、冲突、远期日期、三种交付、发布日期、教学周及连续批次标题；后者让两份真实合成DOCX经读取、上下文、真实模型、HTTP、入库与重复同步走完整链路，退出关闭临时API。均不访问私人课程、真实教学网或生产数据。单次通过不保证任意课程和模型输出总是正确，新增失败样例需保留独立预期并回归。

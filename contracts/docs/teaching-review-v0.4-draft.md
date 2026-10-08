# 教学网审核与学期上下文契约草案

关联 #108、#109，采集对接 #107。**这是供跨模块审查的阶段 A 政策组件，不是新的识别 API，也不能直接驱动自动入库。** 当前 v0.2 运行端和 PR #98 的 v0.3.1 微信文件契约保持原样。

## 入口与版本

- 草案版本 `0.4-draft.1`：新增读取覆盖与学期结构，不能以补丁形式静默更改 v0.2/v0.3.1。正式 v0.4 信封、运行端接入及迁移由 Ken 和上下游审查后另行实现。
- [独立根 Schema](../schemas/teaching-review-pair-v0.4-draft.schema.json) 校验 `record + assessment` 配对对象；它不是完整请求/结果 Schema。根拒绝空对象及额外字段，所有 `$ref` 均为包内引用，无需联网或注册 URN。
- [判断样例](../examples/teaching-review-v0.4-draft-cases.json) 含 23 个合成配对及期望原因码；包装字段 `source_verified` 只供测试模拟可信后端通道，不能放进上游请求或模型输出。
- [参考校验器](../teaching_review_draft.py) 验证结构、证据与字段关系，返回这两项政策的审核原因码；**空原因列表只表示这两个检查通过**，不能取代历史排除、变更匹配、用户意向、完成状态和正式自动入库检查。

## 上游记录 record

| 字段 | 含义及责任 |
| --- | --- |
| input_id / source_item_id / course_id | 当前记录、原作业和课程的稳定 ID；不能用标题作为唯一身份。 |
| snapshot_id | 本次采集快照的标识，由后端绑定账号、来源及内容版本；不是访问凭据。 |
| platform | 本草案限定 teaching_web，不自动推广到微信直接文件证据。 |
| text / body_status | 实际取得的正文，以及 complete / partial / summary / missing；完整正文附带未读文件时可为 complete，不能把文件读完当成正文读完的条件。 |
| references | 真实附件、内容图片、详情引用；只删除可以确定是装饰/导航的元素，不能忽略所有图片、链接或未知内容。 |
| source_metadata_text | 抓取到的课程/学期/适用范围说明，供元数据证据逐字定位，不是模型总结或新的正文。 |
| published_at | 真实原发布时间及 source / unverified 基准；未知为 null，不用采集时间补造。 |
| collected_at / timezone | 本次采集时间及 IANA 时区，用于回放和判断当前时点。 |
| course_term | 未知为 null；已知包含稳定 term_id、starts_on、ends_on、applies_to 及来源证据。 |

每个引用包含 `ref_id/kind/label/read_state/text`。kind 为 attachment/content_image/linked_detail；read_state 为 complete/unread/partial/failed/unsupported。complete/partial 须有实际取得的非空文字，其他状态 text=null；不能将模型自报“读完”当作已采集文字。ref_id 在记录内唯一，不能占用 body/source_metadata。

这是可逐字核验的文本覆盖组件，不表示支持任意文件读取、下载 URL 或新上传接口；引用的网络访问、实际字节、资源限额和附件原件生命周期仍由 #107/既有附件模块管理。直接文件 + model_reported 不适用这里的放宽，#98 v0.3.1 的强制审核规则不变。

### 可信范围的建立

Schema 通过、platform=teaching_web、课程名带年份或 JSON 中自行填写 ID，都不证明可信。

未来 server 必须从当前账号的受控采集通道或服务器持有的快照中取回记录，核对账号、course_id、source_item_id、snapshot_id 和完整内容版本；不能按客户端给的 ID 查到某个快照就信任客户端其余字段。只有核对后的记录才能作为参考函数 `source_verified=True` 的来源。未实现这一层时保持默认 false；此参数不得暴露为 HTTP 输入或由模型填写。

`course_term.applies_to` 区分 current_assignment（来源证明确属该学期当前布置的作业）、course_material（课程内保存的材料）和 unknown。只读取课程名称或当前打开课程页面，最多说明课程范围，不能推断所有材料均为新作业。current_assignment 需有来源适用范围证据；正文出现往年要求、取消或冲突时，还须执行相应语义/历史规则。

## 任务字段判断 assessment

| 字段 | 规则 |
| --- | --- |
| candidate_id / input_id | 当前候选及所属输入；input_id 必须等于 record.input_id。本组件只校验单记录单候选；完整批次 ID 唯一性由正式信封校验。 |
| action.state / evidence | supported 或 uncertain；supported 须有正文或已取得引用文字中的逐字证据，不能仅引用课程元数据。 |
| deadline | 标准 value、precision、derivation、anchor 及逐字证据；未知仍为 null，date-only 不补时刻。 |
| unread_relations | 每个非 complete 引用恰好一条关系，不得遗漏/重复/引用其他记录。 |
| other_review_reasons | 保留所有独立疑点，不能因为本草案两个检查通过就清空。 |

证据由 `part_id + quote` 指向 body、source_metadata 或实际已读取的 ref_id；引文必须是该段连续原文。学期证据只引用 source_metadata，任务行动/日期/附件关系不能用课程标签冒充正文证据。

对每个未读引用，模型判断 supplementary / required / unknown：

- supplementary：正文已独立支持该任务的行动及时间，引用不是判断这些字段的必要来源；须引用实际正文说明依据，不把“附件没读”自动改成“不重要”。这仍是模型语义判断，逐字校验不证明其必然正确。
- required：正文把最终要求/截止交给附件，或当前任务字段依赖该内容；须引用依据，保留审核。
- unknown：无法确定依赖关系，保留审核，无须伪造依据。

`supplementary` 是模型的关系判断，不直接授予免审。参考门禁要求其关系证据全部来自 `body`，行动和已知日期各自至少有一条正文证据；缺少这些依据时返回 `unread_unknown:ID`。任何未完整读取的引用一旦被用于行动或日期证据，即使同时引用正文且自称 supplementary，仍返回 `unread_required:ID`，不能用该引用自身或其他附件的文字免责。只收紧有效免审条件，保留原始判断和引文供复核，不修改输入或丢弃候选；未知/未提供日期仍执行独立的日期门禁。

例如正文只写“要求和截止见附件”，partial 附件提供全部行动/日期并自称“仅供参考”，须返回 `unread_required:ID`。正文独立给出行动/日期、正文说明附件仅供参考且字段证据不使用该未完整引用的原有正例仍通过；程序的引用位置检查不代替模型判断引文是否真正支持字段。

正文不完整或 action=uncertain 仍审核。已完整正文有明确行动和日期，且没有 required/unknown 引用或其他疑点时，这一门禁不因补充材料未读而审核；不能因此说附件已读完。

`not_provided` 只用于完整正文且无未读引用的无时间要求情形；仍有未读资料时，草案保守保留 deadline_coverage_unknown。明确未设置截止日期的特殊语义如需另行放宽，应补充样例后审查，不能靠空 times 推定附件里没有日期。

## 年份与历史范围

1. 明确年份使用原文年份，不用学期范围改写；超出学期的明确未来截止仍可有效。
2. 省略年份且可信原发布时间存在：优先 published_at，沿用前一年/当年/后一年中距离最近、等距优先未来的规则；不能因为当前课程标签更近就替换旧发布时间。
3. 缺可信发布时间，且可信 current_assignment 学期存在：使用 course_term 基准，在学期区间中查找该月日对应的唯一合法年份；跨年学期可以得到次年日期。不存在或不唯一则保留审核，不直接删除任务。
4. 只有课程标签、course_material、unknown 或未核验来源时，仍不能排除旧通知；依采集时间补年的候选保留 original_age_unknown。
5. “明天”等 relative/offset 仍只用可信原发布时间；offset 还要求发布时间含时刻，不能用学期或采集时间替代。
6. 本组件不实现完整历史分类。明确旧任务仍由历史规则排除；旧通知有未来截止或仍要求补交时保留。样例 old-publication-keeps-old-year 返回空原因，只表示补年依据正确，**不是批准 2023 年过期任务自动入库**。

所有判定只校验标准日期及时间基准，不增加原始日期表达式的格式白名单；语义仍由模型及可信上下文提供。课程学期范围不是原发布时间，不能把期初日期写入 published_at。

## 原因码与边界

| 原因码 | 用户可理解的含义 |
| --- | --- |
| source_unverified | 来源上下文尚未由后端核验。 |
| body_incomplete / action_uncertain | 正文存在缺口 / 行动性质尚不明确。 |
| unread_required:ID / unread_unknown:ID | 任务依赖未读内容（包括部分读取的字段证据）/ 无法判断其影响或缺少正文免审依据。 |
| deadline_unknown / deadline_coverage_unknown | 日期本身未知 / 未读内容使“无 DDL”无法确认。 |
| original_age_unknown | 缺少可信发布时间或适用学期，无法确认消息年代。 |
| publication_required / publication_anchor_required | 相对时间缺发布时间 / 应优先使用已有可信发布时间。 |
| year_anchor_mismatch / term_date_unresolved | 补年与发布时间不符 / 学期内无法唯一确定年份。 |
| other_review | 存在独立疑点，保留原理由。 |

结构或证据无效抛出校验错误，不能伪装为没有任务。代码不读取网络、原件、凭据或用户数据库；不保证模型语义正确，不验证来源真实身份，也不实现最终入库。

每个 ID≤256 字符，标签/理由≤512，逐条引文≤2048，元数据说明≤4096，引用≤50、字段证据≤20、学期证据≤10；合计可引用文本≤100,000 字符，配对 JSON≤1,000,000 UTF-8 bytes。拒绝超限而非静默截断。IANA 时区须可解析，datetime 偏移与该时区一致，学期区间正序且不超过366天。

## 验证与接入顺序

在仓库根目录执行：

```powershell
python -m pip install -r contracts/requirements-teaching-dev.txt
python contracts/teaching_review_draft.py
python -m unittest discover -s contracts/tests -p test_teaching_review_draft.py -v
```

先由 Ken、crawler、ddl_text、server 负责人审查本组件及版本方向；再在 #107 实现覆盖/学期事实，在 #108/#109 实现正式模型草稿、版本转换和两侧校验，最后与 #110/#112 的提交状态及旧结果重识别联调。正式请求/结果结构必须另外完整定义并通过消费者测试，不能只把现有 contract_version 字符串改成 0.4。

当前 PR 只交付本草案、可执行判定和反例；不代表上述 Issue 全部完成，不影响正在运行的 v0.2 服务。与 PR #98 并行，既不依赖其未合入实现，也不覆盖其原文件。

# 教学网采集快照（#107，v0.2 扩展 hints）

合成 HTML / 输出配对样例见 [cases](../source-to-text/teaching-source-snapshot-v02-cases.json)，hints 独立结构校验见 [Schema](../schemas/teaching-source-snapshot-v0.2.schema.json)；生产方直接回放同一批样例，消费方同时验证 v0.2 信封及 hints。

运行信封仍为 v0.2；本文约定开放字段 `upstream_hints.references` 的来源读取元数据，不改变任务、审核或学期授信规则，须由 contracts / ddl_text 负责人审查。

每项引用保留 `label`、清除凭据的 `url`、`content_read`，新增 `kind`（detail / attachment / image / link）、`read_status`（unread / complete / partial / failed / unsupported）和可空 `reason`。内嵌 data / 非 HTTP 引用的 url 为空字符串，仍保留 unread，禁止输出 base64 或私有数据。仅 complete 对应 content_read=true；partial 不可标成已完整读取。成功提取的附件文字逐项保存在 `text`，同时以「附件正文：名称」标明来源并追加到 `raw.body` / `text`，不改写原始教师正文或推断 DDL。

`reason` 是稳定机器码：not_read、image_not_read、unsupported_format、untrusted_attachment_url、http_error、authentication_required、download_failed、too_large、parse_failed、empty_text、partial_pdf、detail_unrecognized、detail_failed、submission_history_only。异常消息、Cookie、账号与提交文件不得作为 reason 或 evidence。

正文中的 Blackboard 图标仅在同源 `/images/ci/` 且明确 role=presentation / aria-hidden=true，或匹配已知文件 / 更多选项素材路径且 alt 为空或平台 UI 标签时排除；未知素材即使 alt 为空也保留未读引用；正文真实图片、正文链接及附件均保留。菜单、脚本、按钮及表单不属于教师正文。标题附件即使没有正文也保留引用。作业详情页仅解析教师 assignment-information 区域；空占位容器继续寻找后续正文，多个教师容器中的文字与实质引用合并去重（图片/附件区域即使无文字也保留），其附件与列表引用合并去重。

按已确认的“已提交视为完成”规则，登录教学网原生作业详情页的“复查提交历史记录 / Review Submission History”页面标题表示 `assignment.state=submitted`，不要求额外读取提交记录；正文中的同名文字、私人尝试区域内的标题和菜单入口不作为依据。没有这个标题时继续按教师作业信息识别 active，无法识别或读取失败保持 unknown / 未知；不从页面推断是否需要重交，也不因为允许新建尝试而否定已提交。仅采集页面标题和最小状态，不读提交文件、作答、成绩或尝试时间。提交状态与读取完整性独立：历史页面详情引用为 partial / submission_history_only，`content_read=false`、整条 `text_quality=partial`，已读列表文字保留并追加“提交状态：已提交”，不把未读教师正文标为完整。

稳定身份复用既有 `input_id`、`source_item_id` 和链接：课程 ID 为 `source_url` 中的 `course_id`，作业 ID 为原生 detail 引用 URL 中的 `content_id`；需与该引用的 `course_id` 一致；详情跳转后必须保留原链接中的课程 / 作业 ID，否则视为 detail_failed。缺失或冲突时不得按课程名 / 标题猜测关联。详情补全不改变列表来源身份，同课程同作业的状态变化保持相同身份，不同课程同名作业保持隔离；应用账号和教学网账号由受控采集入口提供，不从 hints 获取。#110 后端只在该入口消费状态，不能授信任意导入 JSON / 粘贴文本中的 submitted；一份作业的提交状态不表示其所有独立行动都已完成，任务 / 行动关联仍由后端确认。页面不可观测的重交要求由用户重新打开任务；后续采集到 active / unknown 时交给后端重新评估，不永久缓存 submitted。

`text_quality` 仍保守表示整条已采集输入：无文字为 summary / missing；有文字且存在非 complete 引用为 partial；教师正文与所有实质引用读全才可 complete。supplementary / required / unknown 由 #108 逐任务判定，crawler 不授予免责。

支持同源教师内容路径 `/bbcswebdav/pid-…-dt-content-rid-…`、`/bbcswebdav/courses/…` 的 PDF 与 UTF-8 txt；其他链接不带教学网 Cookie 访问。下载与每次重定向均校验同源内容路径，流式上限 5 MiB、超时 15 秒，PDF 在独立 Node 子进程中解析（15 秒、旧生代堆 128 MiB 限制）；正常读取等待 PDF 清理与进程退出，崩溃或超时保留 parse_failed，不使整个采集退出，上限 50 页，单项提取文本上限 50,000 字符，列表、详情、到期日期和附件统一检查 v0.2 的 100,000 字符（含标题及分隔符）预算；详情合并超限时保留整份原列表正文、原列表标题及原始到期日期元数据，将详情引用标为 failed / too_large 并明确提示人工核对，不静默截断，也不阻断其他正常记录；附件追加超限保留 too_large 未读引用。PDF 仅在绘制算子都属于已支持的文字/状态操作且解析无退化警告时才可 complete；图片、stencil mask、矢量图、注释、未知绘制操作或空白/扫描页保留 partial，跳过损坏对象等解析警告也保留 partial_pdf，解析抛错为 parse_failed。压缩包、Office、图片 OCR、外链与嵌套目录本轮 unsupported / unread。完整文本提取只证明采集覆盖，不证明作业适用于当前学期，也不解除其他审核原因；#109 的可靠课程 / 学期适用范围、#110 的后端状态消费和正式 v0.4 信封后续分别对接，不使用采集时间作为发布时间或课程名称作为授信凭证。

附件解析缓存按 URL 与推断格式区分；显示名不足以确定格式时不写入缓存，后续更明确的引用仍可读取；缓存独立保留提取结果，单条记录追加超限不能删除缓存正文或影响后续记录。

教师容器自身及祖先若属于私人表单、提交、尝试、成绩或导航区域，整块排除，不输出其文字和引用；原生作业页仅允许已知外层 form#uploadAssignmentForm，其控件及私人区域仍先移除，再解析教师信息。标题、状态和到期日期也使用过滤后的页面；日期仅从 assignmentInfo / assignment-info 区域内的标签所在行或子容器提取，不向区域外回溯。未找到教师说明或实质引用时，保留列表文字并将详情标为 failed / detail_unrecognized、有文字时整条保持 partial，不用列表摘要证明详情读全。图片同时检查 src、srcset 及 picture/source 的候选；默认 src 为装饰图标不能使其他实质候选被忽略，data / 非 HTTP 候选仍以空 url 保留未读，不输出内嵌数据。PDF 文档级内嵌附件未提取时保留 partial_pdf，不递归读取或输出内嵌文件内容。

所有新字段仅是来源事实，不能从 upstream_hints 取得后端 source_verified 授信；v0.3.1 attachment_file/model_reported 强制审核规则保持原样。

支持教师信息区内标签后的相邻日期节点及日期行；读取在下一字段标签（含嵌套的 .label / data-role=label、原生 label / th 及 ARIA 行列头）、行或说明边界停止，父容器查找只穿过纯标签包装，不拼接其他字段的值。表格标签只读取同一行紧邻的一个 td 值单元格，不连续扫描后面的单元格；日期与时间可在该值单元格内拆分。明确存在日期标签但值无法读取或多个值冲突时，保留已读说明与日期证据、将详情标为 failed / detail_unrecognized 并提示人工核对，不标 complete。明确“未设置 / Not set”表示无到期日期。过滤后的“作业信息 / Assignment Information”标题继续作为页面识别入口。教师说明按 DOM 顺序合并，只读取最外层匹配容器，已被祖先覆盖的后代不重复采集；独立容器及其引用仍保留。平台提交区域 #assignmentSubmission 即使没有 submission class 也排除。

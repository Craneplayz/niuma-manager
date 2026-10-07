# 教学网采集快照（#107，v0.2 扩展 hints）

合成 HTML / 输出配对样例见 [cases](../source-to-text/teaching-source-snapshot-v02-cases.json)，hints 独立结构校验见 [Schema](../schemas/teaching-source-snapshot-v0.2.schema.json)；生产方直接回放同一批样例，消费方同时验证 v0.2 信封及 hints。

运行信封仍为 v0.2；本文约定开放字段 `upstream_hints.references` 的来源读取元数据，不改变任务、审核或学期授信规则，须由 contracts / ddl_text 负责人审查。

每项引用保留 `label`、清除凭据的 `url`、`content_read`，新增 `kind`（detail / attachment / image / link）、`read_status`（unread / complete / partial / failed / unsupported）和可空 `reason`。内嵌 data / 非 HTTP 引用的 url 为空字符串，仍保留 unread，禁止输出 base64 或私有数据。仅 complete 对应 content_read=true；partial 不可标成已完整读取。成功提取的附件文字逐项保存在 `text`，同时以「附件正文：名称」标明来源并追加到 `raw.body` / `text`，不改写原始教师正文或推断 DDL。

`reason` 是稳定机器码：not_read、image_not_read、unsupported_format、untrusted_attachment_url、http_error、authentication_required、download_failed、too_large、parse_failed、empty_text、partial_pdf、detail_unrecognized、detail_failed、submission_history_only。异常消息、Cookie、账号与提交文件不得作为 reason 或 evidence。

正文中的 Blackboard 图标仅在同源 `/images/ci/` 且明确 role=presentation / aria-hidden=true，或匹配已知文件 / 更多选项素材路径且 alt 为空或平台 UI 标签时排除；未知素材即使 alt 为空也保留未读引用；正文真实图片、正文链接及附件均保留。菜单、脚本、按钮及表单不属于教师正文。标题附件即使没有正文也保留引用。作业详情页仅解析教师 assignment-information 区域；空占位容器继续寻找后续正文，多个教师容器中的文字与实质引用合并去重（图片/附件区域即使无文字也保留），其附件与列表引用合并去重；复查提交历史页标题只证明历史页面存在，assignment.state=unknown，保持 partial 和人工核对，不读取成绩或尝试区域。

`text_quality` 仍保守表示整条已采集输入：无文字为 summary / missing；有文字且存在非 complete 引用为 partial；教师正文与所有实质引用读全才可 complete。supplementary / required / unknown 由 #108 逐任务判定，crawler 不授予免责。

支持同源教师内容路径 `/bbcswebdav/pid-…-dt-content-rid-…`、`/bbcswebdav/courses/…` 的 PDF 与 UTF-8 txt；其他链接不带教学网 Cookie 访问。下载与每次重定向均校验同源内容路径，流式上限 5 MiB、超时 15 秒，PDF 在独立 worker 中解析（15 秒、旧生代堆 128 MiB 限制），上限 50 页，单项提取文本上限 50,000 字符，列表、详情、到期日期和附件统一检查 v0.2 的 100,000 字符（含标题及分隔符）预算；详情合并超限时保留整份原列表正文、原列表标题及原始到期日期元数据，将详情引用标为 failed / too_large 并明确提示人工核对，不静默截断，也不阻断其他正常记录；附件追加超限保留 too_large 未读引用。PDF 仅在绘制算子都属于已支持的文字/状态操作且解析无退化警告时才可 complete；图片、stencil mask、矢量图、注释、未知绘制操作或空白/扫描页保留 partial，跳过损坏对象等解析警告也保留 partial_pdf，解析抛错为 parse_failed。压缩包、Office、图片 OCR、外链与嵌套目录本轮 unsupported / unread。完整文本提取只证明采集覆盖，不证明作业适用于当前学期，也不解除其他审核原因；#109 的可靠课程 / 学期适用范围、#110 的当前提交证明和正式 v0.4 信封后续分别对接，不使用采集时间作为发布时间或课程名称作为授信凭证。

附件解析缓存按 URL 与推断格式区分；显示名不足以确定格式时不写入缓存，后续更明确的引用仍可读取；缓存独立保留提取结果，单条记录追加超限不能删除缓存正文或影响后续记录。

教师容器自身及祖先若属于私人表单、提交、尝试、成绩或导航区域，整块排除，不输出其文字和引用；原生作业页仅允许已知外层 form#uploadAssignmentForm，其控件及私人区域仍先移除，再解析教师信息。标题、状态和到期日期也使用过滤后的页面；日期仅从 assignmentInfo / assignment-info 区域内的标签所在行或子容器提取，不向区域外回溯。未找到教师说明或实质引用时，保留列表文字并将详情标为 failed / detail_unrecognized、有文字时整条保持 partial，不用列表摘要证明详情读全。图片同时检查 src、srcset 及 picture/source 的候选；默认 src 为装饰图标不能使其他实质候选被忽略，data / 非 HTTP 候选仍以空 url 保留未读，不输出内嵌数据。PDF 文档级内嵌附件未提取时保留 partial_pdf，不递归读取或输出内嵌文件内容。

所有新字段仅是来源事实，不能从 upstream_hints 取得后端 source_verified 授信；v0.3.1 attachment_file/model_reported 强制审核规则保持原样。

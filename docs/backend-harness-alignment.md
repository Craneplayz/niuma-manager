# 牛马管理器：前端 / 后端 / 模型 Harness 对齐说明

文档版本 2.1 · 2026-09-24。本文件是本项目唯一的协作协议基线，统一包含文本识别 v0.2、业务确认 API 和规划 2.0。已按项目负责人授权完成规则取舍及本地实现，不代表外部识别/规划服务已经部署。

顺序固定为：授权来源 → 文本识别候选 → 用户核对/事务确认 → 正式任务 → 用户点击开始规划 → 模型估时与安排 → 预览确认。识别和规划的版本号、请求 ID、服务地址分别管理。

## 已实现边界

- 前端：注册/登录/退出、任务与候选结果、可用时段、持久化“无需规划”选择、个性化用时反馈、规划预览与确认。
- 本地后端：Python 3.9+ 标准库 HTTP API + SQLite，所有读取/写入由 Cookie 会话确认用户。前端不提交 userId，不持久化业务数据。
- 模型 Harness：待团队实现/配置真实服务。前端与后端均不提供默认 1 小时或规则估时的假模型。未配置 HARNESS_URL 返回 503。
- v0.2 文本识别提交、批次与记录级状态、证据/来源/日期依据展示、新增/更新/延期/取消/忽略审核、候选事务确认和幂等检查均已实现。本地“加载协议演示”明确为人工样例，不调用模型。
- 真实文本识别由 TEXT_HARNESS_URL 提供，真实规划由 HARNESS_URL 提供，缺少对应配置返回 503。真实邮箱/教学网/微信采集仍未接入；来源刷新入口只引导至文本识别页，不伪造同步结果。
- 本地规则现在仅展示日期线索，默认不填写 DDL；用户明确确认日期是截止时间后才能带入编辑表单。审核原文按候选展示，缺失就提示，不用示例补齐证据。

## 数据与会话

前端工程现位于 `frontend/`，npm 命令在该目录执行。参考潘勇圳实现的功能取舍与署名见 [前端贡献说明](../frontend/CONTRIBUTIONS.md)。本次不改动文本识别 v0.2 或规划 2.0 的模型协议。

新增账号工作区数据：`courses` 为 `{id,name,teacher,notes,url}[]`（名称/编号唯一，HTTP(S) 无凭据链接）；`focus` 为 `{duration,remaining,running,endsAt,taskId}`（秒单位 duration/remaining，duration 为 1500 或 3000，endsAt 为 Unix 毫秒，taskId 可空）。沿用 `PUT /api/data/:name` 的版本校验，关联任务必须属于当前账号；无数据的旧账号使用空课程及默认暂停计时，无须迁移已有任务。

计时只在开始、暂停、重置、选择任务或时长时保存，不每秒写数据库；界面按 endsAt 计算剩余秒数。多窗口不实时推送，冲突后可读取服务器最新计时。到时不自动生成完成反馈，避免将经过时间当成真实投入。课程名称与 Task.course 精确匹配，课程改名不批量改写任务。

设置页 JSON 导出 workspace 与当前用户识别批次，不包含密码/会话；ICS 导出任务 DDL 和计划，不包含已取消任务，不改变数据库。仅本地下载，无外部日历授权/同步或 JSON 恢复导入。

数据库 server/data/niuma.sqlite3（NIUMA_DB 可覆盖），表：
users（邮箱唯一、昵称、随机盐、PBKDF2-SHA256 600000 次哈希）；sessions（会话令牌摘要、用户、24h 过期）；workspace（用户+数据名主键，JSON value、整数 version）；proposals（用户、任务快照版本、模型结果、是否已应用）；recognition_batches（用户+请求 ID、输入指纹、原始请求及规范化结果、演示标记）；recognition_candidates（服务端生成存储 ID、所属用户及批次、完整候选、pending/applied/ignored、版本、确认请求）。

workspace 数据名：tasks、candidates、availability、feedback。任务沿用前端 Task，新增 planningExcluded:boolean；默认 false。due 为 null 或 {date,precision:'date'|'datetime',time?}，date-only 不补伪造的精确时刻。Plan 包含 id/title/startAt/endAt/origin/isLocked；已有计划保持原样。Task.sourceRequirements:string[] 独立保存原文要求，不自动生成 subtasks；recognitionHistory 保存候选存储 ID、识别请求 ID、候选 ID 和确认操作，完整证据在识别表中留存。

浏览器只保存 HttpOnly、SameSite=Strict 的不透明会话 Cookie，Path=/api，业务数据仅在 React 内存缓存。登录页不自动选择测试账号、不自动提交登录。刷新后需登录，数据库数据保留。退出撤销服务端会话。旧浏览器数据不自动迁移、不删除。

这是本地开发后端而非生产部署：仅监听 127.0.0.1:1332，前端同源代理端口 1331。上线必须配置 HTTPS + Secure Cookie、生产服务进程、登录限流、完整字段校验、数据备份及迁移机制；不要公开测试账号或开发服务。

## 已实现 API

| 方法 / 路径 | 输入及行为 |
| --- | --- |
| POST /api/auth/register | email、password(8–128)、name(1–40)，邮箱唯一，注册后建立会话 |
| POST /api/auth/login | email、password，返回 user，设置 Cookie |
| GET /api/auth/me | 当前会话 user |
| POST /api/auth/logout | 撤销会话、清 Cookie |
| GET /api/workspace | 当前账号的 {数据名:{value,version}} |
| PUT /api/data/:name | {value,version}，乐观锁；版本不符 409，无权限 401 |
| POST /api/planning/proposals | 空对象；服务端读取账号当前数据，调用 Harness，校验后持久化预览 |
| POST /api/planning/apply | {requestId}；校验所属账号及任务版本，事务追加计划、标记 applied，返回 tasks value/version |

工作区保存先写数据库再更新前端。应用计划在同一事务中完成，旧任务版本、重复应用均 409。v0.2 候选确认也在同一事务更新任务和候选状态；完全相同的确认重试返回 200，不重复新增，变更操作则 409。旧 workspace.candidates 不冒充 v0.2，只保留人工编辑入口，旧版编辑保存仍是两次写入；新流程不再生成该格式。

## 规划输入：服务端发送至 HARNESS_URL

HTTP POST，Content-Type: application/json；HARNESS_TOKEN 若配置则通过 Authorization: Bearer 发送。密钥只能放服务端环境变量，不放 VITE_*。环境变量由启动命令/部署注入，Python 不自动加载 .env 文件。

```json
{
  "schemaVersion": "2.0",
  "requestId": "server-generated-id",
  "requestedAt": "2026-09-23T15:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "availability": {"startTime":"19:00","endTime":"22:00","weekdays":[1,2,3,4,5]},
  "tasks": [
    {"id":"t1","title":"需求报告","description":"完成小组需求分析","status":"not_started","priority":"high","due":{"date":"2026-09-25","precision":"date"},"subtasks":[],"plans":[]}
  ],
  "existingPlans": [
    {"id":"p1","taskId":"t2","title":"讨论","startAt":"2026-09-24T19:00:00+08:00","endAt":"2026-09-24T20:00:00+08:00","origin":"manual","isLocked":true}
  ],
  "completionHistory": [
    {"taskId":"done1","actualMinutes":85,"recordedAt":"2026-09-23T06:00:00Z","task":{"id":"done1","title":"上次报告","description":"类似任务","status":"completed"}}
  ]
}
```

tasks 自动选择所有未完成、未取消、没有现有执行计划且 planningExcluded 不为 true 的任务。不允许客户端额外传其他用户任务。没有规划日期范围，没有手填预计工时；主任务旧 estimatedMinutes 不作为输入。weekdays 使用 JavaScript 0=周日。时区只在接口内部保留，不反复展示。

Harness 必须自行评估工时、规划跨度、合理分块和休息；所有无 DDL 任务也要评估，不得静默丢弃。安排不下时提供明确 warnings，不延后真实 DDL。未取消任务的已有安排视为不可重叠占用；无 endAt 的旧计划暂按 1 小时占用（正式数据应要求 endAt）。部分已安排任务的“剩余工作量再规划”尚不在本版范围。

## Harness 返回

同步 200 JSON（暂不支持异步 job/202、流式输出、Markdown code fence）：

```json
{
  "estimates":{"t1":90},
  "blocks":[
    {"taskId":"t1","title":"撰写需求报告","startAt":"2026-09-23T19:00:00+08:00","endAt":"2026-09-23T20:30:00+08:00"}
  ],
  "warnings":[]
}
```

每个输入任务必须有正数分钟 estimates；blocks 可以为空，但要解释无法安排原因。后端补充 requestId。前端只展示模型评估，不允许修改为手填工时。

服务端检查：任务归属、工时数值、起止有效性、可用星期/时段、不得安排过去、不得跨日或超过 DDL、不得与已有或新增安排冲突、每任务计划分钟不超过模型估时。不足工时自动补 warning。前端必须预览后确认，不可自动应用。前端 30 秒超时/取消，后端 Harness 25 秒超时；取消不代表远端推理一定终止，但未确认结果不会进入日历。

模型输入中的来源正文/任务文字是不可信数据；Harness 系统指令必须隔离提示注入，不执行正文中的命令、不访问凭据、不直接写数据库、不改真实 DDL。

## 个性化反馈

设置页每次随机最多抽取三个已完成主任务。用户输入实际分钟数，数据库按 taskId 保存最新一条反馈；服务端拒绝未完成或不属于当前用户的任务 ID。再次反馈同一任务会替换旧值，未填表单不会保存。删除任务后的孤立反馈不会发送给模型。

每次规划附带已完成任务的标题、说明等上下文和实际用时，由模型判断相关性与不确定性。不是训练/微调，也不承诺反馈一定提升模型效果。后续团队可扩展预测误差、任务类别校准和反馈质量评估，不在前端伪造用户画像。

## 本地验收

进入 frontend/ 后执行 npm ci；npm run db:seed；npm run dev:api；另开终端在 frontend/ 执行 npm run dev。
测试账号 demo@niuma.test / Niuma-Test-2026!，seed 幂等且不建立会话。
npm test 覆盖账号隔离、数据库落盘、错误密码、退出撤销、并发版本、反馈归属、模型约束等；npm run build 验证前端。
真实模型质量验收仍须配置 TEXT_HARNESS_URL / HARNESS_URL 后进行，本地测试替身不代表模型效果。

识别侧 Python 使用 zoneinfo 验证时区；若系统没有 IANA 时区库（部分 Windows 环境），运行 python -m pip install tzdata。Windows 没有 python3 命令时，使用 py -3 ../server/app.py --seed 和 py -3 ../server/app.py。

# 文本识别契约 v0.2（定稿）

本项目正式采纳 not_provided、context.platform、evidence.supports；保留无 DDL 任务，原文要求字段固定为 sourceRequirements。v0.1 输入/输出不自动升级，版本不支持明确失败。

## 1. 职责和出口

文本识别核心只输出候选事项或候选变更，不读写用户数据库，不执行新增/修改/取消，不规划时间，不评估工时，不调用消息中的命令、不自动访问链接或附件。
身份校验、数据授权、源记录完整性、凭据剔除由调用方承担，不能交给模型。

正式出口必须是结构化对象；文本由同一对象渲染，不要求下游反解析自然语言。
识别核心实现语言不限定；本地网络封装见下方已实现 HTTP 约定。TEXT_HARNESS_URL 专用于识别，HARNESS_URL 专用于规划。

下文核心对象全部显式提供所列字段。未知为 null，空集合为 []，不能省略字段或用字符串 "null"。扩展字段留在适配层，不自动成为模型指令。

## 2. 输入

请求：{contract_version:"0.2", request_id:非空字符串, records:Record[]}。

request_id 由调用方生成，用于关联一次逻辑处理。同一 ID 不得对应不同内容；相同 ID 和同内容重试返回已保存批次；相同 ID 不同内容返回 409，核心的 candidate_id 仍不承诺跨运行稳定。
版本不支持、请求结构不合法、主记录 input_id 重复：整个请求 failed，不部分猜测修复。records=[] 合法。

Record 字段：

| 字段 | 类型与含义 |
| --- | --- |
| input_id | 非空字符串，主 records 内唯一；本地 ID 带命名空间，不冒充平台 ID |
| text | 字符串，保留实际段落和日期原文 |
| text_quality | complete / partial / summary / missing，不由模型提升完整度 |
| platform | teaching_web / wechat / email / web / unknown，由调用方提供 |
| published_at | {value,precision,basis}，见下 |
| collected_at | 带偏移日期时间或 null，只用于追踪，不能代替发布时间 |
| timezone | 时区名称或 null |
| timezone_basis | source / caller_default / unknown；unknown 时 timezone=null，其余必须非空 |
| source_url | 已剔除凭据的上游 URL 或 null，不保证可访问 |
| source_label | 上游标题、群名等或 null |
| source_item_id | 原平台 ID 或 null |
| course_or_project | 上游明确归属或 null，组织者不自动当课程 |
| context | Context[]，只放同一授权范围内必要上下文 |
| upstream_hints | 对象或 null，非权威；不得覆盖原文。允许引用的原始字符串置于 raw 下 |

published_at={value,precision,basis}：
precision=datetime 对应带偏移完整时间，date 对应 YYYY-MM-DD，unknown 对应 null。
basis=source/upstream_unverified/unknown，未知值的 basis=unknown。人为补成午夜的日期仍为 date。
只有 source 级别发布时间才能无假设换算相对日期；上游未经核实的基准至多用于 inferred，并标待核实。
可信日期可支持“明天”；“24 小时后”必须有可信精确时刻。

Context={input_id,text,published_at,timezone,platform,source_url,source_label}。platform 未知显式 unknown。
context.input_id 不能与任意主记录 ID 冲突。同一上下文被多条记录复用时 ID 和内容须一致；同 ID 不同内容使请求 failed。
context 内 ID 也不得重复。候选证据仅可引用触发记录自身及其 context，不能任意引用别的用户或别的记录内容。

空白 text 或 text_quality=missing 作为该记录 invalid_input，即使有 hints 也不能声称完整正文识别成功。
partial/summary 可以处理，但 record_results.warnings 必须提示覆盖范围；no_tasks 只能解释为已提供内容未发现任务。

## 3. 输出及状态不变量

输出={contract_version,request_id,status,record_results,tasks,errors}。版本和 request_id 必须对应请求。
无合法 request_id 的结构错误可在输出中使用 request_id=null，错误归请求级。

| status | 条件 |
| --- | --- |
| success | 所有记录处理成功，tasks 非空 |
| no_tasks | 所有记录处理成功，tasks 为空；空 records 也返回此状态 |
| partial_success | 至少一条成功处理（包括 no_tasks），且至少一条 failed |
| failed | 请求级错误，或非空 records 全部 failed |

record_results 每项={input_id,status,task_ids,warnings}。
status=success 时 task_ids 非空；no_tasks/failed 时 task_ids=[]。
合法请求每个输入恰有一个结果。tasks 中 candidate_id 全局唯一，每个候选恰好被其 input_id 对应结果引用一次；不得出现孤立 ID 或跨记录错误归属。
解析/验证导致某记录输出不可用时，该记录整条 failed，不默默删除坏候选后声称成功。跨记录语义合并不属于本版核心。

请求级失败返回 tasks=[]、record_results=[]；记录级失败保留其他记录的可用候选。错误必须可定位。
errors 每项={input_id,code,message,retryable}；请求级 input_id=null，记录级引用存在的输入；failed 记录至少有一个 error。
建议 code：invalid_request、invalid_input、unsupported_version、agent_unavailable、agent_timeout、invalid_agent_output。
message 不泄露私人正文/凭据。retryable 只是重试建议，不允许无限重试。未知日期、候选歧义不是技术失败。

## 4. 候选事项

| 字段 | 类型与约束 |
| --- | --- |
| candidate_id | 非空字符串，本次输出内唯一，不是数据库主键 |
| input_id | 触发记录 ID |
| title | 非空字符串，行动名称，不凭空加要求 |
| description | 字符串，原文行动要求及对象 |
| prerequisites | 原文要求步骤的字符串数组，不是模型新增的执行计划，也不带工时 |
| course_or_project | 字符串或 null |
| course_basis | provided / inferred / unknown；unknown 时归属为 null |
| notice_type | create / update / postpone / cancel / uncertain；只描述通知性质 |
| target_reference | 原文或上下文可确定的目标描述或 null，不猜旧任务 ID |
| deadline | 见第 5 节 |
| needs_review | 有影响理解的不确定性则 true；不代表是否需要用户确认落库 |
| review_reasons | 具体核实原因数组；needs_review=true 时非空，否则 [] |
| sources | 至少一条来源对象 |
| evidence | 至少一条可校验的证据对象 |

明确的独立交付/独立期限形成独立事项；同一行动必要步骤才合并进 prerequisites。
纯活动介绍不强行生成待办；有行动但对象或参与意向不明则保留待核实候选。这是本版固定的产品默认值。

source={input_id,platform,url,label,source_item_id}，必须能定位触发记录或其上下文；未知项 null，platform 可 unknown。
上下文没有 source_item_id，输出该值为 null。模型不能补造链接、课程全称、平台 ID。

evidence={input_id,field,quote,supports}：
- quote 非空且必须为指定输入字符串的精确子串，模型概括不能充当引文。
- field 使用 text 或 upstream_hints.raw 下真实存在的字符串路径；context 通过对应 input_id 定位，field=text。
- supports 为非空去重数组，元素来自 title/description/prerequisites/course_or_project/notice_type/target_reference/deadline。
- 每项候选必须有支持 title 的证据；非 unknown/not_provided 的 deadline 必须有 deadline 证据；非 create 的通知类型、非空目标和课程推断必须有对应证据；非空 prerequisites 也必须有支持它的证据。
- 精确子串验证是必要条件，不足以证明推理正确；需要检查引文是否真正支持结论。
- partial/summary 文本和任何影响行动理解的推断需 needs_review=true，并说明限制；普通非课程任务无归属不自动待核实。
- 不强求上游字段推断成正文证据：课程 provided 可直接追溯输入 course_or_project；相对日期换算同时核对 published_at 和 timezone 的可信度。

所有候选，即便 needs_review=false，也要经用户确认才能进入正式任务。这一布尔值不是自动执行授权。

## 5. 时间语义

deadline 固定字段：{value,precision,timezone,kind,status,raw,basis,alternatives}。

| 字段 | 约束 |
| --- | --- |
| value | 带偏移日期时间 / YYYY-MM-DD / null |
| precision | datetime / date / unknown，必须与 value 一致 |
| timezone | 来源或调用方明确提供的时区名称；未知 null，不能默认服务器时区 |
| kind | deadline / reference / unknown |
| status | explicit / inferred / unknown / conflict / not_provided |
| raw | 实际时间原话或 null；不是生成的总结 |
| basis | 非空文字，解释来源、换算或不确定性 |
| alternatives | conflict 时列出候选时间字符串，其余 []；无法可靠归一化的冲突可 []，但 raw/basis 必须说明矛盾 |

规则：

1. unknown/conflict：value=null、precision=unknown、needs_review=true。conflict 不随意选早/晚值。
2. not_provided：已提供的完整正文没有针对该行动的时间要求，value=null、precision=unknown、kind=unknown、raw=null、alternatives=[]；无需仅因缺期限设置 needs_review。部分正文遗漏时间则用 unknown。
3. explicit 表示明确的截止语义和可确定时间；inferred 需核实。reference 一律 inferred，绝不自动进入真实 DDL。
4. “明天 18 点前交”只有在可信发布时间和明确时区下才可无假设换算；采集日不充当基准。“24 小时后”不能只凭发布日期。
5. “周五”“尽快”缺少唯一解释用 unknown；“本周五”在可信基准下可解析。缺年份的“9 月 30 日”按原消息可信发布时间选择最近的相邻年份；缺发布时间时可用该消息采集时间低置信度估年，输出 inferred、具体值与依据。采集时间不能代替发布时间换算“明天”。
6. “明晚开会，请提前加群”的会议时间可以作为 reference，不能当作明确加群 DDL。只有活动时间、没有行动要求的输入可 no_tasks。
7. 原文有本地时刻但无时区，不能输出伪造偏移的 datetime；保留 raw，提示核实。date 精度不补 23:59，过期不等于已完成。
8. 同一行动相互冲突的日期使用 conflict；不同义务各自的日期不是冲突，应分开输出事项。
9. datetime 的偏移须和时区/日期一致；时区存在夏令时歧义或缺口且无法确定时，用 unknown/conflict，不由模型猜。date-only 不作跨日时区转换。
10. 取消没有新期限可以 not_provided；延期但缺少新日期应 unknown，而不是编造新期限。

## 6. 下游展示和确认规则

先展示状态、成功/失败记录数和覆盖范围，再展示行动、原文要求、截止/参考时间、来源、证据、核实原因。
unknown 显示“截止日期待核实”，not_provided 显示“原文未提供截止日期（可保留无 DDL）”，reference 明确“参考时间，不是真实 DDL”。
冲突显示原话和候选；no_tasks 显示“已处理的文本中未识别到待办”；failed 不显示“暂无任务”。

下游适配规则（本地前后端已实现，不属于识别核心输出字段）：
- 创建稳定的存储 ID，保留 contract_version/request_id/candidate_id 和完整 recognition 对象。
- 同一已落库识别结果的重放幂等；跨请求重复、源记录修订需要独立匹配策略，不能只用标题或 candidate_id。
- create：用户核对后确认；reference/unknown/conflict/inferred 不自动复制到 Task.due。用户可确认无 DDL，也可提供明确修正。
- update/postpone/cancel/uncertain：显示候选变更，人工确认目标和差异。匹配不到已有任务时不能偷偷创建或删除；可明确忽略或由用户另行手动操作。
- 确认时在一个事务内更新任务及候选状态，并检查用户、候选/任务版本；重复确认不重复新增。
- 原文要求独立保存为 sourceRequirements，不要直接当作模型估时或执行时段。
- 所有确认后的任务才可进入独立规划。规划模型估时并读取实际完成反馈，识别模型不承担此职责。

## 7. 验收与迁移

配套 [人工请求/期望输出样例](examples/text-contract-v0.2.json) 包含有 DDL、无 DDL、输入缺失和部分失败，不是模型实测结果。
还必须覆盖：取消/延期、reference、相对日期缺基准、来源摘要、伪造引文、重复 ID、错误版本、空批次、同消息多个独立义务、跨用户输入拒绝。

本版不自动适配 v0.1。迁移时不得从一条泛化 evidence 凭空补 supports；不能把 unknown 自动升级为 not_provided。需要原始 records 或重识别/人工核实。版本不支持时明确失败。
本地网络封装、大小限制、认证及幂等规则如下；本版只支持同步 JSON，不支持 202 作业或流式文本。


## v0.2 HTTP 封装（已实现）

所有 /api/recognition/* 使用现有 Cookie 会话，服务端决定 user_id，客户端和模型都不能指定归属。

| 接口 | 输入 | 输出 |
| --- | --- | --- |
| GET /api/recognition/batches | 无 | 当前用户 Batch[]，包括已处理候选和失败记录 |
| POST /api/recognition/batches | 完整 v0.2 请求 | 当前用户 Batch[]；候选未确认前不写 Task |
| POST /api/recognition/demo | {} | 持久化人工协议样例后返回 Batch[]，同账号重复加载不重复创建 |
| POST /api/recognition/candidates/:storageId/resolve | 下述确认对象 | {batches:Batch[]}，任务通过 GET /api/workspace 刷新 |

Batch={request:原始请求,result:规范化v0.2结果,demo:boolean,items:StoredCandidate[]}。
StoredCandidate={id:服务端稳定存储ID,candidate:完整候选,state:pending|applied|ignored,version:整数}。
task_ids 始终引用核心 candidate_id，HTTP resolve 使用 items.id，两者禁止混用。

网络状态：
- 200：成功接收并保存可展示批次，包括 no_tasks、partial_success、核心 failed；业务失败看 result，不只看 HTTP 状态。
- 400：不合法请求、未核对确认、操作不符、目标缺失、日期格式错误。
- 401：未登录或会话失效；404：不存在或不属于当前用户的候选。
- 409：同 ID 输入变化、候选已被不同操作处理、任务版本变化。
- 502：识别服务不可用/超时、JSON 或全局关联结构损坏；不保存不可信结果。
- 503：未配置 TEXT_HARNESS_URL。
网络错误体为 {error:可展示消息}，与核心 result.errors 数组分开。

请求体上限 2 MB，records 最多 100 条，每条 text 最多 100000 字符。前端当前一次提交一条粘贴正文，来源适配器可提交批次。
输入字段未知必须显式 null。手填发布日期只能标 upstream_unverified，不能冒充 source；前端不收集原文精确发布时间，可信发布时间及复杂上下文由来源适配器提供。
采集时间只追踪，不作相对日期基准。前端重试保留原 request_id 和 collected_at；“重新识别（新批次）”生成新 ID。跨批次相似任务不自动合并，由用户核对。

服务端向 TEXT_HARNESS_URL POST 完整 v0.2 请求，返回值必须是裸 v0.2 输出 JSON。
TEXT_HARNESS_TOKEN 若配置则发送 Authorization: Bearer。不要把服务凭据放到 VITE_* 或记录正文里。
服务端超时 25 秒，前端 30 秒。停止等待不撤销可能进行中的服务端处理，应刷新批次后决定是否重试。
同用户同 request_id 已有结果则返回存储结果；并发重复请求可能发生多次模型调用，但数据库只保存一个批次。未实现跨进程推理锁。

校验责任：
- 请求版本/类型/主 ID/上下文冲突、结果版本与 request_id、记录归属/候选 ID/聚合状态结构先校验，结构无法安全定位时整次拒绝。
- 单条记录内任一候选的证据、字段或时间无效，整条记录降级 failed、候选全移除，补 invalid_agent_output，其他记录保留；重算聚合状态。
- 覆盖范围警告缺失时由服务端补充。证据验证精确子串及 supports，来源字段必须与输入一致，raw 必须来自实际正文或 raw hints。
- 日期有效性、时区偏移一致性由服务端验证；语义正确性、相对日期推理、上下文授权和凭据清理仍需来源适配器/Harness负责。程序校验不等于证明模型理解正确。
- UI 将正文当普通文本渲染，不执行 HTML；来源链接仅允许无内嵌用户名/密码的 HTTP(S)。来源适配器还必须剔除查询参数中的访问凭据。

## v0.2 确认操作（已实现）

```json
{
  "version": 1,
  "tasks_version": 3,
  "action": "postpone",
  "target_id": "existing-task-id",
  "reviewed": true,
  "draft": {
    "title": "原任务标题",
    "description": "原任务说明",
    "due": {"date":"2026-10-03","precision":"date"}
  }
}
```

action=create/update/postpone/cancel/ignore。
- create 必须不带 target_id；update/postpone/cancel 必须选择属于当前账号的目标任务。禁止按标题自动匹配。
- action 必须与 notice_type 一致；uncertain 需用户明确选择操作。ignore 只改变候选状态，不写任务，不要求目标或 tasks_version。
- 除 ignore 外 reviewed 必须 true，前端默认不勾选。检查的是整个 tasks 集合版本，任何任务变化都要求刷新后重新核对，宁可保守冲突也不覆盖。
- create 保存确认后的 title/description/due，draft.course 可明确修改课程归属或设为 null；省略时使用候选归属。sourceRequirements 来自原文 prerequisites，subtasks/plans 初始化为空；不默认估时。
- update 只更新用户确认的 title/description/due 以及显式提交的 draft.course；有新的非空 prerequisites 时更新 sourceRequirements，否则保留原要求。显示旧值和新值，由用户明确核对。
- postpone 仅修改 DDL，必须有明确新日期；不改标题、说明、原文要求和计划。日期变化不自动重排已有计划。
- cancel 设置 status=cancelled，不物理删除任务/历史计划；日历和今日安排不显示，规划时不占用可用时间。可在任务“已取消”筛选查看。
- 参考时间、模糊时间或无期限均不自动填 DDL。更新已有任务时默认保留其已确认 DDL，用户可明确清空或修改。
- 仅 explicit+deadline 可以预填，但仍需确认。datetime 按真实瞬时转换到北京时间，date-only 原样保留；源时间含非零秒/毫秒时不预填，避免静默丢失精度，用户核对到分钟后填写。
- 同一个确认请求完全相同地重试返回 200，不再写任务；候选已处理后换操作/目标/内容返回 409。识别候选原文、完整结果和确认请求留在数据库。
- 前端不会把模型 candidate_id 当数据库任务 ID；确认成功才刷新任务清单，随后由用户单独开始规划。

## v0.2 前端验收入口与剩余外部工作

登录后打开“待确认” → “加载协议演示”：看到部分成功、输入缺失错误、明确 DDL、可保留无 DDL、证据引用和已处理状态。
“核对并确认新增/变更”展示独立审核表单；未核对、未匹配目标或未明确不确定通知操作时不可提交。
输入原文 → “识别为候选”调用真实识别封装；没有 TEXT_HARNESS_URL 时明确失败，不用样例替代实际输入。

本地 UI、传输适配、契约验证、数据库事务及样例已实现。真实来源采集、识别模型部署/质量、生产限流/备份/迁移运维仍由后端和 Harness 团队完成。
旧版候选和 v0.1 原稿只作历史参考，不能继续用旧的“直接确认新增”路径消费 v0.2 变更通知。

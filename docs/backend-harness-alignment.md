# 牛马管理器：前端 / 后端 / 模型 Harness 对齐说明

版本 2.0 · 2026-09-23。本版替代旧的浏览器存储和前端演示规划协议。

## 已实现边界

- 前端：注册/登录/退出、任务与候选结果、可用时段、持久化“无需规划”选择、个性化用时反馈、规划预览与确认。
- 本地后端：Python 3.9+ 标准库 HTTP API + SQLite，所有读取/写入由 Cookie 会话确认用户。前端不提交 userId，不持久化业务数据。
- 模型 Harness：待团队实现/配置真实服务。前端与后端均不提供默认 1 小时或规则估时的假模型。未配置 HARNESS_URL 返回 503。
- 来源同步仍为演示；明确日期提取仍为本地规则。它们只生成主任务和 DDL，不自动规划。

## 数据与会话

数据库 server/data/niuma.sqlite3（NIUMA_DB 可覆盖），表：
users（邮箱唯一、昵称、随机盐、PBKDF2-SHA256 600000 次哈希）；sessions（会话令牌摘要、用户、24h 过期）；workspace（用户+数据名主键，JSON value、整数 version）；proposals（用户、任务快照版本、模型结果、是否已应用）。

workspace 数据名：tasks、candidates、availability、feedback。任务沿用前端 Task，新增 planningExcluded:boolean；默认 false。due 为 null 或 {date,precision:'date'|'datetime',time?}，date-only 不补伪造的精确时刻。Plan 包含 id/title/startAt/endAt/origin/isLocked；已有计划保持原样。

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

工作区保存先写数据库再更新前端。应用计划在同一事务中完成，旧任务版本、重复应用均 409。普通任务和候选结果仍分别保存，后续正式服务应提供候选确认事务，避免两次写入之间发生网络中断造成不一致。

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

Harness 必须自行评估工时、规划跨度、合理分块和休息；所有无 DDL 任务也要评估，不得静默丢弃。安排不下时提供明确 warnings，不延后真实 DDL。已有安排视为不可重叠占用；无 endAt 的旧计划暂按 1 小时占用（正式数据应要求 endAt）。部分已安排任务的“剩余工作量再规划”尚不在本版范围。

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

npm ci；npm run db:seed；npm run dev:api；另开终端 npm run dev。
测试账号 demo@niuma.test / Niuma-Test-2026!，seed 幂等且不建立会话。
npm test 覆盖账号隔离、数据库落盘、错误密码、退出撤销、并发版本、反馈归属、模型约束等；npm run build 验证前端。
真实模型端到端质量验收须待团队提供 HARNESS_URL 后进行，本地契约测试只用测试替身。

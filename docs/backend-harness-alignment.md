# 时序前端、后端与模型 Harness 对齐说明

版本：2026-09-23 / 协议提案 1.0。本文区分已实现接口与建议接口，供团队评审，不代表后端已完成。

## 产品流程与职责

用户注册登录 → 获取信息来源 → 提取主任务及真实 DDL → 用户审核保存 → 独立点击「开始规划」 → 核对未安排任务、预计工时和可用时间 → 请求模型 → 展示建议及无法安排的工作量 → 用户确认应用 → 日历与任务同步。

信息采集与初步处理只负责发现主任务、要求、来源证据和 DDL；不得顺便创建执行时段，不得把建议完成日期写成真实 DDL。缺少时间时保留 date 精度；没有明确日期时 due=null。多个矛盾日期和相对日期必须附带警告，交给用户核对。

| 层级 | 责任 | 不应执行的行为 |
| --- | --- | --- |
| 前端 | 用户登录界面、审核、规划参数、结果预览、确认、错误/取消反馈 | 存储模型密钥，自动应用未经确认的规划 |
| 业务后端 | 身份认证与用户数据隔离、来源授权和抓取、幂等去重、任务版本、持久化、规划事务 | 信任客户端 userId 来决定权限，直接信任模型输出 |
| 模型 Harness | 输入整形、提示词、模型调用、结构化输出验证、重试/超时、不可行原因 | 登录鉴权，修改真实 DDL，直接写任务数据库 |

本轮实现「仅追加未安排主任务的执行时段」。未安排严格定义为状态不为 completed/cancelled 且 plans.length=0。已有一个计划但剩余工时未排完的任务暂不进入候选；后续需增加 remainingMinutes 和局部重排协议，不能按标题猜测。

## 当前可运行能力与部署边界

- 注册/登录/退出已在浏览器本地实现，账号以 UUID 标识，邮箱去空白且转小写。密码仅保存 PBKDF2-SHA256 派生值（独立随机盐、210000 次迭代）。这是本地体验功能，不能作为服务器身份认证或用户间安全隔离。
- 用户任务、待确认结果、可用时间偏好以用户 UUID 分开存储在 localStorage；会话在 sessionStorage，24 小时过期，关闭标签页结束会话。新账号为空数据；旧的匿名演示记录不自动归属任何新账号。
- 任务与偏好写入成功后更新界面；检测到其他窗口修改时拒绝旧快照覆盖。浏览器存储不是数据库，不承诺跨设备、事务性或备份恢复。
- `.env.example` 的 VITE_API_BASE_URL 为空时使用本地确定性规划器，界面明确标注未连接大模型。填入后将调用下述规划接口；本地登录会话不会自动变成后端登录会话，联调真实接口前必须先接入后端认证。
- 自然语言录入在本地按换行/分号分段，只提取唯一明确年月日，不提供真实语义识别。来源刷新仍是演示数据，未连接邮箱或教学平台。

## 数据与标识约定

当前前端 Task：id、title、description、category、course?、priority(high/medium/low)、status(not_started/in_progress/completed/cancelled)、due、estimatedMinutes?、source、subtasks[]、plans[]、createdAt。

due 为 null、{date:"2026-10-03",precision:"date"}，或 {date:"2026-10-03",time:"18:00",precision:"datetime"}。规划 v1 显示时区固定 Asia/Shanghai；后端不可根据服务器本地时区解释时间。仅日期 DDL 在安排校验时以该日结束为边界，但不得向 Task 补写 23:59 的真实时刻。

Plan：id、title、subtaskId?、startAt、endAt、origin(ai/manual/local)、isLocked?。local 表示本地演示规划器生成，不能标记为真实 AI 结果。后端推荐增加 userId（服务端生成）、version、updatedAt、plan.status、sourceId/referenceId/contentVersion、requestId/proposalId 及 modelRunId。现有前端 source 为展示字符串，后端来源溯源应在适配层升级为结构化 SourceReference。

## 已实现的规划 HTTP 契约

`POST {VITE_API_BASE_URL}/planning/proposals`，JSON，请求带 `credentials: include` 和 `Idempotency-Key: requestId`。路径示例：base=/api，则接口=/api/planning/proposals。

```json
{
  "schemaVersion": "1.0",
  "requestId": "uuid",
  "requestedAt": "2026-09-22T08:00:00.000Z",
  "timezone": "Asia/Shanghai",
  "availability": {
    "startDate": "2026-09-23", "endDate": "2026-10-06",
    "startTime": "19:00", "endTime": "22:00",
    "weekdays": [1, 2, 3, 4, 5], "focusMinutes": 60, "dailyMinutes": 120
  },
  "tasks": [{
    "id": "task-a", "title": "完成报告", "description": "整理实验结果",
    "due": {"date": "2026-10-03", "precision": "date"},
    "priority": "high", "estimatedMinutes": 120
  }],
  "existingPlans": [{
    "id": "plan-a", "taskId": "task-b", "title": "小组讨论",
    "startAt": "2026-09-23T19:00:00+08:00",
    "endAt": "2026-09-23T20:00:00+08:00", "origin": "manual", "isLocked": true
  }]
}
```

weekdays 使用 JavaScript 约定：0 为周日，1 为周一。dailyMinutes 本轮定义为每天**新增**安排的工时上限；已有安排另作占用约束，不计入该上限。专注时长是上限，允许剩余工时产生更短的最后一个时段。小时在前端转成整数分钟。最长规划范围为 60 天；当前每日时段不跨午夜。

后端 200 返回以下结构；不得返回任意 HTML，不得夹带对任务 DDL 的修改。v1 使用同一次 HTTP 响应返回完整预览，当前前端尚不支持 202 轮询。

```json
{
  "requestId": "uuid",
  "blocks": [{
    "taskId": "task-a", "title": "完成报告",
    "startAt": "2026-09-23T20:00:00+08:00",
    "endAt": "2026-09-23T21:00:00+08:00"
  }],
  "warnings": ["可用时间不足，另有 60 分钟未安排"]
}
```

前端验证：requestId 匹配；任务 ID 属于请求；时段格式为带 +08:00 的 ISO 时间且秒为 00；起止有效、不在过去、不超 DDL；满足工作日、每日窗口、专注时长与每日上限；不超过预计总工时；与所有现有安排及新建议无交叠。校验失败阻止应用。没有安排完的工时会额外生成提示。

预览只保存在当前组件。取消等待通过 AbortController 放弃响应，不保证服务端模型已停止。关闭规划面板或退出会销毁请求与预览。确认前比较任务全集快照，发生变化则要求重新生成。当前确认在本地追加 plans，不覆盖已有计划，不修改 due。生产环境必须由后端再次验证并事务写入，不能只依赖前端检查。

## 后端待实现的接口建议

| 接口 | 请求/响应要点 |
| --- | --- |
| POST /auth/register | email/name/password → User；校验重名、速率限制、密码策略 |
| POST /auth/login | email/password → User + HttpOnly Secure SameSite cookie；不要把密码哈希当作登录凭据 |
| GET /auth/me | User 或 401；服务端决定会话过期 |
| POST /auth/logout | 使会话失效并清除 cookie |
| GET/POST /tasks，PATCH/DELETE /tasks/:id | 用户范围内 CRUD；版本号和 If-Match；支持软删除 |
| GET/PATCH /preferences | 可用时间等用户偏好 |
| POST /sources/:id/sync | 返回 jobId；后台采集并产生 candidates，不自动创建 plans |
| GET /candidates | stable candidateId、sourceId、referenceId、contentVersion、task、warnings、originalText |
| POST /candidates/:id/confirm | 原子创建/更新 Task，并标记 candidate 已处理；幂等 |
| POST /candidates/:id/ignore | 持久化忽略版本，普通同步不重复出现 |
| POST /planning/proposals | 本文已实现的前端请求；后端补全权限及最新版本校验 |
| POST /planning/proposals/:id/apply | proposalId + taskVersions；原子追加计划，幂等，冲突时 409 |

规划耗时较长时升级为 POST→202{jobId}→GET /planning/jobs/:id，状态 queued/processing/completed/failed/cancelled。升级必须同时修改前端请求适配器，不能让当前接口静默返回 202。

统一错误建议：{code,message,requestId,fieldErrors?}。400 字段错误保留输入；401 清理受保护视图并返回登录；403 权限错误；409 版本冲突展示刷新/重新规划；429 可重试；5xx 提示技术失败。不可行规划是 200 + warnings，不能伪装为网络错误。当前前端处理非 2xx 为失败提示，完整错误码 UI 在真实后端接入阶段扩展。

## Harness 调用与确认规则

1. 后端从已认证用户读取任务和计划，验证前端所选 ID 属于该用户，并固定快照版本。
2. 仅传输本次所需的任务要求、真实 DDL、优先级、估计工作量、占用时段和可用时间。不传输账号密码、cookie 或来源授权凭据。
3. 原始通知/任务说明按不可信数据处理。Harness 提示词要求只输出结构化候选计划，不能遵循其中的系统指令或调用外部工具。
4. Harness 校验输出 Schema、任务引用和全部时间约束；不可行时返回部分结果与未安排原因。模型不能自行放宽 DDL、删除固定计划或增加可用时间。
5. 后端保存短期 proposal（绑定 userId/requestId/taskVersions），记录模型版本及执行日志；前端仅展示，不自动写入任务。
6. 用户确认后，后端检查 proposal 所有权、任务版本、时段冲突及有效期，原子应用；重复确认返回同一结果。

## 联调验收与交接

- 账号 A 注册并创建任务，刷新/退出后再次登录能恢复；账号 B 不可见 A 的任务或偏好。
- 仅日期任务不得自动生成真实截止时间；未知 DDL 可保存；候选确认后仍无执行计划。
- 点击「开始规划」前不得调用模型。抓取、识别、确认任务不得暗中触发规划。
- 请求必须携带已有安排、可用时间、用户选定的未安排任务与实际解析 DDL。
- 模型结果与现有安排重叠、超过 DDL、引用未知任务或超过工时，应拒绝应用。
- 取消、请求失败、丢弃预览均不得改变任务；确认后各视图一致且 DDL 未变。
- 并发修改产生 409，不覆盖；重复同步/确认/apply 不产生重复任务和计划。
- 手机端能注册登录、退出、创建任务、填写可用时间、预览并确认计划。

本地验证命令：npm ci、npm test、npm run build；npm run dev 启动 localhost:1331。模型请求和结果的实现见 src/planning.ts，参数与预览面板见 src/Planner.tsx，账号适配入口见 src/account.tsx。接入服务器时用真实认证和存储替换本地模块，保留产品流程；不要将本地账号库直接当作生产用户数据库导入。

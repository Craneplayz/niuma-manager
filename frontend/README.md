# 牛马管理器前端

当前网页围绕两件事组织：导入通知，以及查询和处理任务。全部任务标题右侧的双段开关切换清单与日历，不再单设标签栏；导入与任务共用最大宽度 1450px 的页面容器，日期位于共享紧凑顶栏右上，单行标题统一上移，标题行及第一控件起点一致，860px 阅读区左对齐，日历使用较宽区域。临近截止与已完成固定使用清单；账号入口固定在侧栏底部。当前界面规格见 [设计说明](docs/design.md)，模块边界见 [维护指南](docs/maintenance.md)。

## 功能与数据

- **导入**：粘贴文本，或连接教学网后台采集与识别作业、通知及附件。明确任务由后端直接保存，疑点结果进入待核对列表；摘录、完整原文和来源链接可分别查看。同步进度可恢复、停止，失败或未完成通知可单条 / 批量重试，待补全来源须重新采集。导入页底部折叠入口支持主动重识别已保存的 v0.2 通知与原生教学网 v0.4 快照。邮箱显示禁用入口，尚未接入。
- **任务**：添加、编辑、完成、重新打开、删除和恢复。删除先进入账号回收站；有记录时，任务页底部显示回收站入口。
- **通知**：依据现有 `content_type=notice` 在全部任务下方提供独立清单，沿用查询、详情、编辑和回收站；浅灰“通知”标签区分类型，列表左侧和详情底部都可勾选、取消已阅读。阅读状态复用已有任务状态并同步保存，通知入口保留全部通知，已阅读项同时进入已完成。
- **查询**：全部任务、通知、临近截止、已完成共用关键词、课程、来源和日期筛选。全部任务清单只包含未完成任务；临近截止包含逾期及今天起未来 7 天的未完成任务和未阅读通知；已完成混合展示已完成任务和已阅读通知。
- **日历**：仅在全部任务中提供，月、周视图展示未完成任务的截止日期及通知的关键时间，通知使用灰色卡片与文字标签，任务和通知分别计数；选择日期查看当天清单，无日期内容另有入口。切到临近截止、已完成或通知再返回，保留原月份、选中日期、月周模式、筛选与视图偏好。日期只有天时保留日期精度，具体时刻可留空。
- **账号**：侧栏底部可登录、注册、退出、修改昵称和密码。真实账号数据由服务端保存，浏览器只保留会话 Cookie 和视图偏好，不把工作区作为本地存储副本。
- **导航**：桌面固定收放按钮切换 232px 侧栏与 72px 图标栏；任务区域依次为全部任务、通知、临近截止、已完成。折叠后仍可直接打开各入口和账号，图标悬停约 500ms 或获得键盘焦点时显示名称，手机沿用抽屉菜单。

当前前端不提供规划、任务拆分、学习时段或专注计时入口，也不包含历史任务迁移和旧界面数据转换。客户端工作区仅有任务和回收站，保存只写任务；来源证据继续通过服务端接口读取。服务端要求 `plans`、`subtasks` 为数组，新任务提交空数组，已有字段直接透传。

## 目录结构

`src/` 按入口拆分模块；测试覆盖当前前端功能与接口适配。

```text
frontend/
├── README.md
├── CONTRIBUTIONS.md          历史贡献与当前设计来源
├── index.html                应用、通用弹窗和任务抽屉
├── admin.html                独立管理员入口
├── favicon.svg
├── package.json / package-lock.json
├── vite.config.ts            开发端口与同源 API 代理
├── src/
│   ├── app.js                页面组织、筛选、日历导航和任务交互
│   ├── page-layout.js        共用单行标题、控件与操作渲染器
│   ├── account-ui.js         登录、注册、昵称和密码表单
│   ├── usage-feedback.js     账号中的主动反馈与非阻塞评分
│   ├── admin-usage.js        独立后台统计、日期筛选和反馈列表
│   ├── admin-usage.css       独立后台的桌面与手机样式
│   ├── import-ui.js          文本、教学网采集与候选核对
│   ├── task-relations-ui.js  跨来源关联进度、依据、确认、保持分开和撤回
│   ├── import-evidence.js    摘录、完整通知、来源链接和失败身份
│   ├── reprocessing-ui.js   已保存通知选择、重识别进度与停止
│   ├── materials-ui.js       主动上传、文件任务恢复及失败重试
│   ├── public-sources-ui.js  公开活动预览、选择和幂等加入通知
│   ├── submissions-ui.js     提交状态、完整来源和永久行动关联
│   ├── source-schedule-ui.js 定时来源、间隔与明确同意设置
│   ├── source-sync-ui.js    后台教学网同步、进度恢复与失败重试
│   ├── task-model.js         日期、筛选、排序与截止分组
│   ├── task-views.js         任务行、分组清单和当日面板
│   ├── calendar.js           周一开头的月历与周截止日程
│   ├── calendar.css          日历、任务清单和当日面板样式
│   ├── task-drawer.js        任务详情及新增、编辑表单
│   ├── task-evidence.js      原文、证据摘录和安全来源链接
│   ├── team-store.js         任务与回收站状态、账号请求和保存队列
│   ├── demo.js               仅在内存中的合成任务
│   ├── environment-badge.js  服务环境和版本描述，不生成额外页面浮层
│   ├── icons.js              共用 SVG 图标
│   ├── id.js                 浏览器兼容的随机任务 ID
│   └── styles.css            应用布局、导航、抽屉和表单
├── tests/
│   ├── task-relations.test.js 关联状态恢复、账号隔离、安全渲染和操作
│   ├── relations-smoke.mjs  同步后自动整合、提交完成、证据及撤回的浏览器验收
│   ├── task-model.test.js    筛选、截止分组、月周日历和安全渲染
│   ├── import-flow.test.js   通知时间、候选确认、教学网和账号隔离
│   ├── import-auto.test.js   后台导入、幂等恢复、停止与账号隔离
│   ├── import-evidence.test.js 原文、来源身份与安全渲染
│   ├── reprocessing-flow.test.js 通知选择、持久化运行与停止
│   ├── interface-smoke.mjs  真实浏览器到临时 HTTP/数据库的合成流程验收
│   ├── import-background.test.js 后台同步、保存排队与账号切换回归
│   ├── materials.test.js     上传回执、丢失响应幂等与不支持格式
│   ├── public-sources.test.js 公开活动选择导入和账号隔离
│   ├── submissions.test.js  来源完整展示、快照版本与操作重试
│   ├── source-schedule.test.js 定时间隔、同意、版本与关闭
│   ├── source-sync.test.js   后台同步、运行恢复、停止和重试
│   ├── sync-fixtures.js     后台同步契约的合成运行样例
│   ├── team-store.test.js    任务保存、账号隔离、冲突与删除恢复
│   ├── usage-feedback.test.js 访问去重、主动反馈及后台隔离
│   ├── task-evidence.test.js 来源链接和证据展示
│   └── environment-badge.test.js 环境识别与版本差异
└── docs/
    ├── design.md             当前产品规格
    ├── maintenance.md        模块职责与验证要求
    ├── screenshots/
    │   ├── task-relations-desktop.png 跨来源整合与依据的桌面验收
    │   ├── task-relations-mobile.png  跨来源整合与依据的手机验收
    │   ├── task-list-desktop.jpg
    │   ├── calendar-desktop.jpg
    │   ├── calendar-mobile.jpg
    │   ├── sidebar-collapsed.jpg
    │   ├── import-desktop.jpg
    │   ├── import-evidence-desktop.jpg
    │   ├── background-sync-desktop.jpg
    │   ├── integration-teaching-desktop.png
    │   ├── integration-public-mobile.png
    │   ├── reprocessing-desktop.jpg
    │   ├── notice-proposal-desktop.jpg
    │   ├── notice-proposal-calendar.jpg
    │   ├── notice-proposal-mobile.jpg
    │   ├── notice-read-desktop.png  列表与详情同步勾选已阅读
    │   ├── notice-read-mobile.png   手机通知阅读详情
    │   ├── notice-near-desktop.png  近期任务与通知混合展示
    │   ├── notice-done-desktop.png  已完成任务与已阅读通知混合展示
    │   ├── 2026-10-14-admin-usage-desktop.png 后台桌面布局
    │   └── 2026-10-14-admin-usage-mobile.png 后台手机布局
    └── archive/              历史文档与截图，不代表当前功能
```

## 新入口与验收

- 教学网同步后自动分析当前账号的相关原文、任务和提交状态；“跨来源整合”可查看证据、确认疑点、保持分开、撤回及重新分析，不阻塞其他导入入口。
- `node tests/relations-smoke.mjs` 使用下述相同依赖与临时服务验证关联完整流程；环境变量用法同 `interface-smoke.mjs`，截图为 `task-relations-*.png`。

- 导入 → 教学网：同意后启动后台同步；刷新可恢复进度，失败记录可单条或批量重试，缺正文/附件时重新获取来源。
- 同页“定时同步”：设置 15～10080 分钟间隔并明确同意；管理员须先在环境配置启用 `scheduled_sync`，关闭页面仍执行。
- 同页“教学网提交状态与任务关联”：先查看完整来源与全部行动，逐项关联提交行动；后续同步自动完成对应任务并撤下对应候选，用户重新打开任务会暂停来源自动完成。
- 导入 → 图片与文件：选择 PNG/JPEG/PDF/DOCX、同意上传后后台处理，失败部分单独重试，候选在同页核对；管理员须启用 `manual_materials`，此入口不连接微信。
- 导入 → 公开活动：从计算机、生科、教育学院获取近期讲座，预览后选择加入通知；活动时间放在说明，不写成作业 DDL。
- 导入页底部“重新识别已有通知”支持原生教学网；保留已确认、修改、忽略的决定，不重新采集缺失来源。
- 任务编辑可选择“任务/通知”，两者暂共用清单和截止日历；通知允许有截止日期。

真实浏览器合成验收：安装前端依赖、`server/requirements-test.txt`、爬虫依赖及 Playwright 浏览器，在 `frontend/` 执行 `node tests/interface-smoke.mjs`；可用 `NIUMA_TEST_PYTHON` 指定 Python、`NIUMA_TEST_BROWSER=msedge` 使用已安装 Edge。脚本自动启动随机端口的 Vite/临时 API，采集与模型全部为合成替身，结束关闭服务；不接触现有账号、数据库或生产配置。截图写到 `docs/screenshots/integration-*.png`，这不代替真实校园网/模型验收。

## 本地启动

前端开发与测试需要 Node.js 20.19+。从 `frontend/` 目录执行：

```bash
npm ci
npm run dev
```

网页需要可用的业务 API。本地启动当前后端时，按 [服务端说明](../server/README.md) 准备 Python 环境和依赖，再另开终端，在同一目录执行：

```bash
npm run dev:api
```

打开 [本地网页](http://localhost:1331/)。Vite 默认端口为 1331，同源 `/api` 代理到本机 1332。端口占用时先确认已有进程，不另起一个容易混淆的网页。不要在仓库根目录运行 npm。

文本识别与教学网识别由业务后端调用模型；普通本地启动读取仓库根目录 `.env`，教学网要求有效的七牛配置。显式设置 `NIUMA_ENVIRONMENT` 时，需要为该环境配置模型文件，或用 `NIUMA_MODEL_ENV` 指向已有配置文件；配置变更后重启 API。密钥不进入前端或 `VITE_*`。

教学网采集依赖单独安装在 `crawler/`，安装与浏览器要求见 [采集器说明](../crawler/README.md)。账号连接由用户在导入页管理；缺依赖、缺模型配置与连接失败会显示各自原因，单纯刷新不能代替安装或配置。

教学网同步使用 `/api/sources/sync-runs` 创建后台运行，前端只提交来源选择、操作身份和明确同意；后端负责采集、识别及任务保存，关闭网页不停止运行。旧保存通知的直接重试仅使用服务端 `request_id`，不会回传来源正文。定时配置位于同页下方，需管理员启用环境功能开关。

数据库、凭据、密钥和本地配置不提交到仓库。

## 使用反馈与后台

- 登录后在底部账号入口主动填写使用反馈；连续次日使用时出现一次可关闭的评分窗，不遮挡任务编辑。
- 成功恢复会话、登录完成、前台恢复和键盘交互按账号和北京时间日期补记访问，失败不影响任务读取；合成预览不记录访问。
- 管理员通过 `/admin.html` 独立登录查看使用统计；普通页面不加载后台图表脚本，服务端逐请求校验权限。
- 后台只展示启用统计后的记录及聚合数据，空数据不生成模拟趋势；通知阅读不计入任务完成指标。

## 合成预览

打开 [合成预览](http://localhost:1331/?demo=1)，或 [合成日历](http://localhost:1331/?demo=1&view=calendar)。`demo=1` 使用相对当天生成的合成任务；任务操作只改变页面内存，刷新后恢复初始数据，不写入真实账号或持久化任务。

合成文本与任务不会发送到识别或教学网采集服务；导入操作提示退出预览并登录。页面可能请求公开的 `/api/version` 环境信息。视图、月周模式和侧栏折叠偏好可以保留在浏览器中，合成任务本身不会保留。

## 环境隔离

上述 1331 / 1332 命令是兼容的本地启动方式。共享测试和部署使用 [统一环境入口](../server/deploy/ENVIRONMENTS.md) 与 [团队使用指南](../server/deploy/TEAM_GUIDE.md)：development 默认 1431 / 1432，test 默认 1531 / 1532，production 使用独立配置与数据卷。

各环境的数据库、会话、凭据、附件和模型配置独立；不要拿生产数据初始化开发或测试。Vite 通过 `NIUMA_WEB_PORT`、`NIUMA_PORT` 接收相应端口；同一网页构建通过 `/api/version` 识别当前服务环境，账号入口的环境信息用于核对前后端版本。部署与发布要求以 [部署指南](../server/deploy/README.md) 为准。

## 验证

运行前端模块测试与构建：

```bash
npm test
npm run build
```

`npm test` 仅运行前端测试，不安装后端依赖或运行后端测试。服务端与采集器的验证由各自模块负责。界面变更还应检查桌面与 320–390px 手机宽度，验证任务操作、筛选、日历跳转、导入核对和账号切换；演示截图只使用合成材料。当前截图与视觉规格见 [设计说明](docs/design.md)。

历史贡献与来源见 [CONTRIBUTIONS.md](CONTRIBUTIONS.md)。

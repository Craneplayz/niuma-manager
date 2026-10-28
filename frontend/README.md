# 牛马管理器前端

当前网页围绕两件事组织：导入通知，以及查询和处理任务。全部任务标题右侧的双段开关切换清单与日历，不再单设标签栏；导入与任务共用最大宽度 1450px 的页面容器，统一移除顶部浅色辅助标注及独立占位行。桌面标题与日期位于同一行，“添加任务”在日期左侧，二者整体靠右；手机将导航、操作与日期置于首行，正式标题与视图切换置于次行。清单和日历使用页面可用宽度，导入正文使用左对齐的 860px 阅读区。临近截止与已完成固定使用清单；账号入口固定在侧栏底部。当前界面规格见 [设计说明](docs/design.md)，模块边界见 [维护指南](docs/maintenance.md)。

界面采用纯白背景、珊瑚桃色主操作、深棕按钮文字、细描边与 12px 圆润控件，普通应用和独立后台共用 `src/theme.css`；侧栏品牌区、后台顶栏与后台登录使用同一扫描牛图标，左下角常驻伙伴根据已保存任务切换形象，支持悬停、聚焦和轻点对话，折叠栏与矮窗口使用紧凑尺寸。课程使用固定色与小课程签，清单、当日面板、详情和已选课程筛选保持一致，月周日历复用同色圆点与课程简称。

手动完成任务保存成功后显示底部牛马伙伴，连续完成合并并保留撤销；账号内可关闭伙伴或小动画，动作素材和角色语气见 [牛马伙伴](docs/mascots.md)。

## 功能与数据

独立 `/admin.html` 保留使用数据并提供“运行监控”切换，仅使用管理员会话读取 `/api/admin/operations`；首次不传日期（最近 24 小时），提交日期沿用使用数据筛选，监控失败单独提示，会话失效可重新验证。时间统一转为北京时间，阶段、操作与结果码显示中文；资源采样超过 180 秒标明“采样已过期”，过期或不可用的数值显示“—”。监控合成浏览器验收运行 `node tests/admin-operations-browser.mjs`，需要已安装 Playwright 浏览器；可用 `NIUMA_TEST_BROWSER_PATH` 指定既有 headless 可执行文件，设置 `NIUMA_TEST_SCREENSHOTS=1` 才更新截图，脚本结束自动关闭临时静态服务。

- **导入**：粘贴文本，或连接教学网后台采集与识别作业、通知及附件。明确任务由后端直接保存，疑点结果进入待核对列表；摘录、完整原文和来源链接可分别查看。同步进度可恢复、停止，异常统一点击“处理未完成”，系统选择直接重试识别或仅重新获取异常来源后处理；成功项和正常待核对项不重复处理，错误详情也可单独处理本条。教学网在同一操作区选择单次、常用频率或自定义间隔，主按钮在运行时变为黑底白字的“停止导入 / 停止同步”，停止中禁用，失败可重试，停止后保留所选频率及自定义分钟数；定时启动需明确授权，运行状态和进度紧贴操作区下方按需显示，历史只保留新增任务或通知、待核对、任务更新及错误，跳过或无变化的检查不占历史，同一来源的同类错误只展示最新一次，已恢复异常保留最新一条并显示灰色状态，分类标签只保留名称与历史数量，错误未恢复为红色、部分恢复为黄色、全部恢复为绿色；待核对有未处理项为黄色，全部确认或忽略后变绿；新增与修改为绿色，未解决错误和待核对优先，处理后新增、修改优先，混合记录保留真实产出；无变化时更新最近检查，手动检查另外临时提示。当前来源只保留一个历史入口，同步运行和独立识别结果按真实导入批次合并，错误在对应批次内查看，不另列“识别未完成”历史。历史记录默认折叠，点击错误 / 待核对 / 新增 / 修改数量切换紧凑多列名称列表，再点收起；名称可直达详情或核对，错误点击后查看原因及重试，不再单设“处理详情”。邮箱显示禁用入口，尚未接入。
- **图片与文件**：选择 PNG/JPEG/PDF/DOCX，明确同意后后台识别；失败部分可单独重试，完成记录折叠，候选沿用下方核对列表。
- **提交状态**：教学网页已移除“作业与提交状态”手动目录和管理弹窗，后台提交识别与已有自动完成逻辑保留。
- **任务**：添加、编辑、完成、重新打开、删除和恢复。删除先进入账号回收站；有记录时，任务页底部显示回收站入口。
- **通知**：依据现有 `content_type=notice` 在全部任务下方提供独立清单，默认按关键日期与时刻从早到晚排列，同日仅有日期的内容随后，无日期的内容最后；已阅读状态不改变位置。沿用查询、详情、编辑和回收站；浅灰“通知”标签区分类型，列表左侧和详情底部都可勾选、取消已阅读。阅读状态复用已有任务状态并同步保存，通知入口保留全部通知，已阅读项同时进入已完成。

导入历史的名称旁显示当前实际状态：未完成、已完成、回收站、已取消或已移除；通知在名称前复用小号浅灰标签，后方统一显示完成状态。人工核对结果仍保留在原核对分类，不因确认转入新增；点击“已恢复”将第一条关联结果滚动到可视区域顶部并高亮同来源条目，滚动时保留高亮，切换分类或离开时清除；成功来源无任务产出时，点击“已恢复”定位批次标题并说明该来源没有新增任务或待核对内容，同批其他来源仍保留错误；恢复记录无法查看时明确说明已保存任务不受影响。
- **课程识别**：使用 8 个柔和色系，清单与详情以圆点、浅底和课程名呈现；无课程显示中性灰“未分类”，来源仍为辅助灰字。已有课程配色保留，新课程优先使用空余色；超过 8 门可复用色系，始终保留名称与完整提示，不仅靠颜色辨认。课程颜色是按账号隔离的浏览器视觉偏好，只保存课程名哈希和色号，不保存原文、任务或更改服务端契约。
- **查询**：全部任务、通知、临近截止、已完成的清单共用关键词、课程、来源和日期筛选。全部任务清单只包含未完成任务；临近截止包含逾期及今天起未来 7 天的未完成任务和未阅读通知；已完成混合展示已完成任务和已阅读通知。
- **日历**：仅在全部任务中提供，月、周视图不显示搜索和筛选行，也不受清单条件限制，展示全部未完成任务的截止日期及通知的关键时间；切回清单恢复原查询条件。月历按窗口剩余高度分配 4–6 行日期，优先一屏看完整月；空间较小时缩为单条摘要或数量，点击日期可查看全部内容。宽屏当天详情在右侧独立滚动，窄屏放在完整月历下方。切到临近截止、已完成或通知再返回，保留原月份、选中日期、月周模式与视图偏好。日期只有天时保留日期精度，具体时刻可留空。月、周视图可用鼠标将“无日期”任务拖入日期格设置截止日期，也可从日期格或当日面板拖动已有日期的未完成任务改期，保留原截止时刻；拖回原日期不重复保存，单击仍查看详情，不新增日期按钮；支持目标高亮、失败留在原位和撤销，鼠标覆盖被抓取的文字，通知沿用原操作。
- **账号**：账号摘要与侧栏共用头像；点击圆形头像选取不超过 5 MB 的 JPG/PNG/WebP，预览、缩放及调整位置后保存，支持取消和恢复默认；桌面只在悬停或键盘焦点时覆盖灰色“更改头像”，手机直接点击。头像随账号保存到服务端。侧栏底部可登录、注册、退出、修改昵称和密码；新注册须填写邮件验证码，已有账号可在账号面板补验证邮箱，未验证不影响原有功能。登录页的“忘记密码”通过已验证邮箱重设密码，成功后返回登录；尚不提供换绑邮箱。账号弹窗还提供教学网账号管理，可直接连接、更新或移除教学网账号，复用导入页表单。真实账号数据由服务端保存，浏览器只保留会话 Cookie 和视图偏好，不把工作区作为本地存储副本。发信须由部署者配置 [阿里云邮件服务](../server/deploy/EMAIL.md)。
- **错误反馈**：系统错误使用统一的用户提示和“反馈此问题”；点击后才发送错误分类、操作、时间和版本，成功或失败明确显示，同一错误可重试且不会重复入库。错误报告在管理员后台查看，不附带通知正文或账号密码。
- **导航**：桌面固定收放按钮切换 232px 侧栏与 72px 图标栏；任务区域依次为全部任务、通知、临近截止、已完成。折叠后仍可直接打开各入口和账号，图标悬停约 500ms 或获得键盘焦点时显示名称，手机沿用抽屉菜单。页面、任务分类、清单 / 日历、月周模式、日历范围、选中日期、当日 / 无日期面板及导入来源同步到网址，刷新和浏览器前进、后退可恢复。旧的 `?view=calendar` 与 `?demo=1` 入口继续可用，无效导航参数回退到默认值；搜索、筛选、账号凭据和未提交的表单不写入网址，账号切换仍清理私有上下文。

当前前端不提供规划、任务拆分、学习时段或专注计时入口，也不包含历史任务迁移和旧界面数据转换。客户端工作区仅有任务和回收站，保存只写任务；来源证据继续通过服务端接口读取。服务端要求 `plans`、`subtasks` 为数组，新任务提交空数组，已有字段直接透传。

待核对任务同时出现在任务清单与导入页，清单使用黄色标记，支持直接完成、通过普通编辑器修改日期等内容，以及一键忽略与撤销；完成或保存实际修改后自动转为普通任务，查看、取消或保存失败保留标记。

## 目录结构

`src/` 包含页面、组件与样式；测试覆盖当前前端功能与接口适配。

```text
frontend/
├── README.md
├── CONTRIBUTIONS.md          历史贡献与当前设计来源
├── index.html                应用、通用弹窗和任务抽屉
├── admin.html                独立管理员入口
├── favicon.svg              嵌入确认品牌图的主站与后台标签页图标
├── package.json / package-lock.json
├── playwright.config.js      隔离浏览器显示与反馈交互检查
├── vite.config.ts            开发端口与同源 API 代理
├── src/
│   ├── assets/
│   │   ├── brand-logo.webp   主站与后台共用的扫描牛品牌图标
│   │   └── mascot-peek.png   旧侧栏探头素材，保留备用
│   ├── app.js                页面组织、筛选、日历导航和任务交互
│   ├── navigation.js         网址导航状态、日期与参数校验
│   ├── page-layout.js        共用标题、导航、控件、操作与日期区域渲染器
│   ├── account-avatar.js     头像选图校验、圆形预览裁切与共用显示
│   ├── account-ui.js         可选环境账号工具及登录、验证码注册、邮箱补验证、密码找回、昵称、密码及教学网连接管理入口
│   ├── user-errors.js        共用用户报错、反馈入口和提交状态
│   ├── error-reports-view.js 管理员错误报告列表
│   ├── usage-feedback.js     账号中的主动反馈与非阻塞评分
│   ├── admin-usage.js        独立后台统计、日期筛选和反馈列表
│   ├── traffic-view.js       后台游客、注册、回访卡片及每日明细
│   ├── admin-operations.js   独立管理员运行监控、只读追踪和资源缺失适配
│   ├── admin-usage.css       独立后台的桌面与手机样式
│   ├── text-import-jobs.js   文本作业提交、状态轮询和刷新恢复
│   ├── import-ui.js          文本、教学网采集与候选核对
│   ├── import-evidence.js    摘录、完整通知、来源链接和失败身份
│   ├── materials-ui.js       文件选择、后台识别与失败重试
│   ├── source-schedule-ui.js 主按钮状态、执行频率与定时授权
│   ├── source-sync-ui.js    后台教学网同步、进度恢复与失败重试
│   ├── course-identity.js    课程身份、固定配色偏好及共用课程标记
│   ├── course-identity.css   课程签、紧凑简称与色点的 8 色样式
│   ├── completion-companion.js 完成伙伴文案、连续合并、撤销与本地偏好
│   ├── completion-companion.css 底部伙伴提示、有限动作与减少动态样式
│   ├── sidebar-companion-state.js 已保存任务的侧栏角色、情绪与真实数量文案
│   ├── sidebar-companion.js  常驻角色、悬停聚焦和轻点对话的交互
│   ├── sidebar-companion.css 侧栏角色、紧凑形态、话语与短动作样式
│   ├── assets/companions/    牛马透明动作姿势与角色完成文案
│   ├── task-model.js         日期、筛选、排序与截止分组
│   ├── task-views.js         任务行、分组清单和当日面板
│   ├── calendar.js           周一开头的月历与周截止日程
│   ├── calendar-drag.js      任务拖放分配日期与改期、日期高亮和手势区分
│   ├── calendar.css          日历、任务清单和当日面板样式
│   ├── task-drawer.js        任务详情及新增、编辑表单
│   ├── task-evidence.js      原文、证据摘录和安全来源链接
│   ├── team-store.js         任务与回收站状态、账号请求和保存队列
│   ├── demo.js               仅在内存中的合成任务
│   ├── environment-badge.js  服务环境和版本描述，不生成额外页面浮层
│   ├── icons.js              共用 SVG 图标
│   ├── id.js                 浏览器兼容的随机任务 ID
│   ├── theme.css             普通应用与独立后台共用的颜色、圆角和页面尺寸
│   └── styles.css            应用布局、导航、抽屉和表单
├── tests/
│   ├── app-date.test.js      跨日更新、草稿保留和拖放日期保存
│   ├── completion-companion.test.js 完成伙伴文案选择、剩余任务与偏好容错
│   ├── sidebar-companion-state.test.js 状态优先级、计数边界和未确认数据排除
│   ├── browser/sidebar-companion.spec.js 侧栏角色、已保存状态、对话及手机交互
│   ├── browser/completion-companion.spec.js 保存后反馈、合并撤销、弹窗和偏好交互
│   ├── navigation.test.js    导航恢复、旧链接兼容、参数校验与隐私边界
│   ├── calendar-drag.test.js 点击、拖放、取消与触屏边界
│   ├── user-errors.test.js   用户提示、点击提交、重试、去重及账号重置
│   ├── browser/error-feedback.spec.js 桌面与手机的可见提示、按钮及提交状态
│   ├── browser/email-verification.spec.js 验证码注册、补验证、重发和迟到响应交互
│   ├── browser/password-recovery.spec.js 找回密码、用途隔离、倒计时及关闭/切换竞态
│   ├── account-tools.test.js 测试专用清空工具的确认、失败状态与切换账号保护
│   ├── account-ui.test.js    密码找回、草稿保留、迟到响应和已登录状态保护
│   ├── browser/account-avatar.spec.js 头像悬停、选图裁切、保存恢复与关闭期间响应
│   ├── browser/account-calendar.spec.js 账号连接管理、月周拖动改期与撤销
│   ├── course-identity.test.js 配色稳定、账号隔离、存储降级和名称安全
│   ├── course-calendar.test.js 月周课程标记、日期聚合色点与安全名称
│   ├── browser/course-identity.spec.js 课程跨视图、刷新、筛选与窄屏显示
│   ├── task-model.test.js    筛选、截止分组、月周日历和安全渲染
│   ├── text-import-jobs.test.js 文本状态恢复、迟到读取和账号切换回归
│   ├── browser/text-import-jobs.spec.js 真实前后端、合成模型的刷新、停止与重试验收
│   ├── import-flow.test.js   通知时间、候选确认、教学网和账号隔离
│   ├── import-auto.test.js   后台导入、幂等恢复、停止与账号隔离
│   ├── import-evidence.test.js 原文、来源身份与安全渲染
│   ├── import-background.test.js 后台同步与独立导入并行保护
│   ├── materials.test.js     文件回执、稳定重试与刷新恢复
│   ├── interface-smoke.mjs   临时数据库与合成服务的浏览器验收
│   ├── source-schedule.test.js 自动同步授权、频率、冲突和账号隔离
│   ├── source-sync.test.js   后台同步、运行恢复、停止和重试
│   ├── sync-fixtures.js     后台同步契约的合成运行样例
│   ├── team-store.test.js    任务保存、账号隔离、冲突与删除恢复
│   ├── usage-feedback.test.js 访问去重、主动反馈及后台隔离
│   ├── traffic-view.test.js  流量卡片、兼容旧接口及安全展示
│   ├── admin-operations.test.js 监控日期参数、指标缺失、安全转义与入口隔离
│   ├── admin-operations-browser.mjs 合成监控的桌面手机交互、异常隔离和截图
│   ├── task-evidence.test.js 来源链接和证据展示
│   └── environment-badge.test.js 环境识别与版本差异
└── docs/
    ├── design.md             当前产品规格
    ├── maintenance.md        模块职责与验证要求
    ├── mascots.md            牛马性格、动作素材与完成反馈规则
    ├── screenshots/
    │   ├── sidebar-companion-horse.png 逾期较多时的小马与话语
    │   ├── sidebar-companion-cow.png 今日完成良好时的小牛与话语
    │   ├── sidebar-companion-mobile.png 手机菜单内的状态伙伴
    │   ├── sidebar-companion-collapsed.png 折叠栏的紧凑角色与对话
    │   ├── completion-companion-desktop.png 桌面保存后的牛马完成反馈
    │   ├── completion-companion-mobile.png 手机清单的牛马完成反馈
    │   ├── completion-companion-drawer.png 手机详情内避开操作栏的完成反馈
    │   ├── cartoon-a-task-list.png 纯白卡通 A 主题的桌面任务清单
    │   ├── cartoon-a-calendar.png A 主题的完整月历与当日面板
    │   ├── cartoon-a-import.png  A 主题的文本导入与核对区
    │   ├── cartoon-a-mobile.png  A 主题的手机任务清单
    │   ├── test-account-reset.png 测试账号面板的清空入口
    │   ├── test-account-reset-mobile.png 手机清空确认弹窗
    │   ├── 2026-10-20-admin-operations-desktop.png 合成运行监控桌面验收
    │   ├── 2026-10-20-admin-operations-mobile.png 合成运行监控手机验收
    │   ├── compact-header-desktop.jpg 统一页头与日期左侧添加入口
    │   ├── compact-header-mobile.jpg 手机导航、操作与标题分行
    │   ├── calendar-full-month.jpg 完整月历与当天详情
    │   ├── calendar-date-drop.jpg 无日期任务拖入日期后可撤销
    │   ├── login-drag-retained.jpg 框内拖出后保留登录输入
    │   ├── email-login-recovery.png 登录弹窗左下角的忘记密码入口
    │   ├── email-password-recovery.png 已验证邮箱的密码找回空表单
    │   ├── task-list-desktop.jpg
    │   ├── calendar-desktop.jpg
    │   ├── calendar-without-legend.png 移除类型图例与通知总数后的月历
    │   ├── calendar-mobile.jpg
    │   ├── sidebar-collapsed.jpg
    │   ├── import-desktop.jpg
    │   ├── import-evidence-desktop.jpg
    │   ├── import-history-result-statuses.png 历史当前状态与通知类型标记
    │   ├── import-recovery-highlight.png 恢复结果顶部定位与关联条目高亮
    │   ├── import-recovery-empty-source.png 无任务来源在部分成功批次内的恢复定位
    │   ├── background-sync-desktop.jpg
    │   ├── teaching-history-results.jpg 有效导入历史与无变化检查
    │   ├── teaching-sync-progress-desktop.png 主按钮停止与紧凑导入进度
    │   ├── teaching-sync-stop-mobile.png 手机定时同步停止入口
    │   ├── notice-proposal-desktop.jpg
    │   ├── notice-proposal-calendar.jpg
    │   ├── notice-proposal-mobile.jpg
    │   ├── notice-read-desktop.png  列表与详情同步勾选已阅读
    │   ├── notice-read-mobile.png   手机通知阅读详情
    │   ├── notice-near-desktop.png  近期任务与通知混合展示
    │   ├── notice-done-desktop.png  已完成任务与已阅读通知混合展示
    │   ├── 2026-10-14-admin-usage-desktop.png 后台桌面布局
    │   ├── 2026-10-14-admin-usage-mobile.png 后台手机布局
    │   ├── 2026-10-19-traffic-desktop.png 游客、注册与回访桌面布局（合成数据）
    │   ├── 2026-10-19-traffic-mobile.png 游客、注册与回访手机布局（合成数据）
    │   ├── 2026-10-18-test-diagnostics-desktop.png 测试诊断组件合成桌面截图
    │   └── 2026-10-18-test-diagnostics-mobile.png 测试诊断组件合成手机截图
    └── archive/              历史文档与截图，不代表当前功能
```

测试环境错误附带受限诊断时，可展开“测试环境诊断详情”查看完整 JSON，窄屏换行并滚动；反馈按钮不上传诊断内容，生产错误仍使用原有简洁提示。

## 导入入口

- 教学网：执行频率、统一启动与停止按钮、紧贴操作区按需显示的进度，以及折叠的历史导入；提交状态手动目录已移除。
- 图片与文件：选文件、同意上传、识别任务；失败记录可重试，成功记录在“最近导入”中查看。
- 编辑任务或通知时可选择内容类型，保存沿用原有任务版本与账号隔离；未阅读通知可在月历、周历及日期面板拖动关键日期，保留时刻并支持撤销。
- 通知先未阅读、后已阅读，同组按关键日期与时刻排序；未阅读的过去日期与任务共用红色。
- 整轮连接失败在后续完整采集成功后标为已恢复；点击后展开、高亮全部关联批次与分类，并保留完整批次标题。

浏览器合成验收脚本为 `node tests/interface-smoke.mjs`，需要前端与爬虫依赖、Playwright 浏览器和 `server/requirements-test.txt`；可通过 `NIUMA_TEST_PYTHON` 指定 Python。采集与模型均为合成替身，使用新建临时数据库，不访问私人账号或真实模型服务。

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

教学网同步使用 `/api/sources/sync-runs` 创建后台运行，前端只提交来源选择、操作身份和明确同意；后端负责采集、识别及任务保存，关闭网页不停止运行。旧保存通知的直接重试仅使用服务端 `request_id`，不会回传来源正文。

数据库、凭据、密钥和本地配置不提交到仓库。

## 使用反馈与后台

- 登录后在底部账号入口主动填写使用反馈；连续次日使用时出现一次可关闭的评分窗，1～5 分同排、从红经黄过渡到绿，可反复改分并保存最后一次选择；原窗口可选填建议或跳过，跳过或提交后统一显示“谢谢您的反馈，我们会继续努力！”，在 1.5 秒内带动画收起。
- 成功恢复会话、登录完成、前台恢复和键盘交互按账号和北京时间日期补记访问，失败不影响任务读取；合成预览不记录访问。
- 管理员通过 `/admin.html` 独立登录查看使用统计；普通页面不加载后台图表脚本，服务端逐请求校验权限。
- 后台只展示启用统计后的记录及聚合数据，空数据不生成模拟趋势；通知阅读不计入任务完成指标。
- 后台提供游客数、新增注册、回访次数及每日明细；游客按匿名 Cookie 标识去重，回访按跨北京时间自然日再访问统计且同一标识每天最多一次，注册只计成功事件，另显示现有账号总数。

## 合成预览

打开 [合成预览](http://localhost:1331/?demo=1)，或 [合成日历](http://localhost:1331/?demo=1&view=calendar)。`demo=1` 使用相对当天生成的合成任务；任务操作只改变页面内存，刷新后恢复初始数据，不写入真实账号或持久化任务。

合成文本与任务不会发送到识别或教学网采集服务；导入操作提示退出预览并登录。页面可能请求公开的 `/api/version` 环境信息。视图、月周模式和侧栏折叠偏好可以保留在浏览器中，合成任务本身不会保留。

## 环境隔离

上述 1331 / 1332 命令是兼容的本地启动方式。共享测试和部署使用 [统一环境入口](../server/deploy/ENVIRONMENTS.md) 与 [团队使用指南](../server/deploy/TEAM_GUIDE.md)：development 默认 1431 / 1432，test 默认 1531 / 1532，production 使用独立配置与数据卷。

各环境的数据库、会话、凭据、附件和模型配置独立；不要拿生产数据初始化开发或测试。Vite 通过 `NIUMA_WEB_PORT`、`NIUMA_PORT` 接收相应端口；同一网页构建通过 `/api/version` 识别当前服务环境，账号入口的环境信息用于核对前后端版本。部署与发布要求以 [部署指南](../server/deploy/README.md) 为准。

邮件配置未就绪时，development/test 可按 [空白测试账号操作](../server/deploy/ENVIRONMENTS.md#空白测试账号) 创建普通账号，再从现有登录窗登录。账号不预置任务或其他数据；本工具没有前端专用入口，也不能替代真实邮件流程验收。

## 验证

运行前端模块测试与构建：

```bash
npm test
npm run build
```

`npm test` 仅运行前端测试，不安装后端依赖或运行后端测试。服务端与采集器的验证由各自模块负责。界面变更还应检查桌面与 320–390px 手机宽度，验证任务操作、筛选、日历跳转、导入核对和账号切换；演示截图只使用合成材料。当前截图与视觉规格见 [设计说明](docs/design.md)。

`playwright.config.js` 配置隔离浏览器回归。安装浏览器组件后运行 `npm run test:browser`，使用全合成 API 检查 1440px、390px 和 320px 下的报错、反馈按钮、提交成功 / 失败与弹窗操作。CI 的 Linux 前端作业执行这些检查并上传截图及失败跟踪。

历史贡献与来源见 [CONTRIBUTIONS.md](CONTRIBUTIONS.md)。

文本提交使用后台作业，显示等待识别、正在识别和最终状态；刷新或重新打开文本栏目会恢复记录，未识别出任务也保留在历史导入中。失败、停止或服务中断可在原记录重试；未提交的文本草稿仍不保存。接口见 [文本后台作业](../contracts/docs/text-import-jobs-v0.1.md)。

文本导入的合成验证截图：[桌面识别中](docs/archive/2026-10-22-text-import-running-desktop.png)、[手机识别中](docs/archive/2026-10-22-text-import-running-mobile.png)、[识别完成与候选](docs/archive/2026-10-22-text-import-completed-desktop.png)；截图使用合成账号和模型结果，不代表真实模型准确率或服务器验收。

本地/测试的「清空测试数据」入口由 API 提供独立脚本，网页构建不打包清空文案与逻辑，正式 API 不提供脚本地址；使用方式见 [环境说明](../server/deploy/ENVIRONMENTS.md#清空测试数据)。`tests/account-tools.test.js` 验证确认、未保存处理、重复点击和切换账号保护。

清空入口与手机确认界面的合成账号截图：`docs/screenshots/test-account-reset.png`、`docs/screenshots/test-account-reset-mobile.png`。

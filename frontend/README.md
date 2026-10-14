# 牛马管理器前端

当前网页围绕两件事组织：导入通知，以及查询和处理任务。全部任务标题右侧的双段开关切换清单与日历，不再单设标签栏；两视图共用固定宽度页面容器，标题和查询区保持原位，清单正文限宽 860px，日历使用较宽区域。临近截止与已完成固定使用清单；账号入口固定在侧栏底部。当前界面规格见 [设计说明](docs/design.md)，模块边界见 [维护指南](docs/maintenance.md)。

## 功能与数据

- **导入**：粘贴文本，或连接教学网采集作业与通知。结果进入待核对列表，核对原文、日期和目标任务后再确认新增或变更；支持忽略、撤回忽略及失败重试。邮箱显示禁用入口，尚未接入。
- **任务**：添加、编辑、完成、重新打开、删除和恢复。删除先进入账号回收站；有记录时，任务页底部显示回收站入口。
- **查询**：全部任务、临近截止、已完成三个范围，共用关键词、课程、来源和截止日期筛选。临近截止包含逾期任务以及今天起未来 7 天到期的未完成任务。
- **日历**：仅在全部任务中提供，月、周视图都按截止日期展示未完成任务；选择日期查看当天清单，无日期任务另有入口。切到临近截止或已完成再返回，保留原月份、选中日期、月周模式、筛选与视图偏好。日期只有天时保留日期精度，具体时刻可留空。
- **账号**：侧栏底部可登录、注册、退出、修改昵称和密码。真实账号数据由服务端保存，浏览器只保留会话 Cookie 和视图偏好，不把工作区作为本地存储副本。

当前前端不提供规划、任务拆分、学习时段或专注计时入口。保存适配层保留已有任务的来源、证据、`plans`、`subtasks` 等元数据；精简界面不删除账号中已有的这些记录。

## 目录结构

`src/` 当前包含 15 个文件；测试按当前产品和服务端适配职责保留。

```text
frontend/
├── README.md
├── CONTRIBUTIONS.md          历史贡献与当前设计来源
├── index.html                应用、通用弹窗和任务抽屉
├── favicon.svg
├── package.json / package-lock.json
├── vite.config.ts            开发端口与同源 API 代理
├── src/
│   ├── app.js                页面组织、筛选、日历导航和任务交互
│   ├── account-ui.js         登录、注册、昵称和密码表单
│   ├── import-ui.js          文本、教学网采集与候选核对
│   ├── task-model.js         日期、筛选、排序与截止分组
│   ├── task-views.js         任务行、分组清单和当日面板
│   ├── calendar.js           周一开头的月历与周截止日程
│   ├── calendar.css          日历、任务清单和当日面板样式
│   ├── task-drawer.js        任务详情及新增、编辑表单
│   ├── task-evidence.js      原文、证据摘录和安全来源链接
│   ├── team-store.js         服务端账号工作区与元数据适配
│   ├── demo.js               仅在内存中的合成任务
│   ├── environment-badge.js  服务环境和前后端版本描述
│   ├── icons.js              共用 SVG 图标
│   ├── id.js                 浏览器兼容的随机任务 ID
│   └── styles.css            应用布局、导航、抽屉和表单
├── tests/
│   ├── task-model.test.js    筛选、截止分组、月周日历和安全渲染
│   ├── import-flow.test.js   通知时间、候选确认、教学网和账号隔离
│   ├── team-store.test.js    保存适配与已有元数据保留
│   ├── task-evidence.test.js 来源链接和证据展示
│   └── environment-badge.test.js 环境识别与版本差异
└── docs/
    ├── design.md             当前产品规格
    ├── maintenance.md        模块职责与验证要求
    ├── screenshots/
    │   ├── task-list-desktop.jpg
    │   ├── calendar-desktop.jpg
    │   ├── calendar-mobile.jpg
    │   └── import-desktop.jpg
    └── archive/              历史文档与截图，不代表当前功能
```

## 本地启动

需要 Node.js 20.19+ 和 Python 3.9+；完整测试需要 Python 3.10+。从 `frontend/` 目录执行：

```bash
npm ci
node ../scripts/run-python.mjs -m pip install -r ../server/requirements.txt
pnpm --dir ../crawler install --frozen-lockfile
npm run dev:api
```

另开终端，在同一目录执行：

```bash
npm run dev
```

打开 [本地网页](http://localhost:1331/)。Vite 默认端口为 1331，同源 `/api` 代理到本机 1332。端口占用时先确认已有进程，不另起一个容易混淆的网页。不要在仓库根目录运行 npm。

文本识别与教学网识别由业务后端调用模型；普通本地启动读取仓库根目录 `.env`，教学网要求有效的七牛配置。显式设置 `NIUMA_ENVIRONMENT` 时，需要为该环境配置模型文件，或用 `NIUMA_MODEL_ENV` 指向已有配置文件；配置变更后重启 API。密钥不进入前端或 `VITE_*`。

教学网采集依赖单独安装在 `crawler/`；已有 Chrome 或 Edge 时直接使用，否则从 `frontend/` 执行 `pnpm --dir ../crawler exec playwright install chromium`。账号连接由用户在导入页管理；缺依赖、缺模型配置与连接失败会显示各自原因，单纯刷新不能代替安装或配置。

教学网同步返回服务端保存的来源展示对象，前端仅使用其 `request_id` 发起 `teaching-run/0.1` 识别或重试；不会把浏览器中的来源正文当作可信采集请求回传。

`npm run db:seed` 仅供独立的本地测试数据库初始化合成账号；不用于生产，也不是查看设计的前提。数据库、凭据、密钥和本地配置不提交到仓库。

## 合成预览

打开 [合成预览](http://localhost:1331/?demo=1)，或 [合成日历](http://localhost:1331/?demo=1&view=calendar)。`demo=1` 使用相对当天生成的合成任务；任务操作只改变页面内存，刷新后恢复初始数据，不写入真实账号或持久化任务。

合成文本与任务不会发送到识别或教学网采集服务；导入操作提示退出预览并登录。页面可能请求公开的 `/api/version` 环境信息。视图、月周模式和侧栏折叠偏好可以保留在浏览器中，合成任务本身不会保留。

## 环境隔离

上述 1331 / 1332 命令是兼容的本地启动方式。共享测试和部署使用 [统一环境入口](../server/deploy/ENVIRONMENTS.md) 与 [团队使用指南](../server/deploy/TEAM_GUIDE.md)：development 默认 1431 / 1432，test 默认 1531 / 1532，production 使用独立配置与数据卷。

各环境的数据库、会话、凭据、附件和模型配置独立；不要拿生产数据初始化开发或测试。Vite 通过 `NIUMA_WEB_PORT`、`NIUMA_PORT` 接收相应端口；同一网页构建通过 `/api/version` 识别当前服务环境，账号入口的环境信息用于核对前后端版本。部署与发布要求以 [部署指南](../server/deploy/README.md) 为准。

## 验证

仅运行前端模块测试：

```bash
node --test tests/*.test.js
npm run build
```

完整回归：

```bash
npm test
```

`npm test` 会安装服务端测试依赖，并运行前端与服务端测试。界面变更还应检查桌面与 320–390px 手机宽度，验证任务操作、筛选、日历跳转、导入核对和账号切换；演示截图只使用合成材料。当前截图与视觉规格见 [设计说明](docs/design.md)。

历史贡献与来源见 [CONTRIBUTIONS.md](CONTRIBUTIONS.md)。

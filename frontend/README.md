# 牛马管理器前端

## 目录结构

```text
frontend/
├── README.md                  提供目录、启动、功能和验证入口。
├── CONTRIBUTIONS.md           记录当前界面来源及维护范围。
├── .gitignore                 排除依赖、构建结果和本地运行数据。
├── .nvmrc                     固定推荐的 Node.js 主版本。
├── index.html                 加载应用脚本与样式。
├── favicon.svg                提供浏览器页签图标。
├── package.json               定义开发、构建和测试命令。
├── package-lock.json          锁定前端依赖版本。
├── vite.config.ts             配置开发端口与 API 代理。
├── src/
│   ├── app.js                 组织页面、路由与主要交互。
│   ├── store.js               导出统一的状态访问接口。
│   ├── team-store.js          对接账号数据库和业务 API。
│   ├── team-recognition-ui.js 展示识别、证据与人工确认流程。
│   ├── team-planner-ui.js     展示规划输入、预览与确认流程。
│   ├── team-calendar-ui.js    渲染日、周、月日历与 DDL。
│   ├── team-personalization-ui.js 记录实际用时反馈。
│   ├── styles.css / polish.css / team-integration.css 维护界面样式。
│   └── 其他工具模块           提供示例、搜索、计时、语音、主题和图标。
├── tests/                     覆盖前端行为与团队接口适配。
└── docs/
    ├── maintenance.md         说明模块边界和验证要求。
    └── archive/               保存带日期的历史文档。
```

本目录以 **潘勇圳（Yongzhen23）** 的 [niuma-frontend](https://github.com/Yongzhen23/niuma-frontend) 为主界面，参考提交 `18d5421`；当前版本接入小组仓库的账号数据库、文本识别和模型规划能力。维护前请阅读 [维护指南](docs/maintenance.md)、[贡献说明](CONTRIBUTIONS.md) 和 [小组协作规范](../CONTRIBUTING.md)。业务后端位于 `server/`。

## 启动

需要 Node.js 20.19+、Python 3.9+。从 `frontend/` 目录执行：

```bash
npm ci
npm run db:seed
npm run dev:api
```

另开终端仍在 `frontend/` 目录运行 `npm run dev`，访问 http://localhost:1331 。前端 /api 代理到本机 1332。已有进程占用 1331 时，请使用当前运行中的页面，或先停止旧的 Vite 进程。不要在仓库根目录运行 npm。

上述 npm 命令共用跨平台 Python 启动器，Windows 会依次尝试 python、python3、py；macOS/Linux 尝试 python3、python。也可通过 PYTHON 环境变量指定解释器路径。

开发测试账号：`demo@niuma.test` / `Niuma-Test-2026!`。账号和模拟数据由 seed 在协作者各自本地的 SQLite 中生成；不默认登录，不提交数据库文件。seed 幂等，已有账号内容不会被覆盖。数据库默认在 `../server/data/niuma.sqlite3`，可用 `NIUMA_DB` 指定路径。

## 功能与数据

- 任务、子任务、课程、资料、计划和专注状态通过账号数据库保存；浏览器只保留 HttpOnly 会话 Cookie。深浅主题只在当前页面会话生效。
- DDL 支持无截止日期或精确到分钟。日历有日、周、月视图，日/周默认滚动到 10:00，可向上滚回 00:00；缺少具体时刻的旧 DDL 会提示补全。设置新 DDL 时必须填写时刻。
- 快速收集与语音输入进入“识别审核”，由文本 Harness 输出 v0.2 候选；用户核对证据和目标任务后才应用。原文只有截止日期时可先加入任务，日历提示“时刻待补充”，不虚构 23:59；后续可在任务详情补全时间。没有服务时明确显示未接入，可加载标记为人工演示的候选。
- “开始规划”只处理尚未完成、没有计划且未标记为“无需规划”的任务。可用时间、已有计划、真实 DDL 和个性化用时交给模型 Harness；先预览再确认。没有模型服务时不会用本地规则伪造安排。
- 设置页可随机选择最多三项已完成任务填写实际分钟数。参考版的课程资料、键盘搜索、专注计时、撤销、示例体验、JSON 与 ICS 导出继续可用。
- 文本识别和规划的接口以 [团队对齐文档](../docs/backend-harness-alignment.md) 为准。前端独有的视图结构由 `src/team-store.js` 映射到服务端工作区，保留来源证据、取消历史和已有计划。

真正的服务由后端配置，前端不接触密钥：

```bash
TEXT_HARNESS_URL=http://127.0.0.1:1333/recognize HARNESS_URL=http://127.0.0.1:1334/planning npm run dev:api
```

## 检查

```bash
npm test
npm run build
```

开发 API 仅用于本地联调。部署前需生产服务、HTTPS/Secure Cookie、限流、备份和迁移。客户端 JSON 导出是手动备份，不提供恢复导入或外部日历双向同步。

# 牛马管理器前端

本目录以 **潘勇圳（Yongzhen23）** 的 [niuma-frontend](https://github.com/Yongzhen23/niuma-frontend) 为主界面，参考提交 `18d5421`。主页、侧边栏、任务、课程、主题、快捷搜索、专注和移动端布局保留其 HTML/CSS/JavaScript 实现；接入本项目的 SQLite 账号数据库、文本识别 v0.2 和模型规划 2.0。具体合并边界见 [贡献说明](CONTRIBUTIONS.md)。

## 启动

需要 Node.js 20.19+、Python 3.9+。从 `frontend/` 目录执行：

```bash
npm ci
npm run db:seed
npm run dev:api
```

另开终端仍在 `frontend/` 目录运行 `npm run dev`，访问 http://localhost:1331 。前端 /api 代理到本机 1332。已有进程占用 1331 时，请使用当前运行中的页面，或先停止旧的 Vite 进程。不要在仓库根目录运行 npm。

开发测试账号：`demo@niuma.test` / `Niuma-Test-2026!`。账号和模拟数据由 seed 在协作者各自本地的 SQLite 中生成；不默认登录，不提交数据库文件。seed 幂等，已有账号内容不会被覆盖。数据库默认在 `../server/data/niuma.sqlite3`，可用 `NIUMA_DB` 指定路径。

## 功能与数据

- 任务、子任务、课程、资料、计划和专注状态通过账号数据库保存；浏览器只保留 HttpOnly 会话 Cookie。深浅主题只在当前页面会话生效。
- DDL 支持无截止日期或精确到分钟。日历有日、周、月视图，日/周默认滚动到 10:00，可向上滚回 00:00；缺少具体时刻的旧 DDL 会提示补全。设置新 DDL 时必须填写时刻。
- 快速收集与语音输入进入“识别审核”，由文本 Harness 输出 v0.2 候选；用户核对证据和目标任务后才应用。没有服务时明确显示未接入，可加载标记为人工演示的候选。
- “开始规划”只处理尚未完成、没有计划且未标记为“无需规划”的任务。可用时间、已有计划、真实 DDL 和个性化用时交给模型 Harness；先预览再确认。没有模型服务时不会用本地规则伪造安排。
- 设置页可随机选择最多三项已完成任务填写实际分钟数。参考版的课程资料、键盘搜索、专注计时、撤销、示例体验、JSON 与 ICS 导出继续可用。
- 文本识别和规划的接口以 [团队对齐文档](../docs/backend-harness-alignment.md) 为准。前端独有的视图结构由 `src/team-store.js` 映射到服务端工作区，保留来源证据、取消历史和已有计划。

真正的服务由后端配置，前端不接触密钥：

```bash
TEXT_HARNESS_URL=http://127.0.0.1:8001/recognition HARNESS_URL=http://127.0.0.1:8000/planning npm run dev:api
```

## 检查

```bash
npm test
npm run build
```

开发 API 仅用于本地联调。部署前需生产服务、HTTPS/Secure Cookie、限流、备份和迁移。客户端 JSON 导出是手动备份，不提供恢复导入或外部日历双向同步。

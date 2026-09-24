# 牛马管理器前端

React + TypeScript 前端，Python 3.9+ 本地 API，SQLite 数据库。区分真实 DDL 与执行计划。

## 目录与协作者贡献

本目录独立管理前端源码 `src/`、测试 `tests/`、Node 依赖、Vite/TypeScript 配置及构建产物。仓库根 README 面向整个团队；共享协议在 `../docs/`，本地 API 在 `../server/`。不要在根目录运行 npm 命令。

参考并适配了 **潘勇圳（Yongzhen23）** 的前端贡献，详细来源、实际采纳和未合入内容见 [贡献与整合说明](CONTRIBUTIONS.md)。新增任务筛选排序、⌘/Ctrl K 快捷搜索与 N 新建、数据库专注计时、课程资料笔记、JSON 与 ICS 导出。导出位于设置页，专注入口在页面右下角；课程、计时均保存到账号数据库。

## 本地启动

界面采用潘勇圳参考版的浅灰/蓝紫风格，任务卡片参考其进度条，按实际完成子任务数显示比例。任务详情直接展开真实截止时间编辑；设置 DDL 时必须填写日期与具体时刻，也可清空日期表示无 DDL。日/周日历按分钟定位截止标记，月历同时显示时刻；默认滚动到 10:00，仍可向上滚回 00:00。旧记录缺少时刻时单独提示补充，不虚构为 23:59。原始识别协议和数据库仍保留日期精度，以忠实保存来源；前端确认时要求补全。

需要 Node.js 20.19+、Python 3.9+ 和可用的 IANA 时区数据库（Windows 缺少时运行 `python -m pip install tzdata`）。npm 脚本会寻找本机 Python，也可用环境变量 `PYTHON` 指定解释器路径。

```bash
cd frontend
npm ci
npm run db:seed
npm run dev:api
```

另开终端进入 `frontend/` 后运行 `npm run dev`，访问 http://localhost:1331。前端将 /api 代理至本机 1332。
测试账号：`demo@niuma.test`，密码：`Niuma-Test-2026!`。包含 6 个模拟任务（其中 3 个已完成）、一个固定安排、3 条模拟用时反馈和默认可用时段；不会默认登录。重复执行 seed 保留已有数据。

协作者拉取代码后，执行上述命令即可在自己的数据库中生成相同的测试账号和演示内容，任务日期会相对于初始化当天生成。模拟数据定义随 `../server/app.py` 一起提交，不需要共享数据库文件。这是公开的开发测试密码，请勿用于真实账号或公网生产环境。

数据库默认位于仓库根目录下的 `server/data/niuma.sqlite3`，已忽略，不提交 Git。账号密码仅存随机盐与 PBKDF2 哈希；业务数据不再写入 localStorage/sessionStorage。浏览器只保留 HttpOnly 会话 Cookie。旧版浏览器数据不自动导入或删除；如不需要，可自行清除旧网站数据。

## 规划与个性化

先创建/审核主任务和 DDL，再点击“开始规划”。不设置规划开始/结束日期、不手填预计工时。默认纳入未完成、没有执行计划且未标记“无需规划”的任务；折叠列表可保存排除选择。已有安排保持不变。

设置页“个性化”随机抽取最多三个已完成任务，记录真实分钟数，下次规划发送给模型作为估时参考。这是上下文反馈，不是模型微调。

真实规划需要后端团队提供 Harness HTTP 服务：
```bash
HARNESS_URL=http://127.0.0.1:8000/planning HARNESS_TOKEN=your-token npm run dev:api
```
模型负责工时评估和安排，接口见 [协作对齐文档](../docs/backend-harness-alignment.md)。未配置时明确提示未接入，**不会冒充模型生成演示结果**。手动创建页的日期线索提取仍为本地规则；真实来源采集尚未接入。文本识别页使用下述独立服务或明确标记的协议演示。

## 验证

文本识别 v0.2 已定稿并与前端审核、候选事务确认 API 对齐，统一见 [前端 / 后端 / Harness 协作协议](../docs/backend-harness-alignment.md)。

登录后在“待确认”页粘贴原文识别，或点击“加载协议演示”体验明确标注的人工样例。真实识别需设置服务端 TEXT_HARNESS_URL 和可选 TEXT_HARNESS_TOKEN，独立于规划 HARNESS_URL。无真实服务时返回未接入提示，不用样例冒充识别结果。原文要求单独保存，延期/更新/取消必须匹配目标后确认。

Python 时区校验依赖系统 IANA 数据；若 Windows 等环境缺少时区库，可运行 python -m pip install tzdata。没有 python3 命令时使用 py -3 ../server/app.py。

```bash
npm test
npm run build
```

上述 npm 命令均在 `frontend/` 执行，前端产物在 `frontend/dist/`。当前 Python HTTP 服务仅绑定回环地址，用于本地开发，不可直接用于公网生产部署；上线需替换生产应用服务器、HTTPS/Secure Cookie、登录限流、备份、迁移和完整输入校验。通知、外观等旧占位设置尚未接入系统能力。

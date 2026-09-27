# 前端整合与贡献

> 归档日期：2026-09-27。本文记录首次整合背景；当前维护范围以 `frontend/CONTRIBUTIONS.md` 为准。

最终前端以 **潘勇圳（Yongzhen23）** 的 [niuma-frontend](https://github.com/Yongzhen23/niuma-frontend) 为主，来源提交 `18d5421eeb75cc8f29a63cf95000ab21932ee7d3`。感谢潘勇圳完成了页面设计、响应式布局和大量日常使用交互。

直接采用并继续维护的主要部分：

- `index.html`、`favicon.svg`、`src/styles.css`、`src/polish.css` 的页面结构和视觉系统。
- `src/app.js` 的首页、任务列表、课程资料、示例体验、快捷操作与交互框架；`src/icons.js`、`src/examples.js`、`src/productivity.js` 的相应实现。
- `src/experience.js` 的快捷搜索和专注计时、`src/voice.js` 的浏览器语音输入、`src/theme.js` 的主题切换，以及相关测试。

在这套界面里补入本项目原有且参考版没有的协作能力：

- `src/team-store.js`：把参考版页面状态映射到现有账号数据库的 tasks、courses、materials、focus 等工作区；保留识别历史、原文要求、取消任务和已有计划。
- `src/team-recognition-ui.js`：文本识别 v0.2 原文、证据、候选确认与事务变更。
- `src/team-planner-ui.js`：用户主动启动模型估时排程、任务排除、可用时间和预览确认。
- `src/team-personalization-ui.js`：真实用时反馈。
- `src/team-calendar-ui.js`：按分钟定位 DDL 的日/周/月视图，并提示补全旧记录的时刻。
- `src/team-integration.css`：为新增页面延用参考版视觉语言。

参考版的浏览器访客业务存储、单体 `/api/state`、本地固定工时排程和默认一小时估时与团队当前持久化及 Harness 协议冲突，已替换为现有服务端流程。原有 React 页面不再作为第二套前端保留。课程项目的共同协议和后端由本项目成员继续维护。

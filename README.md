# 牛马管理器

“牛马管理器”是 DDL压鼠人小组开发的、面向学生的 AI 任务管理与学习规划助手。项目希望从课程通知、邮件或聊天文本中识别任务和截止时间（DDL），经用户确认后生成任务，并逐步提供排程、提醒和进度管理能力。

> 当前阶段：文本识别已完成固定回归、七牛真实接入及网页审核入库验证；爬虫已合并但尚未接入识别链路；仓库结构整理中。

## 模块

各模块的启动方式、依赖和验证命令见各自的入口文档。

| 模块 | 目录 | 负责内容 | 入口文档 |
| --- | --- | --- | --- |
| 爬虫 | 根目录 `src/`（整理后移至 `crawler/`） | 学院讲座、教学网作业采集 | [爬虫说明](docs/crawler.md) |
| 文本识别 | `ddl_text/` | 从文本中识别任务与 DDL，输出候选 | [文本模块交付使用说明](docs/text-handoff.md) |
| 前端 | `frontend/` | 页面、候选确认、任务与规划界面 | [前端说明与启动指南](frontend/README.md) |
| 后端 | `server/` | 账号、SQLite 存储、候选确认与规划 API | [前端 / 后端 / Harness 协作协议](docs/backend-harness-alignment.md) |

- 前端命令在 `frontend/` 中使用 npm；爬虫命令目前在仓库根目录使用 pnpm。
- 模块之间的数据格式以协作协议和 `docs/examples/` 中的样例为准（整理后移至 `contracts/`）。

## 项目文档

- [产品定义与需求说明](docs/product-requirements.md)：项目背景、目标用户、产品边界和全局质量要求。
- [Product Backlog](docs/product-backlog.md)：高层级功能需求及其当前状态。
- [项目协作规范](docs/scrum-workflow.md)：Scrum 角色、工件、活动、任务状态和完成条件。
- [参与开发](CONTRIBUTING.md)：目录归属、分支、Commit、PR 与 Review 的规则和操作步骤。
- [协作手册](docs/collaboration-handbook.md)：目录归属、前后端对接与 PR 审查的白话说明。

## 当前工作方式

小组使用 GitHub Issues 记录细化后的需求和任务，使用 GitHub Project 跟踪 Sprint、状态、负责人和工作量，使用 Pull Request 审查代码与文档变更。每个目录有固定负责人，成员只修改自己负责的目录，详见[参与开发](CONTRIBUTING.md)。

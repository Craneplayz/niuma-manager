# 牛马管理器

“牛马管理器”是 DDL压鼠人小组开发的、面向学生的 AI 任务管理与学习规划助手：用户只用一个对话框，系统识别任务、判断优先级、排进日历；教学网与微信的通知自动识别后直接进日历。

> 当前阶段（2026-09-28）：文本识别、教学网同步、微信导入已接通；正在按 F1–F3 改为「对话框 + 日历」（见 [Product Backlog](docs/product-backlog.md)）。

## 模块

各模块的启动方式、依赖和验证命令见各自的入口文档。

| 模块 | 目录 | 负责内容 | 入口文档 |
| --- | --- | --- | --- |
| 爬虫 | `crawler/` | 学院讲座、教学网作业采集 | [爬虫说明](crawler/README.md) |
| 微信 | `wechat/` | 微信群 / 聊天数据采集，转成 v0.2 交给识别 | [微信说明](wechat/README.md) |
| 文本识别 | `ddl_text/` | 从文本中识别任务与 DDL，输出候选 | [文本识别说明](ddl_text/README.md) |
| 数据契约 | `contracts/` | 模块之间的数据格式与样例 | [契约说明](contracts/README.md) |
| 任务规划 | `task_planning/`（建立中，#65） | 优先级判断规则与排程 | — |
| 前端 | `frontend/` | 对话框 + 日历界面 | [前端说明与启动指南](frontend/README.md) |
| 后端 | `server/` | 账号、SQLite 存储、候选确认与规划 API | [后端说明](server/README.md) |

- 前端命令在 `frontend/` 中使用 npm；爬虫命令在 `crawler/` 中使用 pnpm。
- 模块之间的数据格式以 `contracts/` 为准，接口说明见[前端 / 后端 / Harness 协作协议](docs/backend-harness-alignment.md)。

## 项目文档

- [产品定义与需求说明](docs/product-requirements.md)：项目背景、目标用户、产品边界和全局质量要求。
- [Product Backlog](docs/product-backlog.md)：F1–F3 三项功能及对应 Issue。
- [参与开发](CONTRIBUTING.md)：目录归属、分支、Commit、PR 与 Review 的规则和操作步骤。
- [协作手册](docs/collaboration-handbook.md)：目录归属、前后端对接与 PR 审查的白话说明。

## 当前工作方式

小组使用 GitHub Issues 记录任务（任何人都可以开，主要由 ken0857900 发布），使用 Pull Request 审查代码与文档变更；暂不采用 Scrum。每个目录有固定负责人，成员只修改自己负责的目录，详见[参与开发](CONTRIBUTING.md)。

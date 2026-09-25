# 牛马管理器

“牛马管理器”是 DDL压鼠人小组开发的、面向学生的 AI 任务管理与学习规划助手。项目希望从课程通知、邮件或聊天文本中识别任务和截止时间（DDL），经用户确认后生成任务，并逐步提供排程、提醒和进度管理能力。

> 当前阶段：文本识别已完成固定回归、七牛真实接入及网页审核入库验证；阶段 E 收尾材料待团队评审合并。

## 项目文档

- [产品定义与需求说明](docs/product-requirements.md)：项目背景、目标用户、产品边界和全局质量要求。
- [Product Backlog](docs/product-backlog.md)：高层级功能需求及其当前状态。
- [项目协作规范](docs/scrum-workflow.md)：Scrum 角色、工件、活动、任务状态和完成条件。
- [参与开发](CONTRIBUTING.md)：领取 Issue、创建分支、提交 Commit、发起 PR 和完成 Review 的操作步骤。
- [协作手册](docs/collaboration-handbook.md)：目录归属、前后端对接与 PR 审查的白话说明。

## 当前工作方式

小组使用 GitHub Issues 记录细化后的需求和任务，使用 GitHub Project 跟踪 Sprint、状态、负责人和工作量，使用 Pull Request 审查代码与文档变更。

产品范围和待实现功能分别以产品定义与需求说明、Product Backlog 为准；团队管理规则和开发操作分别以项目协作规范、参与开发文档为准。

## 文本任务识别模块

接手与演示先看 [文本模块交付使用说明](docs/text-handoff.md)；本轮完成范围、测试证据和剩余限制见 [阶段 E 验收记录](docs/text-stage-e-review.md)。

阶段 A 提供约定与合成样例，阶段 B 提供可运行的离线处理骨架。离线回放不代表识别效果。

阶段 C 增加共享识别 skill，以及 Agent 草稿的日期换算、来源证据核对和候选生成。阶段 D 的真实 API 和 v0.2 HTTP 接口已随 PR #14 合并；独立召回率统计暂缓。课程七牛 API 支持直接调用 DeepSeek V4.1 Flash，无需 Codex；2026-09-25 已通过真实合成样例和前端后端联调。填写密钥和验证方式见 [API 配置说明](docs/text-api-setup.md)。

- [需求基线与职责边界](docs/text-extraction-requirements.md)
- [第一轮冲刺任务与验收标准](docs/sprint-1-text-processing.md)
- [分阶段开发、检查与提交计划](docs/text-development-plan.md)
- [输入输出约定 v0.1](docs/text-contract-v0.1.md)
- [上游样例观察与映射](docs/text-source-mapping.md)
- [人工标注与阶段 A 验收](docs/text-annotation-guide.md)
- [合成契约样例](docs/examples/text-contract-v0.1.json)
- [阶段 A 交付与复核记录](docs/text-stage-a-review.md)
- [阶段 B 运行、配置与检查](docs/text-stage-b-runner.md)
- [阶段 C 文件说明、演示与测试](docs/text-stage-c-extraction.md)

## 前端与本地联调

- [前端说明与启动指南](frontend/README.md)：页面功能、开发环境、测试账号和验证命令。
- [前端 / 后端 / Harness 协作协议](docs/backend-harness-alignment.md)：文本识别 v0.2、用户确认接口与规划流程。

前端代码、Node 依赖配置和前端测试集中在 `frontend/`；`server/` 是配套本地开发 API，`docs/` 保留跨团队共享文档。前端 npm 命令请在 `frontend/` 目录执行。

## 本地文本识别集成

团队主线已合并文本 API（PR #14）、前端和七牛接入（PR #17）。启动识别服务、配置前端后端和 JSON 样例见 [前端对接说明](docs/text-frontend-integration.md)。前端 npm 命令在 `frontend/` 中运行。

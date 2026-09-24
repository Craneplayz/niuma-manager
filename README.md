# 牛马管理器

“牛马管理器”是 DDL压鼠人小组开发的、面向学生的 AI 任务管理与学习规划助手。项目希望从课程通知、邮件或聊天文本中识别任务和截止时间（DDL），经用户确认后生成任务，并逐步提供排程、提醒和进度管理能力。

> 文本模块已进入实现与联调，整体产品仍按团队计划开发。

## 项目文档

- [产品定义与需求说明](docs/product-requirements.md)：项目背景、目标用户、产品边界和全局质量要求。
- [Product Backlog](docs/product-backlog.md)：高层级功能需求及其当前状态。
- [项目协作规范](docs/scrum-workflow.md)：Scrum 角色、工件、活动、任务状态和完成条件。
- [参与开发](CONTRIBUTING.md)：领取 Issue、创建分支、提交 Commit、发起 PR 和完成 Review 的操作步骤。

## 当前工作方式

小组使用 GitHub Issues 记录细化后的需求和任务，使用 GitHub Project 跟踪 Sprint、状态、负责人和工作量，使用 Pull Request 审查代码与文档变更。

产品范围和待实现功能分别以产品定义与需求说明、Product Backlog 为准；团队管理规则和开发操作分别以项目协作规范、参与开发文档为准。

## 文本任务识别模块

阶段 A 提供约定与合成样例，阶段 B 提供可运行的离线处理骨架。离线回放不代表识别效果。

阶段 C 增加共享识别 skill，以及 Agent 草稿的日期换算、来源证据核对和候选生成。可用合成草稿测试。

阶段 D 已提供 DeepSeek 经 Codex 的真实调用适配器和 `.env` 配置。请按[填写密钥与 API 验证](docs/text-api-setup.md)操作；2026-09-24 已完成十个真实 API 合成场景验证，见[交付与复核记录](docs/text-stage-d-review.md)。

现已支持前端 PR #15 所需的 v0.2 JSON 和本地 HTTP 入口，并通过真实模型到前端后端的合成通知联调。CLI 默认输出 JSON。启动步骤、输入输出示例、文件职责和验证结果见[前端对接说明](docs/text-frontend-integration.md)。独立召回率评测按负责人要求暂缓，队友评审与网页人工验收仍待完成。

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
- [阶段 D 输出校验进度](docs/text-stage-d-progress.md)
- [API 配置与测试操作](docs/text-api-setup.md)

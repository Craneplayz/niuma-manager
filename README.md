# Niuma Manager

面向学生的 AI 任务管理与学习规划助手。项目希望从课程通知、邮件或聊天文本中识别任务和截止时间（DDL），经用户确认后生成任务，并逐步提供排程、提醒和进度管理能力。

> 当前阶段：需求整理与 Scrum 流程搭建，尚未进入具体功能实现。

## 项目文档

- [产品需求](docs/product-requirements.md)
- [Product Backlog](docs/product-backlog.md)
- [Scrum 开发流程](docs/scrum-workflow.md)
- [参与开发](CONTRIBUTING.md)

## MVP

第一阶段只完成最小闭环：

```text
粘贴任务文本 → AI 提取任务与 DDL → 用户确认 → 创建任务 → 查看任务列表
```

邮件自动接入、微信自动读取、位置感知和课程回放整理暂不作为首个 Sprint 的必选内容。

## 协作方式

1. 每项需求建立一个 Issue，并写明用户故事和验收标准。
2. Sprint Planning 时从 Product Backlog 中选择本周期任务。
3. 开发者从主分支创建功能分支，不直接向主分支提交。
4. 完成后发起 Pull Request，至少经过一名成员审查。
5. 满足 Definition of Done 后合并，并关闭对应 Issue。

详细规则见 [Scrum 开发流程](docs/scrum-workflow.md)。

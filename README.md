# Niuma Manager

面向学生的 AI 任务管理与学习规划助手。项目希望从课程通知、邮件或聊天文本中识别任务和截止时间（DDL），经用户确认后生成任务，并逐步提供排程、提醒和进度管理能力。

> 当前阶段：需求整理与 Scrum 流程搭建，尚未进入具体功能实现。

## 项目文档

- [产品需求](docs/product-requirements.md)
- [Product Backlog](docs/product-backlog.md)
- [Scrum 开发流程](docs/scrum-workflow.md)
- [空白 Sprint Backlog 模板](docs/sprint-backlog-template.md)
- [空白燃尽图记录模板](docs/burndown-template.md)
- [参与开发](CONTRIBUTING.md)

## MVP

第一阶段只完成最小闭环：

```text
粘贴任务文本 → AI 提取任务与 DDL → 用户确认 → 创建任务 → 查看任务列表
```

邮件自动接入、微信自动读取、位置感知和课程回放整理暂不作为首个 Sprint 的必选内容。

## 协作方式

1. 每项产品任务建立一个 Issue，包括功能、非功能需求、Bug、技术事项和待确认问题。
2. Sprint 计划会议时，团队根据优先级和实际承诺能力共同选择本次任务。
3. 开发者从主分支创建功能分支，不直接向主分支提交。
4. 完成后发起 Pull Request，至少经过一名成员审查。
5. 冲刺结束时通过评审核对实际产出与预期目标，并在回顾会议中确定改进项。

详细规则见 [Scrum 开发流程](docs/scrum-workflow.md)。

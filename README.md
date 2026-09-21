# 牛马管理器

“牛马管理器”是 DDL压鼠人小组开发的、面向学生的 AI 任务管理与学习规划助手。项目希望从课程通知、邮件或聊天文本中识别任务和截止时间（DDL），经用户确认后生成任务，并逐步提供排程、提醒和进度管理能力。

> 当前阶段：需求整理与 Scrum 流程搭建，尚未进入具体功能实现。

## 项目文档

- [产品需求](docs/product-requirements.md)
- [Product Backlog](docs/product-backlog.md)
- [项目协作规范](docs/scrum-workflow.md)
- [参与开发](CONTRIBUTING.md)

## 功能范围

项目当前已记录课程与自学信息获取、多渠道 DDL 识别、日程生成、学习时间分配、任务规划与提醒、主任务拆解、进度与优先级管理、任务依赖、OCR 信息提取、位置场景辅助和课程回放整理等高层级需求。

各项需求的优先级、验收标准和开发顺序尚未确定，具体内容见 [Product Backlog](docs/product-backlog.md)。

## 协作方式

1. 每项需求建立一个 Issue，并写明用户故事和验收标准。
2. Sprint Planning 时从 Product Backlog 中选择本周期任务。
3. 开发者从主分支创建功能分支，不直接向主分支提交。
4. 完成后发起 Pull Request，至少经过一名成员审查。
5. 满足 Definition of Done 后合并，并关闭对应 Issue。

详细规则见 [项目协作规范](docs/scrum-workflow.md)。

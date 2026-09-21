# Scrum 开发流程

## 1. 角色

- **Product Owner**：维护需求和优先级，回答产品问题，验收结果。
- **Scrum Master**：维护流程、主持会议、帮助清除阻塞，不负责替成员分配所有任务。
- **Developers**：共同完成设计、开发、测试、文档和交付。

课程项目中可以一人兼任角色，但 Product Owner 的优先级决策与开发实现责任应明确区分。

## 2. Scrum 工件

- **Product Backlog**：所有希望实现的需求，持续排序和细化。
- **Sprint Backlog**：团队为当前 Sprint 选择并承诺完成的需求。
- **Increment**：本周期结束时可运行、可演示、满足完成标准的产品增量。

## 3. GitHub Project 看板

建议状态：

```text
Product Backlog → Ready → Sprint Backlog → In Progress → In Review → Done
                                      ↘ Blocked
```

- `Product Backlog`：已记录，但尚未准备开发。
- `Ready`：需求和验收标准清楚，可以被选择。
- `Sprint Backlog`：本周期承诺完成。
- `In Progress`：负责人正在处理。
- `In Review`：已发起 PR，等待审查或测试。
- `Done`：满足完成标准并已合并。
- `Blocked`：存在明确阻塞，并在 Issue 中说明原因。

建议创建三个视图：全部 Backlog、当前 Sprint 看板、按负责人分组视图。

## 4. 一周 Sprint 节奏

### Sprint Planning（周期开始）

1. Product Owner 说明最高优先级需求。
2. 团队确定一个清晰的 Sprint Goal。
3. 检查候选 Issue 是否满足 Definition of Ready。
4. 团队估算并选择能够完成的工作量。
5. 成员认领任务并移入 `Sprint Backlog`。

### Daily Scrum（每日异步或 10 分钟会议）

每名成员回答：

- 昨天完成了什么？
- 今天准备完成什么？
- 当前有什么阻塞？

Daily Scrum 用于同步和暴露问题，不用于详细汇报或责罚。

### Sprint Review（周期结束）

- 演示实际可以运行的产品增量。
- 按 Issue 验收标准检查结果。
- 未达到完成标准的任务返回 Product Backlog，不算部分完成。

### Sprint Retrospective（Review 后）

- 哪些做法有效？
- 哪些问题影响了交付？
- 下个 Sprint 只选择一至两个明确改进项。

## 5. Definition of Ready

Issue 进入 Sprint 前必须满足：

- 用户价值或研究目标清楚。
- 范围足够小，可以在一个 Sprint 内完成。
- 验收标准可以验证。
- 依赖和风险已经标明。
- 已完成 Story Points 估算。
- 团队没有关键理解分歧。

## 6. Definition of Done

一个 Issue 只有同时满足以下条件才进入 `Done`：

- 验收标准全部通过。
- 代码和必要测试已完成。
- Pull Request 至少由一名其他成员审查。
- 分支已经合并到主分支。
- 没有已知的阻塞性缺陷。
- 相关文档已经更新。
- 可以在 Sprint Review 中演示。

## 7. 从 Issue 到合并的流程

```text
创建/细化 Issue
  → 进入 Sprint Backlog
  → 创建功能分支
  → 提交代码和测试
  → 发起 PR 并关联 Issue
  → 代码审查与验收
  → 合并主分支
  → Issue 进入 Done
```

分支示例：

```text
feature/12-ddl-extraction
fix/28-ambiguous-time
docs/7-api-design
research/5-wechat-access
```

每名成员原则上同时只保持一个主要 `In Progress` 任务，减少大量工作同时开始却无法完成。

## 8. 建议迭代计划

### Sprint 0：准备和技术验证

目标：让项目具备正式开发条件。

- 确定 MVP 范围和技术栈。
- 设计任务数据结构和主要页面原型。
- 验证 AI 提取 DDL 的效果。
- 调研微信数据获取与隐私风险。
- 建立 GitHub Project、Issue 和 PR 流程。

### Sprint 1：最小闭环

目标：用户能够粘贴文本、确认 AI 提取结果并创建任务。

### Sprint 2：任务规划

目标：用户能够填写预计耗时，并获得可调整的执行时间建议。

### Sprint 3：提醒与重新规划

目标：系统能够追踪进度，并在计划变化时提供重新排程和提醒。

邮件、教学平台、微信自动读取、位置感知和课程回放在 MVP 稳定后重新排序。

## 9. 推荐标签

- 类型：`feature`、`bug`、`research`、`documentation`、`test`
- 优先级：`priority:P0`、`priority:P1`、`priority:P2`
- 模块：`area:frontend`、`area:backend`、`area:ai`、`area:database`、`area:privacy`
- 工作量通过 GitHub Project 的 Story Points 字段管理，不再创建重复标签。

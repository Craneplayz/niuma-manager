# Scrum 开发流程

本文档按照课程课件中的 Scrum 建议组织项目。具体 Sprint 内容不在项目开始时预先指定，而是在每次冲刺计划会议中由团队共同选择。

## 1. Scrum 的三个阶段

### 规划纲要阶段

- 建立大致的项目目标。
- 设计软件体系结构。
- 建立并初步排序产品任务清单。
- 对智能化软件中的关键 AI 能力进行可行性验证。

### Sprint 循环

- 项目由一系列 Sprint 组成。
- 课程图示给出了 2 至 4 周的常见 Sprint 周期，但本项目不机械固定时长。每轮根据课程进度、任务风险和成员可投入时间决定，并在 Sprint 开始前记录在 Sprint Backlog 中。
- 每个 Sprint 都应开发出一个可以评审的系统增量。
- 每轮包含冲刺计划、每日站会、冲刺评审和冲刺回顾。

### 项目结束阶段

- 总结项目结果和经验。
- 完善需要交付的文档，例如系统帮助和用户手册。
- 整理未完成事项、已知问题和后续建议。

## 2. Scrum 角色

- **Product Owner**：维护产品目标和产品任务清单的优先级。
- **Scrum Master**：维护 Scrum 流程，帮助团队发现和处理阻塞。
- **Scrum Team**：共同理解、选择和完成 Sprint 中承诺的工作。

角色人选由团队另行确定；本文档不预设具体成员。

## 3. Scrum 的三个主要工件

### 产品任务清单（Product Backlog）

产品任务清单由需求、故事或特性等组成，并按照重要程度排序，而且持续完善。清单不仅包含功能，也包含非功能需求、已知 Bug、新技术或平台事项，以及待回答的问题和未来功能。

项目中的初始内容见 [产品任务清单](product-backlog.md)。

### 冲刺任务清单（Sprint Backlog）

冲刺计划会议中，团队从产品任务清单中挑选高优先级任务，确定本次 Sprint Goal，并把产品条目进一步拆成可执行任务。

团队能够选取多少内容，取决于团队能够承诺完成多少，而不是取决于产品任务清单中有多少高优先级需求。

冲刺任务清单至少记录：

- Sprint Goal
- 对应的产品任务清单条目
- 具体任务描述
- 任务执行人
- 工作量估算
- 完成状态
- 是否受阻以及阻塞原因

每个 Sprint 使用 [空白 Sprint Backlog 模板](sprint-backlog-template.md) 创建独立记录。模板不预设任何功能或负责人。

### 燃尽图（Burndown Chart）

燃尽图展示 Sprint 中累计剩余工作量及其变化趋势：

- Sprint 开始时记录总估算工作量。
- 每天更新剩余工作量，而不是只记录已经花费的时间。
- 通过实际曲线观察进度过快、过慢或工作量新增的情况。
- 曲线异常时在每日站会中讨论原因和调整措施。

数据使用 [空白燃尽图记录模板](burndown-template.md) 收集，再在 GitHub Project、表格或其他工具中生成图表。

## 4. Scrum 的四个主要活动

### 冲刺计划会议

在 Sprint 开始前召开。团队共同选择和理解即将到来的 Sprint 中要完成的工作，并形成 Sprint Backlog。

会议需要完成：

1. 确定一个清晰的 Sprint Goal。
2. 从 Product Backlog 中选择高优先级条目。
3. 将条目拆成可执行任务。
4. 确定执行人、估算和初始状态。
5. 检查总工作量是否符合团队能够承诺完成的能力。

### 每日站立会议

在整个 Sprint 中每天进行，用于了解项目的真实进度。站会不要求必须线下，可以采用线下面对面、线上语音/视频，或设置固定截止时间的异步文字更新。无论采用哪种形式，每位成员都回答：

1. 上次会议之后做了什么？
2. 下次会议之前准备做什么？
3. 有没有困难？

同步站会结束后，或异步站会截止时间到达后，更新任务状态、阻塞信息和燃尽图数据。站会用于同步和发现问题，不替代详细的问题讨论。

### 冲刺评审会议

在 Sprint 结束时评审产出，确保实际成果与 Sprint Goal 和预期一致。团队应演示可工作的系统增量，并记录反馈以及需要回到 Product Backlog 的内容。

### 冲刺回顾会议

评价本次 Sprint 的过程，并酝酿改进，使下一轮 Sprint 进行得更好。回顾至少记录：

- 本轮有效的做法
- 影响完成工作的困难
- 下一轮准备采取的具体改进

## 5. GitHub 中的对应方式

| 课程中的 Scrum 内容 | GitHub 中的记录位置 |
| --- | --- |
| 产品任务清单 | GitHub Issues + Product Backlog 视图 |
| 冲刺任务清单 | 当前 Sprint 视图 + 独立 Sprint Backlog 文档 |
| Sprint Goal | Sprint Backlog 顶部字段 |
| 执行人 | Issue Assignee / Sprint Backlog Owner |
| 工作量估算 | GitHub Project 数字字段或估算小时 |
| 完成状态 | `Not started`、`In progress`、`Blocked`、`Done` |
| 燃尽图 | 每日剩余工作量记录和图表 |
| 冲刺评审结果 | Sprint Backlog 的 Review 记录 |
| 冲刺回顾结果 | Sprint Backlog 的 Retrospective 记录 |

建议看板状态：

```text
Product Backlog → Sprint Backlog → In Progress → In Review → Done
                                      ↘ Blocked
```

状态列是 GitHub 中的展示方式，不替代三个 Scrum 工件。

## 6. Issue、分支和 PR 流程

```text
产品任务进入 Product Backlog
  → Sprint Planning 时被团队选中
  → 拆分任务并填写 Sprint Backlog
  → 创建 Issue 和功能分支
  → 开发、验证并更新每日剩余工作量
  → 发起 Pull Request
  → 冲刺评审核对产出与预期
  → 合并并记录回顾结果
```

分支示例：

```text
feature/12-ddl-extraction
fix/28-ambiguous-time
docs/7-api-design
research/5-wechat-access
```

## 7. 智能化软件项目的调整

课程指出，AI 模型的每次迭代不一定取得更好效果。智能化软件的需求可能相对明确，但团队并不确定 AI 是否能够有效解决问题。因此：

- 把关键 AI 能力写成有明确问题、时间盒和结论的技术验证项。
- 在模型探索阶段，重点记录实验条件、结果和结论；不为了形式提前堆积大量工程文档和自动化测试。
- 评审 AI 工作时不能只检查“代码是否完成”，还要检查效果是否达到预期。
- 模型达到预期后，再进行工程化过渡，包括流水线、维护迭代、基础设施和工具。
- 验证失败也可以是有效的 Sprint 产出，但必须给出证据、结论和下一步建议。

## 8. Sprint 内容填写原则

仓库不预先规定 Sprint 1、Sprint 2 或后续 Sprint 必须实现哪些功能。每次 Sprint 的目标和任务保持空白，直到团队召开 Sprint Planning，并结合以下信息共同决定：

- Product Backlog 的当前优先级
- 上一次 Review 的反馈
- AI 可行性验证结果
- 成员在本周期的实际时间和能力
- 适合本轮工作的 Sprint 时长与站会形式
- 团队能够承诺完成的总工作量

决定后复制空白模板，按实际编号保存，例如 `docs/sprints/sprint-01.md`。

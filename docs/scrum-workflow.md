# DDL压鼠人小组《牛马管理器》项目协作规范

本文档规定 DDL压鼠人小组在《牛马管理器》项目中的需求管理、Sprint 执行、代码协作和成果验收方式。全体成员按照本规范开展项目工作；未在本文档中确定的事项，由小组讨论形成决议后补充。

## 1. 项目基本信息

| 项目 | 内容 |
| --- | --- |
| 小组名称 | DDL压鼠人 |
| 项目名称 | 牛马管理器 |
| 代码仓库 | `Craneplayz/niuma-manager` |
| 项目管理方式 | Scrum 敏捷开发 |
| Product Owner | 待小组确定 |
| Scrum Master | 待小组确定 |
| Developers | 小组全体开发成员 |

## 2. 协作原则

1. 项目需求、任务状态和负责人以 GitHub Issues 与 GitHub Project 中的记录为准。
2. 产品文档、协作规范和技术文档与代码一同保存在仓库中，并通过 Pull Request 审查。
3. 每项开发工作必须关联 Issue；每个 Issue 必须具有明确目标、负责人和验收条件。
4. 主分支只接收通过审查的 Pull Request，成员不得直接向主分支推送代码。
5. 小组以可运行、可演示或可验证的成果判断任务是否完成，不以代码量或投入时间代替验收。
6. 成员遇到阻塞时须及时公开说明，不得在临近截止日期时才报告问题。
7. 群聊和口头会议中形成的需求、分工或规则变更，须同步到正式项目记录中。

## 3. 角色与职责

### Product Owner

- 维护产品目标和 Product Backlog。
- 根据产品价值和项目风险确定需求优先级。
- 回答和协调产品需求问题。
- 参与 Sprint Review，并确认成果是否符合预期。

### Scrum Master

- 维护本项目的 Scrum 流程。
- 组织 Sprint Planning、进度同步、Sprint Review 和 Sprint Retrospective。
- 跟踪阻塞事项并推动问题解决。
- 确保 Project、Issue 和 Sprint 记录得到及时更新。

### Developers

- 共同完成设计、开发、测试、文档、审查和交付。
- 在 Sprint Planning 中共同评估工作量并承诺任务。
- 主动更新任务状态，及时报告风险与阻塞。
- 审查其他成员的 Pull Request，并对项目整体质量负责。

同一成员可以兼任多个角色，但各项职责均须落实。具体功能负责人在 Sprint Planning 中确定，并记录在对应 Issue 与 Sprint Backlog 中。

## 4. 协作工具与信息归属

| 信息 | 正式记录位置 |
| --- | --- |
| 项目简介与文档入口 | `README.md` |
| 产品范围和需求说明 | `docs/product-requirements.md` |
| 整体产品需求 | `docs/product-backlog.md` |
| 细化后的需求与开发任务 | GitHub Issues |
| Sprint、状态、优先级、估算和负责人 | GitHub Project |
| 具体需求、Bug、调研任务及验收标准 | GitHub Issues |
| 代码与文档修改 | Git 分支与 Pull Request |
| 项目协作规则 | 本文档与 `CONTRIBUTING.md` |
| 临时讨论 | 小组群聊或会议 |

临时讨论产生的决定须由相关负责人同步到正式记录。未同步的信息不作为最终项目依据。

## 5. Scrum 工件

### 5.1 Product Backlog

Product Backlog 首先记录项目希望实现的整体产品能力，包括：

- 主要功能模块；
- 性能、隐私、可靠性等非功能要求；
- 尚未解决的问题与未来能力。

Product Owner 负责维护排序。小组在需求变化、Sprint Review 结束或技术验证产生新结论后更新 Backlog。

高层 Product Backlog 条目不直接作为个人开发任务。需求准备进入 Sprint 前，须细化为可独立验收的用户故事、技术调研或开发任务，并记录为关联对应 Backlog ID 的 Issue。已知 Bug 也须单独建立 Issue。

Product Backlog 条目和拆分后的 Issue 均不预先固定到某个 Sprint。Sprint 归属只能在 Sprint Planning 中依据优先级、依赖关系和小组实际容量确定。

### 5.2 Sprint Backlog

Sprint Backlog 保存小组在当前周期内承诺完成的工作，至少包括：

- Sprint Goal；
- 选入本轮的 Product Backlog 条目；
- 拆分后的具体任务；
- 负责人；
- 工作量估算；
- 当前状态；
- 阻塞原因。

Sprint 周期根据课程进度、任务风险和成员可投入时间确定。周期须在 Sprint 开始前由小组共同确认，并记录在 GitHub Project 中。

### 5.3 Product Increment

每个 Sprint 结束时须形成可以评审的产品增量。增量可以是可运行功能、经过验证的技术方案，或者带有证据的不可行结论，但须符合 Sprint Goal 和对应 Issue 的验收标准。

### 5.4 燃尽记录

Sprint 开始时记录总工作量，执行过程中持续更新剩余工作量。小组通过燃尽趋势识别进度偏差、任务新增和估算变化，并在进度同步时说明异常原因。

## 6. Scrum 活动

### 6.1 Product Backlog Refinement

小组根据产品优先级和后续开发需要定期细化 Product Backlog。细化过程须完成：

1. 明确高层需求的用户价值、范围和边界；
2. 将高层需求拆分为可在一个 Sprint 内独立验收的用户故事、技术调研或开发任务；
3. 为每个 Issue 填写对应的 Product Backlog ID、验收标准、依赖和风险；
4. 由 Developers 共同完成工作量估算；
5. 对仍存在关键疑问的需求继续保留在 Product Backlog，不直接进入 Sprint。

细化只使需求达到可被选择的状态，不代表该需求已经进入某个 Sprint。

### 6.2 Sprint Planning

Sprint 开始前召开计划会议，会议须完成：

1. 确定 Sprint Goal。
2. 从已完成细化的 Issues 中选择符合 Sprint Goal 的工作。
3. 按执行需要进一步拆分具体技术任务。
4. 确定任务负责人、工作量估算和依赖关系。
5. 根据成员实际容量确认小组能够承诺完成的工作量。
6. 在 GitHub Project 中填写 Sprint、状态、负责人和估算。

具体 Sprint 内容不得由单个成员预先决定。

### 6.3 每日进度同步

Sprint 期间每天进行一次进度同步。同步形式由小组在 Sprint Planning 中确定，可以采用线下站会、线上语音或视频会议，也可以在固定截止时间前提交异步文字更新。

每位成员须同步：

1. 上次同步之后完成了什么；
2. 下次同步之前准备完成什么；
3. 当前是否存在困难或阻塞。

同步完成后，任务负责人更新 Issue 与 Project 状态。需要深入讨论的问题另行安排，不占用全体成员的简短进度同步时间。

### 6.4 Sprint Review

Sprint 结束时召开评审会议，小组逐项检查：

- 是否形成了可演示或可验证的增量；
- 实际结果是否符合 Sprint Goal；
- Issue 验收标准是否全部满足；
- AI 或技术验证是否提供了数据、证据和明确结论；
- 哪些反馈和未完成事项需要返回 Product Backlog。

未满足验收标准的任务不得标记为 `Done`，也不得以“完成了一部分”计入已完成成果。

### 6.5 Sprint Retrospective

Sprint Review 后进行回顾，并记录：

- 本轮有效的协作方式；
- 影响进度和质量的问题；
- 下一轮需要执行的具体改进。

每项改进须明确负责人，并在下一轮回顾时检查执行结果。

## 7. Issue 管理规范

### 7.1 Issue 内容

每个 Issue 须包含：

- 清晰的需求、问题或调研目标；
- 背景与范围；
- 可验证的验收标准或预期结论；
- 优先级；
- 依赖与风险。

Issue 在进入 Sprint 前还须明确负责人和工作量估算。范围超过一个 Sprint 可完成能力的 Issue 必须先拆分。

### 7.2 状态定义

| 状态 | 含义 |
| --- | --- |
| `Product Backlog` | 已记录，尚未进入本轮 Sprint |
| `Sprint Backlog` | 已由小组选入当前 Sprint，尚未开始 |
| `In Progress` | 负责人正在处理 |
| `In Review` | 已提交 PR，正在审查或验收 |
| `Blocked` | 存在明确阻塞，Issue 中已记录原因和所需帮助 |
| `Done` | 满足完成条件并已合并或验收 |

每名成员原则上同时只负责一个主要的 `In Progress` 任务。开始新任务前，须先完成、移交或说明当前任务状态。

## 8. Git 与代码协作规范

### 8.1 分支

所有修改从最新主分支创建独立分支：

```text
feature/<issue-number>-<short-name>
fix/<issue-number>-<short-name>
docs/<issue-number>-<short-name>
research/<issue-number>-<short-name>
```

一个分支只处理一个明确目标，不得混入无关修改。

### 8.2 Commit

Commit 使用 Conventional Commits：

```text
feat: add DDL extraction result editor
fix: handle ambiguous relative dates
docs: update collaboration rules
test: cover task deadline parsing
refactor: simplify task status transitions
```

一次 Commit 只表达一个完整目的。仓库中不得提交密钥、账号信息、个人数据、构建产物或仅适用于个人电脑的配置。

### 8.3 Pull Request

每个 PR 须满足：

1. 关联对应 Issue，并使用 `Closes #<issue-number>`；
2. 说明变更内容、验证方式、风险及影响；
3. 提供必要的测试结果，界面改动提供截图或录屏；
4. 由至少一名非作者成员审查；
5. 处理全部阻塞性审查意见；
6. 通过验收后再合并到主分支。

合并完成后删除功能分支，并同步更新 Issue 与 Project 状态。

## 9. AI 与技术调研规范

AI 模型或外部平台接入存在不确定性时，须建立带时间限制的 Research Issue。调研任务须写明：

- 需要回答的问题；
- 实验或调查方法；
- 使用的数据与评价条件；
- 结果与证据；
- 可行、不可行或需要进一步验证的结论；
- 后续行动。

AI 工作不能仅以“接口已经调用”或“代码已经运行”作为完成标准。模型结果须与预期指标或代表性案例比较。验证失败可以作为有效成果，但须留下可复查证据和明确结论。

## 10. 完成条件

Issue 只有同时满足以下条件才能标记为 `Done`：

- 验收标准全部通过；
- 代码、测试和必要文档已完成；
- PR 已由至少一名非作者成员审查并合并；
- 没有已知的阻塞性缺陷；
- Issue 和 Project 信息已更新；
- 成果能够在 Sprint Review 中演示或验证。

Research Issue 还须包含实验记录、证据、结论和后续事项。

## 11. 规范变更

本规范的修改须通过 Pull Request 提交，并由至少一名其他成员审查。涉及角色职责、Sprint 流程、验收条件或主分支规则的修改，须在小组会议中确认后合并。

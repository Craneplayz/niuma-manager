# 参与开发

本文档只说明成员从领取 Issue 到合并 Pull Request 的实际操作。Scrum 角色、活动、任务状态和完成条件以[项目协作规范](docs/scrum-workflow.md)为准。

## 0. 目录归属、对接、审查与整合

本节规定目录负责人、模块对接方式以及 PR 的审查与合并。Issue、分支、Commit、PR 的具体操作仍按第 1–6 节执行。日常操作说明见[协作手册](docs/collaboration-handbook.md)。

### 0.1 目录负责人

| 目录 | 内容 | 负责人 |
| --- | --- | --- |
| `crawler/` | 学院讲座、课程网爬虫 | 【待确认】 |
| `ddl_text/`、`skills/`、`tests/` | 文本任务识别 | dhjx1234 |
| `frontend/` | 前端页面及与其他模块的对接代码 | Dranix123、【待确认】 |
| `server/` | 本地业务后端 | Dranix123 |
| `contracts/` | 模块之间的数据格式（Schema 与样例） | ken0857900 + 上下游负责人 |
| 根目录文件、`docs/`、`.github/` | 项目总说明与共享文档 | ken0857900（整合负责人） |

1. 成员的分支只修改自己负责的目录；需要改动其他目录时，先在 Issue 中说明，并请该目录负责人审查。
2. 各模块的说明写在 `<模块>/README.md`，忽略规则写在 `<模块>/.gitignore`，模块文档放在 `<模块>/docs/`。根目录 `README.md` 只保留项目介绍和各模块链接。
3. `<模块>/README.md` 开头须有目录树架构图，列出本模块每个文件和文件夹并各用一句话说明用途；新增、删除或搬移文件时同步更新。
4. `docs/` 只存放全组共享、当前仍然有效的文档。

### 0.2 模块对接

1. 模块之间的数据交接只以 `contracts/` 中的 Schema 和样例为准，各模块不得另行定义第二份。
2. 数据格式的任何改动都必须先修改 `contracts/`，不得只在代码中修改。
3. `contracts/` 的样例须覆盖边界情况，至少包括：无 DDL、日期仅精确到天、字段为空、单条输入对应多个任务。
4. 数据提供方的测试须校验其输出符合 `contracts/` 的 Schema；数据使用方的测试须以 `contracts/` 的样例作为输入。
5. 对接代码由数据使用方在其目录内编写和维护。
6. 对接出现问题时，以 `contracts/` 为准判断责任：数据不符合约定的，由提供方修改；数据符合约定而使用方处理错误的，由使用方修改；约定本身不明确或双方理解不一致的，由整合负责人决定如何修订 `contracts/`。

### 0.3 分支

1. 所有成员直接在主仓库创建分支，不使用 fork。
2. 发起 PR 前先将最新的 `master` 合并到自己的分支，冲突由 PR 作者解决。
3. 暂不合并的内容以 Draft PR 提交。
4. 分支在 PR 合并后删除；未合并的分支不得删除。不维护长期个人分支。
5. 分支名须以自己的 GitHub 帐号（小写）开头，格式见第 2 节。

### 0.4 审查与合并

1. 每个 PR 须经一名非作者成员审查通过，任何成员均可审查。
2. 审查通过后由 PR 作者自行合并。
3. 修改 `contracts/`、根目录文件、`docs/` 或 `.github/` 的 PR，须经整合负责人审查，并由其合并；其他成员不得批准此类 PR。
4. `.github/CODEOWNERS` 用于自动通知相关负责人，不代表只有负责人可以审查。

### 0.5 整合

1. 整合负责人每周【待确认】完整运行一次 爬虫 → 文本识别 → 前端 的链路。
2. 发现问题时，按 0.2 第 6 条判断责任，开 Issue 指派给对应目录的负责人，不直接修改他人目录。

### 0.6 负责人调整

1. 新任务由整合负责人指派到现有目录，或新建目录并登记负责人。
2. 成员转做其他任务时，须为原目录指定维护人（可以仍为原作者）。
3. 更换负责人时，须同时修改 0.1 的表格与 `.github/CODEOWNERS`。
4. 每个目录在任何时候都必须有一名负责人。

### 0.7 记录与提交范围

1. 进度、验证结果和阶段总结记录在 Issue 或 PR 描述中，不另行新增阶段进度文档。
2. 日志和爬取数据不得提交到仓库；需要保留的验证结果（如实测 JSON）放在 `<模块>/docs/archive/`。
3. 文档更新后，正式位置只保留最新版本；被取代的旧版本、过程记录与历史决策移至 `<模块>/docs/archive/`，文件名加上日期（如 `crawler-2026-09-26.md`）。

## 1. 开始处理 Issue

1. 在 GitHub Project 中选择已经进入当前 Sprint 的 Issue。
2. 确认 Issue 已关联 Product Backlog ID，并包含验收标准、优先级、工作量估算和负责人。
3. 检查依赖事项是否已经解决；存在阻塞时先在 Issue 中记录。
4. 开始工作时将 Issue 状态改为 `In Progress`。

## 2. 创建分支

不得直接向主分支提交。每个 Issue 使用独立分支，分支名以自己的 GitHub 帐号（小写）开头：

```text
<github-id>/feature/<issue-number>-<short-name>
<github-id>/fix/<issue-number>-<short-name>
<github-id>/docs/<issue-number>-<short-name>
<github-id>/research/<issue-number>-<short-name>
```

例如：

```bash
git switch master
git pull --ff-only
git switch -c dhjx1234/feature/12-ddl-extraction
```

一个分支只处理一个 Issue 或一个可以独立审查的目标，不得混入无关修改。

## 3. 提交 Commit

Commit 使用简短、清晰的 Conventional Commits：

```text
feat: add DDL extraction result editor
fix: handle ambiguous relative dates
docs: update collaboration rules
test: cover task deadline parsing
refactor: simplify task status transitions
```

一次 Commit 只表达一个完整目的。仓库中不得提交密钥、账号信息、个人敏感数据、构建产物或仅适用于个人电脑的配置。

## 4. 创建 Pull Request

1. 推送分支并创建 Pull Request。
2. PR 标题使用与 Commit 相同的格式。
3. 使用 `Closes #<issue-number>` 关联 Issue。
4. 按模板填写变更内容、验证方式、风险和影响。
5. 界面修改提供截图或录屏；功能修改提供必要的测试结果。
6. 将 GitHub Project 中的状态更新为 `In Review`。
7. 邀请至少一名非作者成员进行 Review。

## 5. Review 与修改

审查者须检查：

- 是否满足 Issue 的验收标准；
- 是否包含超出本次 Issue 的无关修改；
- 是否存在隐私、安全或数据丢失风险；
- 异常情况是否得到处理；
- 是否包含必要的测试和文档；
- 代码是否便于其他成员理解和维护。

PR 作者须处理全部阻塞性意见。修改后继续提交到原分支，不重复创建 PR。

## 6. 合并与收尾

1. 确认 PR 已通过至少一名非作者成员审查。
2. 按[项目协作规范中的完成条件](docs/scrum-workflow.md#10-完成条件)检查成果。
3. 合并 PR 并删除对应分支。
4. 确认 Issue 已关闭，并将 GitHub Project 状态更新为 `Done`。
5. 如果产生新的问题或未完成事项，另外建立 Issue，不在已完成的 Issue 中继续追加范围。

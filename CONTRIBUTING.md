# 参与开发

本文档只说明成员从领取 Issue 到合并 Pull Request 的实际操作。Scrum 角色、活动、任务状态和完成条件以[项目协作规范](docs/scrum-workflow.md)为准。

## 1. 开始处理 Issue

1. 在 GitHub Project 中选择已经进入当前 Sprint 的 Issue。
2. 确认 Issue 已关联 Product Backlog ID，并包含验收标准、优先级、工作量估算和负责人。
3. 检查依赖事项是否已经解决；存在阻塞时先在 Issue 中记录。
4. 开始工作时将 Issue 状态改为 `In Progress`。

## 2. 创建分支

不得直接向主分支提交。每个 Issue 使用独立分支：

```text
feature/<issue-number>-<short-name>
fix/<issue-number>-<short-name>
docs/<issue-number>-<short-name>
research/<issue-number>-<short-name>
```

例如：

```bash
git switch master
git pull --ff-only
git switch -c feature/12-ddl-extraction
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

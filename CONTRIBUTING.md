# 参与开发

## 开始前

1. 只处理在冲刺计划会议中由团队共同选入当前 Sprint 的任务。
2. 确认 Sprint Goal、任务描述、执行人、估算和状态已经记录。
3. 将 Issue 状态改为 `In Progress`。

## 分支规范

不要直接向主分支提交。每项工作创建独立分支：

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

## Commit 规范

使用简短、清晰的 Conventional Commits：

```text
feat: add DDL extraction result editor
fix: handle ambiguous relative dates
docs: describe sprint workflow
test: cover task deadline parsing
refactor: simplify task status transitions
```

一次提交只处理一个清晰目的，不提交密钥、个人数据、构建产物或本地配置。

## Pull Request 流程

1. 推送功能分支并创建 Pull Request。
2. PR 标题使用与 Commit 相同的格式。
3. 在 PR 中使用 `Closes #<issue-number>` 关联 Issue。
4. 填写变更说明、验证方式、截图和风险。
5. 至少由一名非作者成员审查。
6. 处理审查意见并确保验收标准通过。
7. 在冲刺评审中核对产出与预期；确认完成后合并并将 Issue 移至 `Done`。

## Review 重点

- 是否满足 Issue 的验收标准？
- 是否超出本次 PR 的范围？
- 是否存在隐私、安全或数据丢失风险？
- 异常情况是否得到处理？
- 是否有必要的测试和文档？
- 代码是否容易让其他成员理解和维护？

## 禁止事项

- 不直接向主分支推送。
- 不在没有 Issue 的情况下开始较大功能。
- 不把多个无关需求放进同一个 PR。
- 不在当前 Sprint 中随意增加未评估需求。
- 不将“代码写完”直接视为完成；冲刺评审还需要核对产出与预期是否一致。

# 牛马管理器 · 协作手册

本手册是[参与开发](../CONTRIBUTING.md)第 0 节的白话版，方便日常操作；两者有出入时以 `CONTRIBUTING.md` 为准。

## 一、谁管哪里

```text
niuma-manager/
├── crawler/     爬虫                → Craneplayz（许智翔）
├── wechat/      微信数据             → 肖涵
├── ddl_text/    文本识别             → 杨沐之
├── task_planning/ 优先级与排程        → 潘勇圳
├── frontend/    前端（含对接代码）     → Dranix
├── server/      后端                → Dranix
├── contracts/   模块间的数据格式       → Ken + 上下游
└── 根目录、docs/ 公共文件             → Ken
```

**核心原则：只改自己的文件夹。**

自己模块的说明、忽略规则、文档都放在自己的文件夹里（`你的模块/README.md`、`你的模块/.gitignore`、`你的模块/docs/`），不要改根目录的文件。

`你的模块/README.md` 开头要画一张目录树架构图，每个文件、文件夹后面写一句它是做什么的；文件有增删或搬家时记得一起更新。

**文档写重点，不写废话**：每件事一句话讲完，例如「爬取结果放在 `crawler/data/normalized/`」就好；背景、开发过程写在 Issue / PR，不要写进 README。

## 二、做一件事的流程

1. **开 Issue**：Issues → New issue，写清楚要做什么，Assignees 选自己，记下编号（例如 #25）
2. **开分支**：在主 repo 上开，不要 fork；分支名以自己的 GitHub 帐号（小写）开头

   ```bash
   git fetch
   git switch -c 你的GitHub帐号/feature/25-简短描述 origin/ken0857900/refactor/repo-restructure
   ```

3. **写代码、commit**：只动自己的文件夹
4. **发 PR 之前先同步开发分支**：`git pull origin ken0857900/refactor/repo-restructure`，冲突由作者自己解决
5. **发 PR**：base 选 `ken0857900/refactor/repo-restructure`（**不要选 master**），描述里写 `Refs #25`，GitHub 会自动通知对应文件夹的负责人
6. **有人批准后自己按 Merge**，然后删分支

> **`master` 只由 Ken 合并**：开发分支整合测试通过后，由 Ken 合到 `master`，其他人不要往 `master` 发 PR。
>
> 合并之后删分支不会丢版本：commit 已经在开发分支里了，PR 页面还可以 Revert 或 Restore branch。**还没合并的分支不能删。**

## 三、PR 谁来审

- 每个 PR 都需要**一位非作者的组员**按 Approve，谁都可以；批准后作者自己合并
- 改到 `contracts/`、根目录、`docs/` 的 PR，**要等 Ken 批准**，并由 Ken 合并

## 四、前端和数据端怎么对接

两边唯一的交集是**数据格式**，写在 `contracts/`。

**第 1 步：先定格式（经过 Ken）**

双方商量好字段，一人发 PR 只改 `contracts/`（Schema + 示例 JSON）。示例要包含边界情况：没有 DDL、日期只到天、字段为空、一次识别出多个任务。Ken 和双方审查后合并。

**第 2 步：各做各的（不经过 Ken）**

- 数据端：输出要通过 `contracts/` 的 Schema 校验，写进自己的测试
- 前端：对接代码写在 `frontend/`，先用 `contracts/` 的示例 JSON 开发和测试，不用等数据端做完
- 两边的 PR 各自审、各自合并

**要改格式？** 回到第 1 步，先改 `contracts/`，不能只改代码。

## 五、接不起来的时候

对照 `contracts/` 判断是谁的问题：

| 情况 | 谁修 | 要不要找 Ken |
| --- | --- | --- |
| 数据不符合 `contracts/` | 数据端 | 不用，前端直接找数据端 |
| 数据符合，但前端没处理好 | 前端 | 不用 |
| `contracts/` 没写清楚，或双方理解不一样 | Ken 决定怎么改 `contracts/`，双方再照着改 | **要** |

## 六、常见情况

- **写到一半，暂时不想合并？** 发 Draft PR（选 Create draft pull request）
- **PR 有冲突？** 通常发生在同一个文件夹有两个人的时候（例如前端）；同步开发分支后由 PR 作者自己解决
- **发现别人的模块有 bug？** 开 Issue 指派给那个文件夹的负责人，不要直接改
- **进度、测试结果写哪？** 写在 Issue 或 PR 描述里，不另外新增进度文档；日志、爬取的数据不要提交，要保留的验证 JSON 放 `你的模块/docs/archive/`
- **文档更新了，旧版怎么办？** 正式位置只放最新版，旧版移到 `你的模块/docs/archive/`，文件名加日期（例如 `crawler-2026-09-26.md`）
- **我的部分做完，要去做别的？** 跟 Ken 说，决定原本的文件夹由谁维护（可以还是你）。每个文件夹都要有负责人

## 七、Ken 做什么

- 管 `contracts/` 和公共文件，审查格式时确认示例覆盖了边界情况
- 每周六下午把 爬虫 → 文本识别 → 前端 完整跑一次，发现问题开 Issue 给对应的人
- 分配新任务：属于现有文件夹的交给负责人，全新的功能就新建文件夹并指定负责人
- 契约有争议时拍板

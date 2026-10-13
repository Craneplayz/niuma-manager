# 新环境下的团队开发与测试

日常改代码用本机 development，团队联调用服务器 test，正式用户使用 production；三者账号、数据库、附件和配置独立，但沿用同一套代码及功能分支流程。

本文面向所有成员；参数、容器部署与回滚细节见 [环境操作手册](ENVIRONMENTS.md)，代码协作与审查要求见 [团队公约](../../CONTRIBUTING.md)。

## 1. 先选对入口

| 用途 | 入口 | 谁来操作 | 数据范围 |
| --- | --- | --- | --- |
| 本机开发 | `http://127.0.0.1:1431`，按需启动 | 修改代码的成员 | 自己电脑的 `server/data/development/` |
| 团队测试 | SSH 隧道后访问 `http://127.0.0.1:1531` | 已获服务器 SSH 授权的成员 | 服务器 `niuma-test-data` 卷 |
| 正式使用 | `http://47.94.152.31:1331` | 正式用户；发布由当次部署者操作 | 服务器原生产卷 `niuma-data` |

测试服务器不是自动跟随 GitHub 更新的预览站；测试前看页面环境标识，访问 `/api/version` 核对 `environment` 和 `revision`，确认当前版本包含自己的修改。

本机前后端显示同一个启动提交号，未提交修改会单独标明；这不是源码内容的实时指纹，修改后重新启动再核对。

三个环境须分别注册账号、设置个人来源；生产账号不会自动出现在测试环境。不同测试账号的数据也彼此隔离，共享测试服务时各成员使用自己的账号。云端 test 的模型已由部署者配置，测试成员不需要各自填写 API key；本机 development 的模型由开发者配置。

云端账号和任务仍保留；迁移后 Cookie 名称改变，生产用户重新登录即可。当前部署目录、镜像和备份位置以 [PR #150](https://github.com/Craneplayz/niuma-manager/pull/150) / [Issue #149](https://github.com/Craneplayz/niuma-manager/issues/149) 的交接记录为准。

## 2. 在本机开发

### 获取代码

按团队公约，从 `origin/ken0857900/refactor/repo-restructure` 开自己的功能分支，PR 仍提交到该开发分支；不创建长期 development/test/production 代码分支。

以下 Git 示例的 `origin` 指团队仓库 `Craneplayz/niuma-manager`；仍使用旧 fork 的成员应换成指向团队仓库的 remote 名称。

环境入口由 #150 引入；该 PR 尚未合入时，旧开发分支没有 `server/manage_environment.py`。需要提前验收的成员可在**独立工作树**预览：

```bash
git fetch origin pull/150/head
git worktree add --detach ../niuma-environment-preview FETCH_HEAD
```

该目录用于预览；#150 合入后，在原功能分支同步开发分支即可正常使用。不要覆盖现有工作目录的未提交修改，也不要从个人电脑复制零散补丁。

### 首次安装与启动

使用仓库支持的 Node、Python；可沿用 Windows CI 的 Node 24、Python 3.11，爬虫使用 pnpm 10.17.1。以下 PowerShell 命令在**仓库根目录**执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server/requirements-test.txt -r ddl_text/teaching_requirements.txt
npm --prefix frontend ci
.\.venv\Scripts\python.exe server/manage_environment.py init development
```

在编辑器打开 `server/deploy/environments/development/.env.model`，填写 provider、model、endpoint、API key；已有配置则沿用。环境入口**不读取根目录 `.env`**，也不把密钥放进前端。配置文件已被 Git 忽略，勿手动强制提交。

```powershell
.\.venv\Scripts\python.exe server/manage_environment.py check development
.\.venv\Scripts\python.exe server/manage_environment.py local development
```

一个命令会启动网页和业务 API，访问 `http://127.0.0.1:1431`；Ctrl+C 停止这次启动的两个服务。前端修改由 Vite 更新，修改 Python 后端、识别规则或模型配置后重启该命令。

macOS/Linux 用 `python3 -m venv .venv`，其余 Python 命令将解释器替换为 `.venv/bin/python`；环境入口命令相同。不需要另开识别或规划 Harness。

第一次安装或 lockfile 改变时才需要 `npm ci`；已运行 Vite 时先停止它再安装，避免 Windows 文件占用。

### 需要在本机测试教学网时

已安装 pnpm 10.17.1 后，在仓库根执行：

```bash
pnpm --dir crawler install --frozen-lockfile
pnpm --dir crawler exec playwright install chromium
```

Linux 缺少浏览器系统依赖时按 [爬虫说明](../../crawler/README.md) 安装；云端测试镜像已带爬虫与浏览器，单纯通过网页测试不需要在自己的电脑重复安装。

修改识别逻辑使用 [文本模块测试命令](../../ddl_text/README.md)，其他模块按各自 README 检查；涉及多个模块时，补测“输入 → 识别 → 保存 → 页面操作”的真实流程。

## 3. 使用团队测试服务器

1. 请 Dranix123 为自己的服务器系统账号配置公钥；使用自己的私钥，不共用他人账号或私钥。这里只测试网页不需要 sudo 权限。
2. 在本机终端运行以下命令，将路径和 `niuma-yourname` 替换为自己的值；首次连接核对服务器指纹。

```bash
ssh -i "YOUR_PRIVATE_KEY_PATH" -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:1531:127.0.0.1:1531 niuma-yourname@47.94.152.31
```

3. 保持终端运行，浏览器打开 **`http://127.0.0.1:1531`**，注册自己的测试账号；使用 `127.0.0.1`，不要随意换成 `localhost` 或其他端口，否则可能与允许的 Origin 不符。
4. 结束时 Ctrl+C 关闭隧道，服务器 test 仍在运行，测试数据保留。

服务器 test 只监听回环地址，直接访问公网 IP 的 1531 端口不会打开测试站。若本机 1531 被本地 test 占用，先停止那个本地服务；日常 development 在 1431，可以与隧道同时使用。

共享 test 是**单一候选版本**；更换版本前在对应 PR 说明提交号和验收内容，并协调正在测试的人。不要在生产页清数据或复现破坏性操作，也不要在共享 test 随意清除他人的账号。

使用合成、脱敏或明确授权的材料；测试环境默认关闭后台恢复，手动识别仍会真实调用模型并产生费用。环境分开不等于模型预算分开，使用同一课程 API key 仍共享额度；定时同步尚未实现。

## 4. 从修改到发布

1. **开发者**：在自己的功能分支完成修改及相关检查，提交 commit 和 PR，写明操作入口、测试数据、预期效果与依赖。
2. **当次测试部署者**：与正在使用 test 的成员协调后，在干净的候选检出中构建并部署指定提交；保持独立 test 配置，发布后核对 `/api/version`。测试方法和结果写回 PR。
3. **测试成员**：在 test 使用自己的账号验收完整流程，反馈问题时附提交号、操作步骤、预期与实际结果，不贴密钥或私人材料。
4. **审查者**：任意一名非作者成员审查无误并 Approve，必要 CI 通过且无阻塞后即可合并；不等待指定负责人。
5. **当次生产部署者**：先用 `accept test --evidence "验收记录"` 保存实际测试镜像 ID，备份后发布同一组镜像；生产入口会拒绝缺少记录、镜像不符和 `latest`，回滚沿用已保存的原验收记录，命令见 [发布与回滚](ENVIRONMENTS.md#5-后续发布停止与回滚)。

**合并 PR、更新服务器源码、实际部署是三件不同的事。** 合并不会自动替换线上容器；构建内容改变时补验受影响流程，不能用生产重新构建一个未经测试的版本。

服务器管理统一在当前部署工作目录执行 `python3 server/manage_environment.py status test` / `status production`，需要重建时执行对应 `up`；不要启动旧的 `niuma-api` / `niuma-web` 回退容器来做日常更新。

模型配置现在位于该工作目录的 `server/deploy/environments/<环境>/.env.model`；原 `/root/niuma-runtime/.env.model` 已不再是新生产容器的配置入口。修改模型配置后必须重建对应环境容器，单纯重启旧容器不会读取新的 Compose 配置。

新检出不会自动带上忽略的环境配置；部署者须从同一环境保留的配置安全迁移，并复核 `check`，不能拿生产配置初始化 test。已有生产接管已完成，日常更新保持 `adopt_existing_data=false`，不得删除环境标记或执行 `down -v`。

## 5. 常见现象

| 现象 | 先检查什么 |
| --- | --- |
| 生产账号无法登录 test | 环境账号独立，在 test 单独注册。 |
| test 打不开 | SSH 隧道是否运行，本机 1531 是否被其他进程占用。 |
| 测试看到旧效果 | `/api/version` 是否为待测提交；合并 PR 不自动部署。 |
| 模型未配置 | 当前环境的 `.env.model`，不是根目录 `.env`；修改后重启本地服务或重建对应容器。 |
| 正在排队 | 20 路是进程内所有功能共享的模型准入，不代表教学网每次同步都自动并行。 |
| 本机修改后线上没变化 | 本机 development 与服务器 test / production 完全独立，需要另行构建和部署。 |

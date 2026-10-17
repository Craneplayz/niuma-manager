# 开发、测试与生产隔离

一套代码与镜像，通过明确环境启动；本文件是环境入口，云资源与 Docker 安装见 [部署指南](README.md)。

第一次使用或只需联调的成员先看 [团队使用指南](TEAM_GUIDE.md)，包含本机开发、测试服务器 SSH 入口及从 PR 到发布的分工。

| 环境 | 默认网页 / 本地 API | Compose 项目 | 默认数据卷 | 自动恢复后台作业 |
| --- | --- | --- | --- | --- |
| development | 127.0.0.1:1431 / 1432 | niuma-development | niuma-development-data | 关闭 |
| test | 127.0.0.1:1531 / 1532 | niuma-test | niuma-test-data | 关闭 |
| production | 127.0.0.1:1331 / 仅容器内 1332 | niuma-production | 显式核对原生产卷 | 开启 |

配置在 `server/deploy/environments/<环境>/config.json`，模型配置在同目录 `.env.model`，二者均不入 Git / 镜像；开发和测试不继承仓库根 `.env` 或终端遗留的模型、Compose 配置。

邮箱验证码配置使用同目录独立 `.env.mail`，初始化只补充缺失模板，未配置不阻断已有账号登录；新注册和补验证需先完成 [阿里云发信配置](EMAIL.md)。本地与容器均只读取当前环境邮件配置，Compose 入口生成私有 `.env.mail.compose`；邮件凭据不得放入模型文件或前端变量。Compose 需 2.24 或更新版本。

模型配置沿用本地识别器的字面量格式，不展开 `$变量`、反斜杠或行内注释；注释单独成行。容器入口自动生成同目录私有 `.env.model.compose` 和 `.env.compose`，不要手工编辑或提交它们。[Compose 变量展开规则](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)

本地数据在 `server/data/<环境>/`，容器数据在独立卷的 `/data`，包括 SQLite、凭据加密密钥、附件及作业记录；不得跨环境共享或复制生产数据来初始化测试。

## 1. 一次准备三个环境

仓库根目录执行（Windows 使用 `.venv\Scripts\python.exe`，Linux 使用相应虚拟环境的 `python`）：

```bash
python server/manage_environment.py init all
python server/manage_environment.py check development
python server/manage_environment.py check test
python server/manage_environment.py check production
```

初始化只补充缺失配置，不覆盖现有文件、不复制密钥、不创建生产账号；分别编辑需要调用模型的环境的 `.env.model`，不要将密钥放入 `config.json` 或 `VITE_*`。

`check` 只检查配置边界，不证明服务器已启动、密钥有额度或模型可调用；环境目录权限应限制为部署者可访问。

`config.json` 可设置 origin、监听地址和端口、HTTP/HTTPS、镜像及后台恢复；测试数据卷名称固定，生产可明确指定旧卷，多个配置指向同卷会被拒绝。

`origin` 的端口须与该协议发布端口一致；HTTPS 的 `port` 是 HTTP 跳转入口，`https_port` 是 HTTPS 入口，二者不能相同。比如 `https://test.example.com:1543` 对应 `site_address=test.example.com:1543`、`https_port=1543`；容器目标端口也跟随站点端口，避免代理在 1543 监听却转发到 443。[Caddy 站点地址规则](https://caddyserver.com/docs/caddyfile/concepts#addresses)

入口会按浏览器规则将域名转为小写并省略默认 `:80` / `:443`，避免登录请求因 Origin 表示不同而被拒绝。

## 2. 本地开发与测试

先安装 `server/requirements-test.txt` 和 `frontend/` 的 `npm ci`，然后在仓库根启动所需环境：

```bash
python server/manage_environment.py local development
# 另一个终端可同时启动隔离的测试环境：
python server/manage_environment.py local test
```

页面分别为 `http://127.0.0.1:1431` 和 `http://127.0.0.1:1531`；Ctrl+C 停止本命令启动的两个子进程，不停止其他环境。

本机启动同时向 API 和前端注入当前提交号及源码修改状态，页面标明“有未提交修改”；这是启动时快照，修改代码后须重启核对，Git 不可用时显示 unknown，不沿用终端里的旧版本号。

Windows 也可运行 `powershell -ExecutionPolicy Bypass -File server/start-local-test.ps1 -Environment test`；旧 `server/data/teaching-test.sqlite3` 保留原处，不会自动复制到新测试环境。

需要单独调试进程时使用 `api <环境>` 与 `web <环境>` 两个命令；原 `npm run dev:api` 保留兼容旧本地安装，不能用它代替生产或隔离测试入口。

## 3. 测试服务器与构建

已提交源码并安装 Docker Compose v2 后：

```bash
python server/manage_environment.py build test
python server/manage_environment.py up test
python server/manage_environment.py status test
```

命令固定项目名并显式选择环境文件，不受终端残留的生产配置覆盖；默认只监听服务器回环地址，测试通过 SSH 隧道访问，或由维护者配置独立测试域名和 HTTPS。

`build production` 被禁止；构建时 development/test 的 `api_image`、`web_image` 必须留空，以提交号生成新标签，不能覆盖已填写的发布镜像。`up` 仍可使用明确指定的已构建镜像。

例如已有 SSH 连接名 `niuma-server` 时，本机执行 `ssh -N -L 1531:127.0.0.1:1531 niuma-server` 后访问本机 1531；实际连接名由部署者提供。

测试与生产优先使用不同主机名；仅换端口不隔离 Cookie，因此服务另使用 `niuma_session_development/test/production` 三个名称，且数据和会话表也独立。[Cookie 作用域说明](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)

页面左下角显示当前环境，悬停查看前后端版本；`GET /api/version` 返回环境、提交号和后台恢复状态，不返回密钥、账号或磁盘路径。

验收实际运行的 test API / web 后，记录本次结果或验收链接：

```bash
python server/manage_environment.py accept test --evidence "验收链接或合成输入、预期与实际结果"
```

命令检查 test 容器运行状态、API 健康状态、协议和前后端完整提交号，保存实际镜像 ID 到 `test/accepted-images.json`；填写镜像配置但尚未部署时拒绝记录。该文件是部署者的验收记录，不替代业务测试，不放密钥或私人材料；环境配置目录已排除版本控制，发布前保留它的副本以便回滚。

## 4. 接管现有生产环境

由当前部署维护者执行；开始前记录原容器、Compose 项目、启动配置、实际挂载卷 / 目录、镜像摘要与可用回滚命令，不能仅凭默认卷名猜测。

1. 先在独立 test 环境验收准备发布的同一 API / web 镜像，确认登录、输入识别、任务持久化及所需采集流程；记录版本和实际未通过项。
2. 按 [备份步骤](README.md#10-数据备份更新和回退) 暂停写入并备份整个原 `/data`，包括数据库、`credential.key` 和附件；验证备份可读，保留旧容器与镜像。
3. 编辑 production 的 `config.json`：填写原数据卷名，`external_data_volume=true`，首次接管未标记的旧目录设置 `adopt_existing_data=true`；设置实际 origin、监听地址、端口及已验收的 API / web 镜像摘要或固定提交标签，并用 `accept test` 保存同协议的验收记录。
4. 旧部署若使用主机目录挂载，先将完整备份恢复到单独命名卷并验收；不要把目录路径填进 `data_volume`，不要覆盖原目录。
5. 停止旧 API 和网页后执行 `python server/manage_environment.py up production`；仍有其他运行部署使用该卷时命令拒绝启动，外部卷不存在时 Compose 报错，不静默新建空生产卷。
6. 核对 `/api/version`、登录后的原任务 / 来源 / 附件、重启持久化和页面版本；新 Cookie 名称需要重新登录，已有账号与数据保留。
7. 接管成功后把 `adopt_existing_data` 改回 `false`，保留备份，记录环境配置版本、镜像摘要和验收结论。

存储目录的 `.niuma-environment.json` 会拒绝跨环境打开，即使传入接管开关也不能把已标记的生产卷当作测试卷；不要删除该标记来绕过检查。

已标记的目录也拒绝未设置 `NIUMA_ENVIRONMENT` 的旧启动入口；兼容入口仅能继续使用未标记的旧本地目录。生产必须使用显式存在的外部卷，不允许自动创建或使用开发/测试卷名。

生产不允许 seed 示例账号；不提供清空生产数据或 `down -v` 的快捷命令。HTTP/HTTPS 选择沿用现有部署指南，本次隔离不自动迁移域名或证书。

## 5. 后续发布、停止与回滚

测试验收后，将同一对镜像引用填入生产配置并执行 `up production`；入口强制核对 `test/accepted-images.json`，拒绝缺失记录、不同协议、镜像不匹配、`:latest` 或省略标签。不在生产重新构建不同源码；仅 `git pull` 或重启旧容器不算发布新版本。

核对成功后 Compose 使用不可变的 `sha256:` 镜像 ID，禁止启动时拉取或构建；核验后标签被覆盖也不能改变本次发布。目标镜像必须已在该 Docker 主机上，跨主机部署先导入同一镜像并传递验收记录。HTTP / HTTPS 使用不同 web 镜像，切换协议须先在对应协议的 test 验收。

回滚时将生产配置改为原镜像，并用 `python server/manage_environment.py up production --acceptance <已保存的原验收记录.json>` 核对该版本；不得通过删除检查绕过验收。此前部署的运行容器不受代码更新影响，但升级入口后首次新发布须按新流程生成验收记录。

`python server/manage_environment.py stop test` 只停止测试项目，不删除数据；任何环境的重新创建都保留数据卷。

`stop`、`status` 根据固定项目标签定位容器，不依赖当前配置或密钥文件仍然有效；即使配置误改，仍能停止该环境，不影响其他项目。

回滚先停止新生产项目并保存故障现场；确认数据库兼容时恢复原镜像和启动配置，涉及迁移则在独立卷恢复完整备份验证后切换，避免两个 API 同时写一份数据库。

## 6. 验证范围

`python -m unittest discover -s server -p test_environment_isolation.py -v` 启动三个使用临时数据库的真实 API，验证六个方向的原名/改名 Cookie 拒绝、任务/设置具体值、附件上传和账号隔离读取、版本、配置及旧数据接管保护，不调用模型。

安装 Compose CLI 时，同一组测试还校验三个环境的 HTTP/HTTPS 配置、特殊字符模型配置和端口；独立 Compose 可通过 `NIUMA_COMPOSE_TEST_BIN` 指定，无须 Docker 引擎。未安装 CLI 时仅此集成检查跳过。

`.github/workflows/environment-smoke.yml` 在临时 Linux runner 构建候选镜像并运行 `test_container_environment.py`：HTTP / 本地 HTTPS、三环境双向会话与附件隔离、版本一致，以及三个环境分别重建后任务/设置完整值、凭据解密、附件字节与 SHA-256 一致性。模型密钥为空，不调用模型；随机命名项目和卷只保存合成数据，测试结束清理，不连接生产服务器。

当前没有公开附件下载 API：测试通过真实 HTTP 上传，再调用识别器实际使用的账号隔离文件读取器回读字节，不能据此宣称浏览器下载功能已实现。发布约束另用 `python -m unittest discover -s server -p test_release_images.py -v` 验证。

本机复现需 Docker 引擎并设置 `NIUMA_CONTAINER_TEST_API`、`NIUMA_CONTAINER_TEST_WEB_IP`、`NIUMA_CONTAINER_TEST_WEB_HTTPS` 为当前提交构建的镜像，再运行 `python -m unittest discover -s server -p test_container_environment.py -v`。HTTPS 验收仅对本地合成端点接受临时自签证书，不代表公网证书已签发。

教学网定时同步使用各环境私有 `config.json` 的 `scheduled_sync` 布尔开关，省略时兼容为 false；统一入口将其注入 `NIUMA_ENABLE_SCHEDULED_SYNC`，不会继承其他环境终端的值。三个环境均默认关闭，开发 / 测试只在独立合成账号验收时显式设为 true 并重启。`/api/version` 的 `scheduled_sync_available` 表示环境是否开放该能力，账号还须在来源设置中选定来源、间隔并同意启用。`background_recovery` 不能替代此开关。定时运行与手动同步共用队列、数据库和状态保护，详见 [接口约定](../../contracts/docs/source-sync-v0.1.md)。

Compose 配置、镜像启动、服务器挂载和真实业务联调须由对应环境分别验收，不能由本地回归结果推定生产已隔离。[Compose 项目与环境变量规则](https://docs.docker.com/compose/how-tos/project-name/)

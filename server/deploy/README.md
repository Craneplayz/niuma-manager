```text
deploy/
├── README.md                 阿里云 ECS、弹性 IP 与 HTTPS 的部署、验收和维护步骤。
├── Dockerfile                构建 API、HTTPS 网页和 HTTP IP 网页镜像。
├── Dockerfile.dockerignore   排除凭据、数据库、采集数据和本机配置。
├── compose.ip.yaml           单机 HTTP IP 部署，仅向外开放 1331。
├── compose.yaml              域名 HTTPS 部署，仅向外开放 80/443。
├── Caddyfile.eci              HTTP 网页与同网络空间 API 的代理配置。
├── Caddyfile                  HTTPS 网页、证书与 API 代理配置。
├── .env.ip.example           HTTP IP、镜像和数据卷的公开配置模板。
├── .env.example              HTTPS 域名的公开配置模板。
└── .env.model.example        运行时模型配置模板，不包含密钥。
```

# 阿里云部署指南

## 1. 部署条件与功能边界

部署者需要自己可管理、已完成阿里云实名认证的云账号；实名认证仅是云资源开通条件，牛马管理器的注册不收集真实姓名、身份证或实名信息。

本指南面向 Ubuntu 24.04、Linux x86_64 的单台 ECS；内部演示可使用 2 vCPU、4 GiB 内存、30 GiB 系统盘和北京 1 Mbps 弹性 IP，图片、附件及多人并发会增加资源需求。

本地只有 macOS；容器验收、合成调用链与远端 Linux 运行结果不等于 Windows 人工实测。

默认镜像不包含 `wechat/` 采集模块，前后端禁用微信采集、来源凭证、采集器上传与旧采集作业恢复；网站不提供获取微信内容的途径。

用户可以主动上传自己有权提供、自行导出的未加密文字、JSON 或支持的附件；`/api/materials/` 只接受应用登录会话，不接受采集器凭证，拒绝加密材料，材料识别会调用配置的第三方模型。

若有人推动真实微信采集，应由推动者提供并管理自己的实名云账号和服务器，或获得明确授权的组织资源；不得把其他维护者的个人云账号、服务器或域名作为默认承载资源。

更换服务器不等于获得腾讯许可；真实采集须另行核对平台协议、数据授权和实际接入方式，免责声明与模块分工不提供免责保证；本指南不提供开启真实采集的步骤。

## 2. 先确认费用与测试期限

在阿里云费用中心查看代金券的适用产品、地域、计费类型、有效期与剩余额度，分别确认 ECS、系统盘、EIP、流量/CDT 和镜像仓库是否可抵扣。

代金券不是所有费用的统一免单凭证；按量产品通常在出账时抵扣，具体以[代金券使用规则](https://help.aliyun.com/zh/user-center/how-to-use-coupons)和账户实际账单为准。

只接受代金券支出时，不能覆盖的资源不要开通；设置费用提醒、ECS 自动释放时间，并记录 EIP 的单独释放步骤。

ECS 停机或自动释放不代表关联 EIP 已释放；测试结束分别检查 ECS、磁盘、快照、EIP、镜像仓库与公网计费，详见 [EIP 计费说明](https://help.aliyun.com/zh/eip/support/billing-faq)。

模型服务由模型提供商单独计费，阿里云代金券不能据此推定可以抵扣七牛模型费用。

## 3. 创建 ECS、EIP 与安全组

1. 在 ECS 控制台选择北京地域、Ubuntu 24.04 64 位、x86_64 实例、30 GiB 系统盘及自己的 VPC/vSwitch；建议为内部演示设置明确的自动释放时间。
2. 创建专用安全组：HTTP IP 方案只开放入方向 TCP 1331；HTTPS 方案开放 TCP 80/443；不开放 1332、1333、1334。
3. 使用 Workbench 或云助手连接 ECS；首次要求服务关联角色或短信验证时，由云账号所有者完成授权。
4. 若使用 SSH，仅向管理者自己的 IP 开放 TCP 22，不向全部公网开放。
5. 在同一北京地域创建 EIP 并绑定这台 ECS，记录控制台显示的公网 IP；具体操作见 [EIP 绑定说明](https://help.aliyun.com/en/eip/bind-an-eip-to-a-cloud-resource/)。
6. 检查 ECS 关联的所有安全组规则；新增安全组不会自动撤销旧安全组的开放端口。

仅供内部测试也不能代替云平台协议、网站用途和地域要求；采用域名时按实际用途核对[阿里云备案要求](https://help.aliyun.com/zh/icp-filing/)。

## 4. 安装 Docker 并获取代码

以下命令在新建 Ubuntu 24.04 ECS 的终端执行，使用 root 或在命令前加 `sudo`：

```bash
apt-get update
apt-get install -y docker.io docker-compose-v2 git
systemctl enable --now docker
docker version
docker compose version
mkdir -p /opt/niuma
cd /opt/niuma
git clone --branch ken0857900/refactor/repo-restructure --single-branch https://github.com/Craneplayz/niuma-manager.git repo
cd /opt/niuma/repo/server/deploy
```

这些步骤适用于未安装 Docker 的 Ubuntu 24.04；已有 Docker CE 时不要混装 `docker.io`，安装方式见 [Docker Ubuntu 官方说明](https://docs.docker.com/engine/install/ubuntu/)。

私有仓库需要部署者自己的 GitHub 访问权限；不要将访问令牌写入 Git URL、脚本或共享日志。

合并本部署 PR 后上述开发分支即包含全部配置；需要固定版本时记录 `git rev-parse HEAD`，不要依赖维护者本机未提交文件。

云助手批量命令的超时应覆盖构建时间；首次安装 Chromium、Python 和 Node 依赖可能需要十几分钟，优先在交互式终端构建。

## 5. 配置识别与规划模型

```bash
cd /opt/niuma/repo/server/deploy
cp .env.model.example .env.model
chmod 600 .env.model
nano .env.model
```

填写自己的模型密钥，保留以下配置或替换为团队当前确认的兼容模型：

```dotenv
DDL_PROVIDER=qiniu
DDL_MODEL=deepseek/deepseek-v4.1-flash
DDL_ENDPOINT=https://api.qnaigc.com/v1
QINIU_API_KEY=填写自己的七牛密钥
```

如果没有交互式编辑器，可在本机创建私有 `.env.model`，通过云助手“发送文件”上传到 `/opt/niuma/repo/server/deploy/`，指定文件名 `.env.model`、root 所有者和 `0600` 权限。

不要把密钥写入云助手命令文本、Git、`VITE_*`、Docker 构建参数、镜像标签或群聊；Docker 管理员能读取容器运行配置，仅向受信任管理员授予主机访问。

默认业务 API 直接调用识别与规划模块，不需要另外启动 1333/1334；不要设置 `TEXT_HARNESS_URL` 或 `HARNESS_URL`，除非已经部署并准备使用对应独立服务。

更新 `.env.model` 后必须重新创建 API 容器；仅 `docker restart` 不会重新读取 Compose 的 `env_file`。

## 6. 方案 A：HTTP 弹性 IP 内部演示

HTTP 没有传输加密，只用于测试账号与合成数据；不要通过此入口发送个人教学网密码、私人材料或生产凭据。

```bash
cd /opt/niuma/repo/server/deploy
cp .env.ip.example .env
chmod 600 .env
nano .env
```

将文档示例 IP `203.0.113.10` 替换为自己的 EIP：

```dotenv
APP_ORIGIN=http://203.0.113.10:1331
HTTP_PORT=1331
API_IMAGE=niuma-manager-api:local
WEB_IMAGE=niuma-manager-web-ip:local
NIUMA_DATA_VOLUME=niuma-app-data
```

`APP_ORIGIN` 必须包含浏览器实际使用的协议、主机和端口，不加末尾 `/`；多个获准来源用空格分隔，不用逗号或 `*`。

```bash
docker compose -p niuma -f compose.ip.yaml config -q
docker compose -p niuma -f compose.ip.yaml build
docker compose -p niuma -f compose.ip.yaml up -d --no-build
docker compose -p niuma -f compose.ip.yaml ps
curl --fail --retry 10 --retry-connrefused --retry-delay 2 http://127.0.0.1:1331/api/health
```

浏览器访问 `http://自己的EIP:1331/`，自行注册测试账号；项目不会自动登录，也不要求实名。

API 仅在容器内的 `127.0.0.1:1332` 监听；网页容器共享 API 的网络空间，在 1331 提供静态网页并代理 `/api/`，不需要 Kubernetes 或 ECI。

HTTP 网页使用 Dockerfile 的 `web-eci` target，普通 HTTP 下 ID 生成兼容不支持 `crypto.randomUUID()` 的浏览器；Caddy 使用非 root 用户且不依赖 Linux capabilities。

API 容器重新创建时一并重新创建网页容器，以更新共享的网络空间：

```bash
docker compose -p niuma -f compose.ip.yaml up -d --no-build --force-recreate api web
```

在本机试运行时可把 `HTTP_PORT` 改为 `127.0.0.1:18081`、`APP_ORIGIN` 改为 `http://127.0.0.1:18081`，并使用独立项目名和数据卷，避免占用已有 1331 或已有用户数据库。

## 7. 方案 B：域名 HTTPS

使用自己有权用于本项目的域名，将 A 记录指向 ECS 的 EIP；证书签发需要公网可访问 TCP 80/443，DNS 生效且域名用途满足当地及云平台要求。

首次部署时执行：

```bash
cd /opt/niuma/repo/server/deploy
cp .env.example .env
chmod 600 .env
nano .env
```

```dotenv
SITE_ADDRESS=app.example.com
APP_ORIGIN=https://app.example.com
COOKIE_SECURE=1
API_IMAGE=niuma-manager-api:local
WEB_IMAGE=niuma-manager-web:local
NIUMA_DATA_VOLUME=niuma-app-data
```

```bash
docker compose -p niuma -f compose.yaml config -q
docker compose -p niuma -f compose.yaml build
docker compose -p niuma -f compose.yaml up -d --no-build
docker compose -p niuma -f compose.yaml ps
curl --fail https://app.example.com/api/health
```

HTTPS 网页使用 `web` target，由 Caddy 签发及续期证书；保留 `tls-data` 和 `tls-config` 数据卷，浏览器登录 Cookie 带 `Secure`。

从 IP 方案切换时先按第 10 节备份并停止旧项目，保留原 `NIUMA_DATA_VOLUME`，再运行 HTTPS 配置；确认成功后关闭公网 TCP 1331。

HTTPS 不代表可以将无限制的付费模型调用公开给任意访问者；对外开放前另行确定访问范围、模型预算、监控和运行策略。

## 8. 可选：通过 ACR 发布镜像

ECS 上直接构建不需要 ACR；使用 ACR 时由部署者在自己的北京实例中创建命名空间和私有仓库，并在控制台设置专用镜像仓库登录密码。

ACR 密码不一定等于阿里云登录密码；使用控制台显示的用户名、公网地址及 ECS VPC 地址，不使用维护者的仓库凭据；操作见 [ACR 个人版官方说明](https://help.aliyun.com/zh/acr/user-guide/use-a-container-registry-personal-edition-instance-to-push-and-pull-images/)。

以下命令在本机 Bash/zsh 或 WSL 的仓库根目录执行；ECS 是 x86_64，即使本机是 Apple Silicon 也需要构建 `linux/amd64`：

```bash
NIUMA_REGISTRY=填写控制台公网仓库域名
NIUMA_REPOSITORY="$NIUMA_REGISTRY/自己的命名空间/niuma-manager"
NIUMA_TAG=$(git rev-parse --short HEAD)
docker login --username 自己的ACR用户名 "$NIUMA_REGISTRY"
docker buildx build --pull --platform linux/amd64 -f server/deploy/Dockerfile --target api -t "$NIUMA_REPOSITORY:api-$NIUMA_TAG" --push .
docker buildx build --pull --platform linux/amd64 -f server/deploy/Dockerfile --target web-eci -t "$NIUMA_REPOSITORY:web-ip-$NIUMA_TAG" --push .
```

HTTPS 对应网页镜像改用 `--target web` 和不同的标签；不要将 `web-eci` 镜像用于域名自动证书配置。

镜像中不包含模型密钥、数据库、教学网账号、采集目录、本机域名或本地 AGENTS；构建前确认工作区已提交且 `git status --short` 无待发布修改。

在 ECS 的 `.env` 中把 `API_IMAGE`、`WEB_IMAGE` 改成自己的完整仓库地址与构建结果 digest，例如 `自己的VPC仓库域名/命名空间/niuma-manager@sha256:实际摘要`，保留 `.env.model` 和原数据卷。

有交互式终端时，在 ECS 用 VPC 地址执行 `docker login --username 自己的ACR用户名 实际VPC仓库域名`，再执行：

```bash
docker compose -p niuma -f compose.ip.yaml pull
docker compose -p niuma -f compose.ip.yaml up -d --no-build --force-recreate api web
```

### 只有云助手时传输临时登录文件

1. 本机执行 `NIUMA_AUTH_DIR=$(mktemp -d)`，再执行 `DOCKER_CONFIG="$NIUMA_AUTH_DIR" docker login --username 自己的ACR用户名 "$NIUMA_REGISTRY"`；专用目录避免依赖本机系统 credential helper。
2. 通过云助手“发送文件”上传该目录的 `config.json` 到 `/root/niuma-acr-pull/config.json`，指定 root 和 `0600`，等待发送成功后再运行拉取命令。
3. 本机只能连接公网地址时，不要尝试从本机连接仅 VPC 可达的仓库地址；同一 ACR 实例的公网/VPC 地址需在 ECS 登录配置中对应。
4. 在 ECS 执行下面的脚本；先填写控制台给出的同一实例的两个地址，不输出登录文件内容。

```bash
set -eu
test -s /root/niuma-acr-pull/config.json || { echo '指定位置没有 config.json，请重新上传'; exit 1; }
chmod 600 /root/niuma-acr-pull/config.json
trap 'rm -f /root/niuma-acr-pull/config.json' EXIT
export NIUMA_REGISTRY=填写公网仓库域名
export NIUMA_VPC_REGISTRY=填写同一实例VPC仓库域名
python3 - <<'PY'
import json, os
from pathlib import Path
path = Path('/root/niuma-acr-pull/config.json')
value = json.loads(path.read_text(encoding='utf-8'))
auths = value.get('auths', {})
public, vpc = os.environ['NIUMA_REGISTRY'], os.environ['NIUMA_VPC_REGISTRY']
if public not in auths or not auths[public].get('auth'):
    raise SystemExit('登录文件未包含此实例的可转移登录信息，请使用独立 DOCKER_CONFIG 重新登录')
auths[vpc] = auths[public]
path.write_text(json.dumps(value), encoding='utf-8')
PY
cd /opt/niuma/repo/server/deploy
DOCKER_CONFIG=/root/niuma-acr-pull docker compose -p niuma -f compose.ip.yaml pull
docker compose -p niuma -f compose.ip.yaml up -d --no-build --force-recreate api web
docker compose -p niuma -f compose.ip.yaml ps
```

5. 删除本机专用目录里的 `config.json`，例如 `rm -f "$NIUMA_AUTH_DIR/config.json"`；若拉取失败需要重试，应重新上传而非复用已删除文件。

登录文件相当于仓库访问凭据，仅传到自己授权的服务器，不贴到工单或群聊；HTTPS 方案将以上命令中的文件名替换为 `compose.yaml`。

## 9. 部署验收：登录 → 识别 → 日程 → 规划

`{"status":"ok"}` 只证明业务 API 可用，不证明模型已配置；以下步骤分别验收：

1. 使用新建合成测试账号注册、退出、重新登录，检查不同账号的数据隔离；页面不要求应用实名信息。
2. 在工作台输入未来日期的明确任务，例如“请于未来某日 18:00 前提交部署测试报告，需要整理三项测试结果”，点击识别通知，确认收到真实识别结果而非演示回放。
3. 按页面要求确认手动文本候选，检查标题和具体 DDL，进入学习日程查看；无日期任务补充日期，不把缺少日期伪造为来源已给出的时间。
4. 设置足够的可用时间，点击开始规划，检查真实模型工时、任务风险/优先级和简短原因；生成预览时任务与已有日程不变。
5. 应用规划，刷新页面并退出重登，确认任务与时段仍在；按第 10 节重建容器后再次检查持久化。
6. 检查页面说明没有获取微信内容的入口；旧 `/api/sources/wechat/` 返回 403；只用合成未加密材料测试主动上传，加密材料应被拒绝。

教学网全链路必须使用 HTTPS、获授权的账号和实际可达的校园网环境：在“来源管理”保存账号，勾选教学网，回工作台同步，再核对任务、具体 DDL 和规划结果。

镜像已安装 crawler 依赖与 Chromium，不需要用户上传浏览器；服务器能访问互联网不代表能通过校园网认证，实际网络可达性需由部署者确认。

只列出标题、缺少正文或 ZIP 原件未被读取时，页面可能保留来源补充/核对状态；模型重试不会自动补齐采集器没有取得的内容，不能把一条成功任务当成全部来源已完成。

## 10. 数据备份、更新和回退

`/data` 保存 SQLite、来源凭据加密密钥与托管材料；必须备份整个目录，不能只备份数据库或重新 seed 覆盖用户数据。

HTTP 方案在 ECS 执行以下完整备份；HTTPS 方案使用 `compose.yaml`：

```bash
cd /opt/niuma/repo/server/deploy
umask 077
NIUMA_BACKUP=$(mktemp -d /opt/niuma/backup.XXXXXX)
docker compose -p niuma -f compose.ip.yaml stop api
NIUMA_API=$(docker compose -p niuma -f compose.ip.yaml ps -aq api)
test -n "$NIUMA_API"
docker cp "$NIUMA_API:/data" "$NIUMA_BACKUP/data"
docker compose -p niuma -f compose.ip.yaml start api
docker compose -p niuma -f compose.ip.yaml restart web
```

记录备份目录、原 Git SHA、原镜像 digest、项目名和 `NIUMA_DATA_VOLUME`；备份含私人信息，只存放在部署者可访问的位置，不能上传 Git。

更新前先在独立数据库验证新版本，保护已有未提交配置；干净检出可用 `git pull --ff-only` 同步开发分支，再按所选方案 `build` 或 `pull` 并 `up -d --no-build --force-recreate api web`。

Compose 项目名、配置文件和持久数据卷保持一致；本指南新部署默认 `niuma-app-data`，旧安装应显式填写原卷名，不能直接切换默认卷。

从手工 `docker run` 迁移时，先停原网页和 API、备份原容器 `/data`、确定原挂载卷，再让 Compose 使用同一个卷；不能在同一数据库上同时启动两个 API。

回退时先备份新状态，再恢复上一组镜像并确认数据库兼容；涉及数据库结构变化时在独立卷恢复完整备份验证，不把旧数据库直接覆盖仍在运行的服务。

不要执行 `docker compose down -v`、`docker volume prune` 或删除数据卷来修复登录问题；正常重建容器不会删除命名卷。

测试期限到达前导出需要保留的数据，再停止容器并在云控制台分别释放 ECS、EIP 和不再需要的关联资源。

## 11. 故障定位

| 现象 | 检查与处理 |
| --- | --- |
| 浏览器无法打开 | 核对 EIP 绑定、全部安全组、实际访问端口及 `docker compose ... ps`；不要开放 API 或模型端口来绕过问题。 |
| HTTP 点识别无反应 | 使用包含 `frontend/src/id.js` 的新网页镜像，清理旧页面缓存；HTTP 网页必须用 `web-eci` target。 |
| 登录失败或反复退出 | 核对实际来源与 `APP_ORIGIN` 一致；HTTP 使用不带 Secure 的 Cookie，HTTPS 使用 `COOKIE_SECURE=1`。 |
| 模型未配置/503 | 检查 `.env.model` 路径、变量名和自有模型账户额度，更新后重建 API 与网页；不要输出密钥。 |
| Agent 不可用或识别失败 | 检查模型 DNS、出网、服务地址、模型支持及运行日志中的错误类型；健康检查成功不代表模型调用成功。 |
| 教学网同步失败 | 核对账号、密码、校园网可达性和 Chromium；先用 HTTPS，不能把个人凭据发到公网 HTTP。 |
| 登录后原数据为空 | 核对项目名与原数据卷，先找回原卷；不要重新 seed、删库或删除旧卷。 |
| ACR 拉取 unauthorized | 核对专用密码、仓库权限、登录文件上传结果及公网/VPC 地址是否属于同一实例；使用完的临时凭据已删除时需重传。 |
| 镜像 exec format error | ECS x86_64 使用 `linux/amd64` 镜像，Apple Silicon 本机默认镜像不能直接复用。 |
| Caddy operation not permitted | HTTP IP 使用本仓库新版 `web-eci` target，不直接以旧镜像叠加 `cap_drop: ALL`。 |
| 首次 curl connection reset | 等待健康检查和网页启动，复查 `ps`；持续失败再看日志，不以一次启动瞬间的失败替代最终状态。 |

在部署目录执行 `docker compose -p niuma -f compose.ip.yaml logs --tail 80 api web` 查看有限日志；分享前检查是否包含私人内容、账号或网络地址。

云助手只显示“执行失败”时打开该命令的执行结果，展开实例输出或下载结果文件查看输出字段；命令超时与业务接口报错需要分别定位。

不要运行或公开 `set -x`、完整 `env`、不带 `-q` 的 Compose 展开结果、容器全部 inspect 或登录文件内容，这些输出可能包含密钥。

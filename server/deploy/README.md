```text
deploy/
├── README.md                 公网部署、持久化和验证命令。
├── Dockerfile                构建静态网页及含 Python、Node、Chromium 的业务镜像。
├── Dockerfile.dockerignore   排除密钥、个人数据库、运行数据和历史截图。
├── compose.yaml              启动单实例 API、HTTPS 网页和持久数据卷。
├── Caddyfile                 提供静态页面、自动 TLS 和同域 API 代理。
└── .env.example              公网地址模板，不含个人域名或凭据。
```

## 阿里云服务器

使用有公网 IP 的 Linux 实例，安装 Docker Engine 和 Compose 插件，参考 [Docker 官方安装](https://docs.docker.com/engine/install/ubuntu/)。
域名 A/AAAA 记录指向实际实例，安全组开放 TCP 80、443，SSH 仅允许管理员地址；不要开放 1332、1333、1334。
域名通过 Cloudflare 时首次申请证书可先使用仅 DNS 模式，启用代理后使用 Full (strict) 回源。
中国大陆服务器的公网域名服务须先确认 [阿里云备案要求](https://www.alibabacloud.com/help/en/account/aliyun-vs-alibaba-cloud)，未确定地域及域名时不启动公网发布。

在仓库根执行以下命令，编辑 `.env` 的域名和 HTTPS 地址，并在 `.env.model` 中填写团队模型配置。

```sh
cd server/deploy
cp .env.example .env
touch .env.model
chmod 600 .env .env.model
docker compose build --pull
docker compose up -d
docker compose ps
```

`.env.model` 仅提供运行时模型环境变量，密钥不得写入 Dockerfile、构建参数、前端或 Git；不设置 `HARNESS_URL`、`TEXT_HARNESS_URL` 时使用直调模型。
Debian 下载默认使用官方 HTTPS 源；在阿里云网络构建缓慢时可运行 `docker compose build --build-arg DEBIAN_MIRROR=https://mirrors.aliyun.com`，仍保留 Debian 签名验证。
网站默认不生成共享测试账号，用户自行注册；模型缺少配置时登录和持久化仍可用，识别与规划返回配置错误。
公开注册可触发付费模型调用，发布前配置模型供应商预算与额度告警，未确认额度不进行真实模型压测。
教学网采集从服务器网络访问校园系统，公网服务器不因此获得校园网权限，连接失败时需校方允许的网络环境。
不运行微信采集模块或个人微信客户端，微信材料验证仅使用合成输入。
镜像只运行一个 API 实例，进程内模型并发锁不能跨副本共享，不横向扩容本配置。

## 验证与数据

```sh
curl --fail https://demo.example.com/api/health
docker compose exec api python -c "from ddl_text.pipeline import process_request; from task_planning.planning_harness import generate; print('imports ok')"
docker compose logs --tail=50 api
```

浏览器验收注册、退出、登录、任务保存与重启恢复，识别、拆解和规划使用已配置模型；明确区分容器合成验证与校园真实采集。
账号、日程、导入材料及凭据加密密钥一起保存在 `app-data` 卷，TLS 证书保存在 `tls-data`，不得执行 `docker compose down -v`。
备份前停止 API，完整备份 `app-data` 卷到仅管理员可读的目录，再恢复 API；只复制 SQLite 而不保存 credential.key 会丢失来源凭据的解密能力。
升级前记录旧镜像 ID 并备份数据，重建后检查健康、登录与原日程；回滚必须同时核对数据库结构兼容性，不盲目覆盖用户数据。

## 本机容器验证

仅在隔离测试目录将 `.env` 设为 `SITE_ADDRESS=:80`、`APP_ORIGIN=http://localhost:18080`、`COOKIE_SECURE=0`、`HTTP_PORT=18080`、`HTTPS_PORT=18443`，模型文件可留空。
HTTP 测试配置不用于公网发布，既有 Vite 的 localhost:1331 启动方式不变。

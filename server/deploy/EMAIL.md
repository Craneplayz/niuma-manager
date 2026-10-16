# 阿里云邮箱验证码服务

应用已支持新账号验证码注册与老账号补验证，默认使用阿里云邮件推送 DirectMail 的 TLS SMTP。购买 ECS 不代表邮件推送已开通；未配置发信时已有账号仍可登录，新注册不能跳过验证。

## 服务器负责人准备

1. 在邮件推送控制台确认开通区域及实际账户配额、余额；测试和生产优先使用不同发信地址。
2. 配置专用发信子域名，按控制台要求验证 DNS（SPF、DKIM 等）；已有企业邮箱的 DNS 不应被覆盖。
3. 创建“触发邮件”发信地址，并为这个地址设置专用 SMTP 密码；该密码不是阿里云登录密码，也不是 AccessKey。
4. 确认服务器可出站连接邮件站点 TCP 465；项目不使用 ECS 常见受限的 25 端口。

收件用户可以使用普通可收信邮箱，不需要拥有阿里云账号。邮件是否入箱、垃圾邮件处理及发送额度以实际邮件服务和账户为准。

官方说明：[发信域名](https://help.aliyun.com/zh/direct-mail/user-guide/how-to-configure-sending-domain-names)、[发信地址](https://help.aliyun.com/zh/direct-mail/user-guide/setup-sender-addresses)、[SMTP 站点](https://help.aliyun.com/zh/direct-mail/smtp-endpoints)、[SMTP 发信](https://help.aliyun.com/zh/direct-mail/user-guide/send-emails-using-smtp)、[配额限制](https://help.aliyun.com/zh/direct-mail/product-overview/limits)。

## 按环境配置

在仓库根运行 `python server/manage_environment.py init all`，只补齐缺失的 `.env.mail`，不覆盖已有配置。分别编辑 `server/deploy/environments/<环境>/.env.mail`；模板为 [.env.mail.example](.env.mail.example)：

```dotenv
NIUMA_MAIL_HOST=smtpdm.aliyun.com
NIUMA_MAIL_PORT=465
NIUMA_MAIL_FROM=
NIUMA_MAIL_PASSWORD=
NIUMA_MAIL_TIMEOUT=10
```

`NIUMA_MAIL_FROM` 填控制台发信地址，也作为 SMTP 用户名；`NIUMA_MAIL_PASSWORD` 填其专用 SMTP 密码。域名所属区域决定 HOST，不按 ECS 所在区域猜测；当前支持杭州、新加坡、美国弗吉尼亚和德国法兰克福的官方 SMTP 站点，只允许 465/TLS、校验证书。

配置值按字面量读取，不展开 `$变量`、反斜杠或行内注释；包含空格时可用一对单双引号包裹，注释放独立行。配置须仅部署者可读，不提交 Git、不写入构建参数、前端变量或模型 `.env.model`。

`python server/manage_environment.py check test` 只输出 `mail_configured` 布尔状态，不发邮件，也不证明 DNS、凭据或额度已可用。本地统一入口注入本环境配置；容器统一入口生成私有 `.env.mail.compose` 并注入 API。原始 Compose 直启读取部署目录 `.env.mail`，可缺省但不能发信，需要 Docker Compose ≥2.24。配置更新后重新创建 API 容器；IP 方案须一并重建 web，按 [环境手册](ENVIRONMENTS.md) 操作。

## 升级与验收

升级前备份完整 `/data`。启动会补建验证状态、验证码挑战和限流表；已有 users、任务及会话不清空。已有账号显示“邮箱未验证”，原有功能可继续使用；老账号须先登录才能补验证，不能用验证码改密码或取得他人账号。

`email-verification.key` 在对应数据目录按需生成，用于挑战和限流 HMAC，须随整个数据卷备份；不要复制生产数据或此密钥到测试环境。替换/丢失密钥会使未完成的验证码失效，已完成的验证状态仍在数据库中。

在隔离 test 环境用负责人控制的邮箱依次验证：发送并实际收到验证码、错误码被拒绝、正确码注册后刷新仍登录、六十秒内不可重发；再以旧合成账号补验证，核对原任务和会话保留。缺配置/SMTP 错误应显示失败，不创建账号。把实际提交、收信结果和未通过项记入 Issue，不把配置、验证码或邮件原文贴入记录。

离线测试使用临时数据库与投递替身，不会真实发信；它不能代替上述阿里云入箱验收。前后端打包继续使用现有 Dockerfile 和同一版本镜像，生产必须按原验收流程发布。

Compose 中仅 Caddy 对外提供入口，并覆盖 `X-Niuma-Client-IP`，API 通过显式开关信任该私有头以进行客户端限流。自行直启 API 时默认使用连接地址，不应在 API 可被公网直接访问时开启 `NIUMA_TRUST_PROXY_CLIENT_IP`。

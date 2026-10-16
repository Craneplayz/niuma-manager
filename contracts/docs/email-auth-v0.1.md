# 邮箱注册与验证 v0.1

前端与业务 API 使用同源 JSON；邮箱验证用于证明收信权限，不连接用户的收件箱。本次不提供密码找回、换绑邮箱或未登录时接管已有账号。

独立请求与响应根见 [Schema](../schemas/email-auth-v0.1.schema.json)，共享合成数据见 [样例](../examples/email-auth-v01.json)；后端 HTTP 测试同时校验实际响应与该契约一致。请求允许忽略额外字段，响应不包含验证码、密码或供应商明细。

## 接口

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /api/auth/email-code` | `{ "purpose": "register", "email": "student@example.com" }` | `{ "challengeId": "不可预测句柄", "retryAfter": 60, "expiresIn": 600 }` |
| `POST /api/auth/register` | 原 `name/email/password` 加 `challengeId/code`，code 为六位数字字符串 | 原登录 Cookie 与 `{ "user": { "id": "…", "name": "…", "email": "…", "emailVerified": true } }` |
| `POST /api/auth/email-code` | 已登录：`{ "purpose": "verify_email" }` | 同上；收件邮箱取当前登录账号，忽略客户端传入邮箱 |
| `POST /api/auth/verify-email` | 已登录：`{ "challengeId": "…", "code": "012345" }` | `{ "user": { "id": "…", "name": "…", "email": "…", "emailVerified": true } }` |

登录、管理员登录和 `/api/auth/me` 的 `user` 增加 `emailVerified:boolean`。历史账号默认 false，保留登录、会话和数据权限；验证成功不重置工作区或授予管理员角色。客户端尚未取得该字段时按未验证展示。

注册邮箱去除首尾空白并小写，域名转 IDNA；同一地址规范化后唯一。发码前不占用账号名；实际创建账号、消费验证码及标记验证在同一事务中提交。补验证的验证码同时绑定当前用户 ID、邮箱及用途，注册验证码不能用于补验证。

## 错误与限流

邮件相关错误格式为 `{ "error": "面向用户的提示", "code": "email_code_invalid", "retryAfter": 60 }`；retryAfter 仅在 429 中提供，同时设置 HTTP `Retry-After`。

| HTTP | code | 含义 |
| --- | --- | --- |
| 400 | `email_invalid` | 邮箱格式不正确 |
| 400 | `email_code_invalid` | 验证码缺失、错误、已使用、达到单码尝试上限或用途/账号不符 |
| 400 | `email_code_expired` | 验证码已过期 |
| 401 | 原鉴权错误 | 补验证未登录或会话失效 |
| 409 | `email_registered` | 发注册验证码时邮箱已存在，应先登录 |
| 409 | `email_already_verified` | 当前账号已验证 |
| 429 | `email_code_rate_limited` | 发码或尝试过于频繁 |
| 503 | `email_not_configured` / `email_send_failed` | 发信尚未配置或暂时失败，不返回供应商原始响应 |

原注册校验仍可返回不含 code 的 400/409 错误。验证码十分钟有效、最多五次错误后失效，重发使同用途旧验证码失效；发送失败也保留限流占额，但无法消费其验证码。

默认发信预算：每邮箱每分钟一次、每小时五次、每天十次；每客户端 IP 每小时二十次、每天五十次；每环境每小时一百次、每天五百次。错误验证还有跨重发的每小时邮箱二十次、IP 一百次、环境一千次预算，计数持久化，重启不清零。限流并不提高阿里云账户额度；共享发信账号的多环境还须由负责人统一控制预算。

验证码仅通过邮件投递；HTTP、日志和数据库均不保存明文验证码。挑战保存带环境密钥的 HMAC，IP 限流身份也以 HMAC 保存；过期挑战与限流记录在后续请求中按一天清理。

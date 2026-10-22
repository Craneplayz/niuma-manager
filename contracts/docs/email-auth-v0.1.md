# 邮箱注册、验证与密码找回 v0.1

前端与业务 API 使用同源 JSON；邮箱验证用于证明收信权限，不连接用户的收件箱。已验证邮箱可以找回密码，未验证的历史账号须先登录补验证；本次不提供换绑邮箱。

独立请求与响应根见 [Schema](../schemas/email-auth-v0.1.schema.json)，共享合成数据见 [样例](../examples/email-auth-v01.json)；后端 HTTP 测试同时校验实际响应与该契约一致。请求允许忽略额外字段，响应不包含验证码、密码或供应商明细。

## 接口

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /api/auth/email-code` | `{ "purpose": "register", "email": "student@example.com" }` | `{ "challengeId": "不可预测句柄", "retryAfter": 60, "expiresIn": 600 }` |
| `POST /api/auth/register` | 原 `name/email/password` 加 `challengeId/code`，code 为六位数字字符串 | 原登录 Cookie 与 `{ "user": { "id": "…", "name": "…", "email": "…", "emailVerified": true } }` |
| `POST /api/auth/email-code` | 已登录：`{ "purpose": "verify_email" }` | 同上；收件邮箱取当前登录账号，忽略客户端传入邮箱 |
| `POST /api/auth/verify-email` | 已登录：`{ "challengeId": "…", "code": "012345" }` | `{ "user": { "id": "…", "name": "…", "email": "…", "emailVerified": true } }` |
| `POST /api/auth/email-code` | 匿名找回：`{ "purpose": "reset_password", "email": "student@example.com" }` | 同一 `challengeResponse`；不说明账号存在性或找回资格 |
| `POST /api/auth/reset-password` | `{ "email": "student@example.com", "challengeId": "…", "code": "012345", "newPassword": "新的8–128字符密码" }` | `{ "reset": true }`，不签发会话，用户重新登录 |

登录、管理员登录和 `/api/auth/me` 的 `user` 增加 `emailVerified:boolean`。历史账号默认 false，保留登录、会话和数据权限；验证成功不重置工作区或授予管理员角色。客户端尚未取得该字段时按未验证展示。

注册邮箱去除首尾空白并小写，域名转 IDNA；同一地址规范化后唯一。发码前不占用账号名；实际创建账号、消费验证码及标记验证在同一事务中提交。补验证的验证码同时绑定当前用户 ID、邮箱及用途，注册验证码不能用于补验证。

密码找回仅向已验证账号投递，未知或未验证邮箱也占用相同发码预算并取得不可消费的随机句柄。供应商拒绝的找回请求仍返回相同受理响应，保留 failed 挑战状态；未配置邮件服务时统一返回 503。前端显示条件性发码提示，不将受理视为实际入箱；同步 SMTP 与不投递路径的网络耗时可能不同。

恢复验证码另绑定发码时账号密码版本，正常改密也使旧恢复码失效；注册、补验证与恢复码不能互换。恢复成功在单一事务中消费验证码、更新密码、撤销该账号全部普通/管理员会话并作废其他 pending/active 验证码，保留账号 ID、权限与工作区，不自动登录。登录和已登录改密在写入前重新核对密码快照或会话，防止跨越恢复操作的旧请求重新建立访问。

## 错误与限流

邮件相关错误格式为 `{ "error": "面向用户的提示", "code": "email_code_invalid", "retryAfter": 60 }`；retryAfter 仅在 429 中提供，同时设置 HTTP `Retry-After`。

| HTTP | code | 含义 |
| --- | --- | --- |
| 400 | `email_invalid` | 邮箱格式不正确 |
| 400 | `email_code_invalid` | 验证码缺失、错误、已使用、达到单码尝试上限或用途/账号不符 |
| 400 | `email_code_expired` | 注册/补验证的验证码已过期；找回的错误、过期或失效码统一 `email_code_invalid` |
| 401 | 原鉴权错误 | 补验证未登录或会话失效 |
| 409 | `email_registered` | 发注册验证码时邮箱已存在，应先登录 |
| 409 | `email_already_verified` | 当前账号已验证 |
| 429 | `email_code_rate_limited` | 发码或尝试过于频繁 |
| 503 | `email_not_configured` / `email_send_failed` | 发信尚未配置或注册/补验证投递暂时失败，不返回供应商原始响应 |

原注册校验仍可返回不含 code 的 400/409 错误。验证码十分钟有效、最多五次错误后失效，重发使同用途旧验证码失效；发送失败也保留限流占额，但无法消费其验证码。

找回中的真实句柄与不可消费句柄使用相同的错误格式和错误尝试预算，不通过过期或错误次数响应透露资格；不属于该邮箱/用途的随机句柄只计 IP/全局尝试预算，不能任意消耗别人的邮箱预算。

默认发信预算：每邮箱每分钟一次、每小时五次、每天十次；每客户端 IP 每小时二十次、每天五十次；每环境每小时一百次、每天五百次。错误验证还有跨重发的每小时邮箱二十次、IP 一百次、环境一千次预算，计数持久化，重启不清零。限流并不提高阿里云账户额度；共享发信账号的多环境还须由负责人统一控制预算。

验证码仅通过邮件投递；HTTP、日志和数据库均不保存明文验证码。挑战保存带环境密钥的 HMAC，IP 限流身份也以 HMAC 保存；过期挑战与限流记录在后续请求中按一天清理。

公开用户响应可额外携带 `avatar`（当前账号的 PNG data URL 或 `null`），头像上传和恢复默认见 [账号头像](account-avatar.md)；此字段不影响邮箱验证状态或权限。

# 牛马管理器 Android 适配评估

> 文件名按需求保留为 `Andrioid_APP.md`（`Android` 的拼写应为 Android）。本文基于分支 `XiaoHan666/feature/95-wechat-update` 的当前代码（以本地 `HEAD` 为准）判断，不把尚未合入该分支的功能当作现状。

## 结论先行

当前项目可以较低成本做成 Android 可安装应用，但不建议把现有 Python 后端、Playwright 爬虫和 Windows 微信数据库读取器一起“打包进 APK”。更合适的边界是：

```text
Android（PWA 或 Capacitor APK）
        │ HTTPS / 同一局域网 HTTP（仅开发）
        ▼
业务 API（Python + SQLite，先运行在 Windows；正式环境再部署服务器）
        ├── 文本识别 / 任务规划模型 API
        ├── 教学网爬虫（Node.js + Playwright）
        └── Windows 微信采集器（读取 Windows 微信本地数据库）
```

- **最小改动**：先做响应式网页/PWA。它已经有 `viewport`、移动端底部导航和多个 `@media` 断点，不需要重写前端。
- **确实需要 APK**：用 Capacitor 把 Vite 的 `dist/` 包成 Android 外壳。APK 只负责界面和手机能力，后端仍是独立服务。
- **正式多人使用**：前端和 API 放在同一 HTTPS 域名下，后端部署到云服务器或学校/公司服务器；Docker 是可选的部署方式，不是 Android 运行的必需品。
- **微信同步的现实边界**：当前实时微信读取依赖 Windows 10/11、已登录微信 4.x 和 `wechatauto-replica`，Android 不能直接读取 Windows 微信数据库。因此手机端应先支持“选择导出的 JSON/JSONL 文件并提交文字”，实时采集继续由 Windows 伴随程序完成。

## 1. Android 安装方式

### 方案比较

| 方案 | 安装/分发 | 对现有代码改动 | 适合本项目的场景 |
| --- | --- | --- | --- |
| **PWA** | HTTPS 网页中“添加到主屏幕”，没有 APK | 最少：补 `manifest.webmanifest`、图标和 Service Worker | 先验证手机端交互；不要求应用商店 |
| **Capacitor APK（推荐）** | 生成签名 APK，可侧载；上架 Google Play 时生成 AAB | 中等：增加 Android 工程、生产 API 地址和文件/权限适配 | 需要像普通 App 一样安装，但主体仍复用现有前端 |
| **Trusted Web Activity** | APK 只是已上线 PWA 的轻量容器 | 需要 HTTPS、域名和 Digital Asset Links | 已有稳定公网 PWA，原生能力需求很少 |
| Kotlin/Compose、Flutter、React Native 重写 | 原生 APK/AAB | 很大：重写页面、状态管理、接口和测试 | 只有在需要大量原生能力、长期脱离 Web 时才考虑 |

### APK 是否足够

APK 可以满足“下载并安装”的需求，但发布版本必须使用签名密钥签名，并区分 debug APK 与 release APK。若将来通过 Google Play 分发，应以 AAB 为主；国内自托管、内测或直接下载可以提供签名 APK。APK 大小不是当前主要风险，真正需要解决的是 API 地址、账号会话、HTTPS 和 Windows 数据采集边界。

建议顺序：

1. 先用手机浏览器访问生产或局域网地址，完成 PWA/响应式验收。
2. 若产品必须有 APK，再用 Capacitor 包装同一份 `frontend/dist`，不要先重写业务页面。
3. 只有在相机、推送、系统分享、后台任务等需求稳定后，才增加对应的 Capacitor 原生插件。

## 2. 当前项目现状与手机端差异

### 前端现状

- `frontend/` 是 Vite + 原生 JavaScript 单页应用，不是 Android 原生工程。
- `frontend/index.html` 已有移动端 `viewport`；`styles.css`、`polish.css` 已为小屏提供底部导航、布局折叠、触摸尺寸和安全区处理。
- `frontend/src/team-store.js` 使用 `fetch('/api/...', { credentials: 'same-origin' })`，浏览器端数据主要由服务端工作区保存，HttpOnly Cookie 保存会话。
- `frontend/vite.config.ts` 的 `/api` 代理只服务于开发机：前端 `1331` 转发到本机后端 `127.0.0.1:1332`。打包后的 APK 没有这个 Vite 代理。
- 当前“微信导入”是选择本地 JSON/JSONL，在浏览器内预览并由用户勾选文字消息后生成 v0.2 请求；页面明确不做微信实时同步，也不直接上传 Windows 微信数据库。
- 语音输入依赖浏览器 `SpeechRecognition`，或登录后通过 `MediaRecorder` 调用后端语音接口；Android Chrome/WebView 的语音实现和权限需要真机测试，不能只依赖桌面浏览器测试。

因此，页面**不需要换成另一套前端技术**才能适配手机；需要的是生产化 API 访问、原生容器边界和手机交互回归。

### 前端需要的改动

#### A. 交付为 PWA 或 Capacitor

- 增加 `manifest.webmanifest`、Android 图标、启动背景色和 Service Worker。只缓存静态资源，不缓存账号接口、任务数据、Cookie 或模型结果；离线时应明确显示“未连接”，不能展示过期任务。
- Capacitor 构建时将 `webDir` 指向 Vite 的 `dist/`。增加 Android 工程、应用 ID、签名配置和构建脚本；不要把模型密钥放入 APK。
- 为生产构建增加 API 地址配置。优先把前端静态文件和 `/api` 通过同一 HTTPS 域名反向代理，继续使用相对路径；如果必须跨域，才增加 `VITE_API_BASE_URL` 并让请求使用 `credentials: 'include'`。

#### B. 手机交互和能力

- 在 360、390、412 dp 宽度及横屏下检查日历、任务表单、规划预览、对话框、键盘弹出后的滚动和底部导航；现有 CSS 是基础，不等于已经完成真机验收。
- 文件选择沿用 `<input type="file">` 可覆盖 v0.2 JSON/JSONL；若要手机直接上传 v0.3.1 附件，需要增加文件类型/大小限制、SHA-256、上传进度、断点或有限重试，并使用 Android 文件选择器测试 `content://` URI 场景。
- 语音输入增加权限拒绝、WebView 不支持和网络超时的降级提示；录音文件不应在客户端保存模型凭据。
- 如需要“分享至牛马管理器”、相机、通知、后台同步，再分别引入 Capacitor Share/Camera/Local Notifications 等插件，并为每项能力补 Android 权限说明和隐私告知。
- 继续保持服务端保存账号数据。不要把 SQLite、教学网密码、`credential.key` 或模型 API Key 打进 APK；客户端只能拿到会话 Cookie/短期令牌。

#### C. 跨域、Cookie 和安全

当前 `server/app.py` 只接受 `http://localhost:1331` 和 `http://127.0.0.1:1331` 的写请求来源，并设置 `SameSite=Strict` 的 HttpOnly Cookie。这对本机 Vite 开发有效，但对 APK 的 `capacitor://localhost`、生产域名或局域网 IP 不够。

优先方案是“前端与 API 同源”，避免把 Cookie 变成跨站 Cookie。若必须分离域名，需要：

- 服务端配置化的精确 Origin 白名单，不能使用 `*`；
- CORS 允许凭据，前端请求使用 `credentials: 'include'`；
- 全程 HTTPS，并按实际跨站部署调整 `SameSite=None; Secure`；
- 对所有写接口增加 CSRF 防护或短期 Bearer Token，限制登录、导入和附件上传的大小与频率。

## 3. 后端和数据采集需要的改动

### 当前后端边界

`server/app.py` 是 Python 标准库 `ThreadingHTTPServer`，默认监听 `127.0.0.1:1332`，使用本机 SQLite；识别和规划默认在同一进程直调 `ddl_text` 与 `task_planning`。`server/source_sync.py` 调用 Node/Playwright 教学网爬虫。模型密钥来自服务端环境或根目录 `.env`，不应进入前端。

微信部分更特殊：

- `wechat/script/adapters/windows.py` 延迟加载 Windows 专用的 `wechatauto`，读取已登录微信的本地数据库和媒体。
- `wechat/script/v031.py` 可以生成 v0.3.1 请求、上传附件并处理队列/回执，但这是客户端脚本工具链。
- 前端当前仅实现会话文件预览和 v0.2 文字识别入口；`server/source_sync.py` 当前同步入口也明确只支持 `teaching_web`，微信不是现成的 Android 实时同步 API。

### 后端必须完成的改动

1. **监听和配置化**

   增加 `NIUMA_HOST`、`NIUMA_PORT`、可信 Origin、公开 API 根地址等配置。开发默认仍绑定 `127.0.0.1`；局域网手机联调可临时绑定 `0.0.0.0`，但必须配合 Windows 防火墙规则、登录认证和 HTTPS/隧道，不能裸露到公网。

2. **生产 HTTP 入口**

   同源部署可由 Nginx/Caddy/云负载均衡提供 HTTPS，并把 `/api` 代理到业务进程。现有标准库服务器适合课程项目和单机联调；多人公网服务应改用经过配置的生产 WSGI/ASGI 入口，增加访问日志、限流、健康检查和优雅退出。

3. **数据库和密钥持久化**

   SQLite 可以支持单机或小规模使用，但要把数据库目录和 `credential.key` 放在持久化磁盘，做备份与恢复演练。云端多实例部署不能让每个容器各自拥有一份 SQLite；应使用单实例持久卷，或迁移到 PostgreSQL 等服务数据库，并为会话、附件和模型请求做容量/保留策略。

4. **微信同步产品化（按需求选择）**

   - **短期推荐**：Windows 继续运行采集脚本，输出脱敏 JSON/JSONL；Android 通过文件选择、系统分享或 HTTPS 上传到后端，后端按现有契约识别。附件上传要单独走受控接口，不能让后端访问 Windows 的本地路径。
   - **局域网伴随程序**：Windows 采集器在用户电脑运行，只向后端上传消息和受控附件；手机只访问后端。需要设备配对、短期导入凭据、幂等 `request_id`、上传进度和失败重试。
   - **真正云同步**：必须把微信采集放在用户授权的 Windows 代理上，云端只接受代理主动上传；不要尝试在 Android 或云服务器中读取 Windows 微信数据库。

5. **长任务和状态查询**

   教学网同步可能等待数分钟，v0.3.1 附件导入也可能是长任务。手机端不应依赖一个一直不关闭的 HTTP 请求；建议返回 job ID，由客户端轮询状态或使用 SSE/WebSocket。当前前端已有同步进度展示，但后端 API 仍需把长任务生命周期和取消/重试语义固定下来。

## 4. Windows 开发时是否必须使用云服务器或 Docker

### 本地开发：不需要

继续在 Windows 上开发和运行时，不需要云服务器，也不需要 Docker：

```text
终端 1（frontend）：npm ci && npm run dev:api
终端 2（frontend）：npm run dev
浏览器：访问 http://localhost:1331
```

按仓库现状，默认后端会直接调用识别/规划模块；只有显式配置 `TEXT_HARNESS_URL` 或 `HARNESS_URL` 才需要额外的独立服务。教学网同步还需要 `crawler/` 的 pnpm 依赖和 Playwright 浏览器；微信实时导出需要 `wechat-export` conda 环境和已登录的 Windows 微信。这些依赖在 Windows 本机运行最直接。

### 手机和 Windows 同时使用：也不一定需要云端

若手机和电脑在同一局域网，可以让 Windows 电脑作为开发/家庭服务器：

1. 前端静态文件由 Vite preview 或一个本地 HTTPS 反向代理提供。
2. 后端监听电脑局域网 IP，手机访问 `http(s)://电脑IP:端口`。
3. 配置可信 Origin、Windows 防火墙、账号认证和持久化数据库。

这种方式适合演示和个人使用；电脑关机、换网或离开局域网时手机就无法访问。`localhost` 在手机上指的是手机本身，不是开发电脑，APK 不能把 `127.0.0.1:1332` 当成 Windows 后端地址。

### 何时需要云服务器

需要以下任一能力时，才建议部署云端：手机在任意网络可访问、多人共享、Windows 电脑不必常开、定时同步、统一备份、推送通知或稳定 HTTPS。云端至少需要：

- 一个 HTTPS 入口和域名；
- 业务 API 运行环境；
- 持久化数据库/文件存储和备份；
- 服务端模型密钥、日志脱敏、限流和监控；
- 若保留教学网/微信自动采集，一个常在线且获得授权的 Windows 采集代理。

### Docker 是否必要

当前仓库没有 `Dockerfile` 或 `docker-compose.yml`，所以 Docker 不是现成的运行前提。它的价值在于把 Python API、Node 依赖和反向代理的部署环境固定下来；它不能解决 Android 无法读取 Windows 微信数据库的问题，也不能替代持久化磁盘、HTTPS、备份和密钥管理。

建议：

- 课程开发/单机演示：直接使用现有 Windows 命令，不引入 Docker。
- 云端单实例：可用 Docker Compose 部署 API + 反向代理，并挂载 SQLite/附件/密钥持久卷。
- 多实例生产：容器化 API，但数据库和对象存储使用独立持久化服务；不要把 SQLite 放在临时容器层。

## 5. 推荐实施顺序

### 阶段 1：手机网页验收

- 用现有 `frontend` 构建静态文件，验证登录、对话识别、任务确认、日历、规划预览和文件导入。
- 在 Android Chrome 真机验证语音、文件选择、键盘、网络切换和 Cookie 会话。
- 修正小屏溢出、长标题、日期输入、对话框和加载超时。

### 阶段 2：PWA 或 Capacitor APK

- 若只需“手机桌面图标”：增加 PWA manifest、图标和受控缓存。
- 若必须安装 APK：引入 Capacitor，固定 `dist` 构建和签名流程；先只使用 Web 能力，不复制后端。
- 把 API 地址、可信 Origin、HTTPS 和错误重试做成环境配置，并补 Android 真机测试。

### 阶段 3：数据同步和正式部署

- 先实现 Windows 伴随采集器到后端的安全上传，再决定是否支持 v0.3.1 附件在手机端直接上传。
- 为导入 job、附件存储、幂等、失败重试和用户撤销定义 API；不要直接暴露 Windows 文件路径或微信数据库。
- 需要公网访问时再部署 HTTPS + 持久化数据库/文件存储；Docker 作为部署封装选项，而不是 App 运行条件。

## 6. 验收清单

- [ ] Android Chrome 访问时不把 `localhost`/`127.0.0.1` 错当成 Windows 后端。
- [ ] 生产构建不依赖 `vite.config.ts` 的开发代理。
- [ ] API 只允许配置的 Origin，Cookie/Token 不被跨站滥用。
- [ ] APK/PWA 内没有模型密钥、教学网密码、SQLite 或 `credential.key`。
- [ ] 文字导入、微信 JSON/JSONL 预览、语音、附件大小限制在真机可用。
- [ ] Windows 微信采集继续在 Windows 代理执行，Android 只处理授权后的导入数据。
- [ ] SQLite、附件和 `credential.key` 有持久化位置和备份策略。
- [ ] 教学网同步、识别、规划、附件上传的长任务有超时、状态、失败和重试反馈。
- [ ] release APK 使用受保护的签名密钥；调试包与发布包分开。

## 参考代码位置

- 前端启动与代理：[frontend/README.md](frontend/README.md)、[frontend/vite.config.ts](frontend/vite.config.ts)
- 前端入口与移动 viewport：[frontend/index.html](frontend/index.html)
- 移动布局：[frontend/src/styles.css](frontend/src/styles.css)、[frontend/src/polish.css](frontend/src/polish.css)
- API 会话与工作区：[frontend/src/team-store.js](frontend/src/team-store.js)、[server/app.py](server/app.py)
- 教学网同步：[server/source_sync.py](server/source_sync.py)、[crawler/README.md](crawler/README.md)
- 微信 Windows 读取和跨平台适配：[wechat/README.md](wechat/README.md)、[wechat/script/adapters/windows.py](wechat/script/adapters/windows.py)
- 微信 v0.3.1 请求/附件工具：[wechat/script/v031.py](wechat/script/v031.py)、[contracts/docs/text-attachments-v0.3.1.md](contracts/docs/text-attachments-v0.3.1.md)

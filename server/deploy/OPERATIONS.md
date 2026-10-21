# 访问与同步排队排查

## 访问入口

Cloudflare Tunnel 方案使用 `compose.ip.yaml` 的 HTTP 网页端口作为回源，HTTPS 证书与 HTTP 跳转由 Cloudflare 边缘处理；源站不要求签发 Caddy 证书。

源站直接 HTTPS 方案使用 `compose.yaml` 与 `SITE_ADDRESS`，开放80/443供 Caddy 签发证书；不使用 Tunnel 时按 [部署说明](README.md) 配置 DNS、证书及跳转，不能照搬 Tunnel 的 HTTP 回源参数。

域名正式入口使用 HTTPS；Cloudflare Tunnel 部署在区域的 SSL/TLS → Edge Certificates 启用 Always Use HTTPS，并为主域名及 www 分别配置现有 Tunnel 的公开主机名，源服务指向本机 HTTP 网页端口。

邮件 MX、SPF、DKIM 与 DMARC 记录不随网站入口调整；启用跳转前后核对它们保持不变。

`NIUMA_ALLOWED_ORIGINS` 包含每个实际浏览器入口的完整协议、主机及端口，空格分隔且不带结尾斜杠；未知来源继续拒绝，不设置通配符。

原始 Compose 的 `.env` 使用 `APP_ORIGIN` 映射上述运行变量；三环境入口使用 `config.json` 的单个 `origin` 生成它，原生启动才直接设置 `NIUMA_ALLOWED_ORIGINS`。

多入口部署不能把空格分隔列表填入仅接受单个 URL 的 `config.json.origin`；在既有 Compose 命令最后追加私有覆盖文件，例如：

```yaml
services:
  api:
    environment:
      NIUMA_ALLOWED_ORIGINS: "http://<IP>:1331 https://<主域名> https://www.<主域名>"
```

保留原命令的所有 `--env-file`、项目名、基础配置与邮件预算文件，仅最后追加 `-f /<私有路径>/compose.origins.yaml`；按容器实际标签重新创建 API/web，检查合成账号在各入口的读写，不把此覆盖文件或真实主机配置提交 Git。

需要保留 HTTP IP 演示时将其作为单独来源配置，只用于测试账号和合成材料；不能将域名重定向规则套到弹性 IP。

更新已有部署前读取实际容器的 `com.docker.compose.project.config_files` 与 `com.docker.compose.project.working_dir` 标签，保留多入口覆盖文件；不要用旧单来源命令覆盖运行配置。

## 逐入口验收

1. 对主域名及 www 分别执行 `curl -sS -D - -o /dev/null 'http://<域名>/?page=import&importSource=teaching'`，确认301/302/307/308的 Location 为同域名HTTPS且路径和查询参数完整。
2. 执行 `curl -fLsS 'http://<域名>/api/health'`，确认最终HTTPS返回 `{"status":"ok"}` 且没有循环跳转；健康检查使用GET，不以HEAD的501判断服务不可用。
3. 执行 `curl -fsS 'https://<域名>/api/version'`，记录实际环境与部署版本，并核对前后端镜像；仅更新服务器代码目录不算发布。
4. 普通及独立无痕窗口分别登录合成账号、刷新读取原任务、保存一项设置、退出后确认受保护接口拒绝，再重新登录；各入口的Cookie独立，换域名需要重新登录。
5. 如保留IP，单独验证其HTTP登录、工作区读取和写入，并确认域名HTTP仍强制跳转HTTPS。
6. 验证未知Origin的写入被拒绝、普通账号不能读取管理员接口、微信采集与采集器上传保持关闭。

登录报错先检查浏览器实际协议与请求状态，再核对Tunnel路由、允许来源和Cookie；不要通过清库、重新seed或关闭Origin校验解决入口问题。

## 多人导入

教学网同步最多4轮执行、额外20轮FIFO排队、每账号最多一轮；超过容量返回503，排队超过180秒终结为可重试失败，已保存任务不删除。

同步准入使用独立 `source_jobs.QUEUE_SECONDS=180`，限制未开始工作前的等待，不是采集器300秒执行超时；四轮长采集均占满超过180秒时，新轮次以 `queue_timeout` 结束，用户可重试，不保证所有长任务等待者都获准执行。

排队满为 `queue_full`，等待超时为 `queue_timeout`，均未启动采集且不参与“后续采集成功恢复旧连接故障”的历史规则；用户已停止保持 stopped，不提示重新排队。

排队中的停止或凭据失效会移除等待票据，不等待执行槽释放，也不启动采集器；定时同步与手动同步使用同一容量限制。

其他账号已经等待时，大批量轮次在当前已派发记录完成后让出运行槽并回到FIFO队尾，不重复采集；无竞争时持续派发，公平性不以增加模型并发实现。

排查时先区分同步轮次排队与模型总池排队：每轮记录最多4并发，全部业务模型共用20并发、100等待的模型池，调大模型上限不能消除同步入口容量拒绝。

安装测试依赖后运行 `python server/benchmark_source_capacity.py`，在独立临时数据库比较1/3/5账号的完成数、提交/工作区P95、吞吐及合成阶段耗时；输出不包含账号、来源正文或密钥。

默认基准的外部采集固定500ms、模型固定250ms，真实校验和SQLite保存仍执行；没有附件、浏览器进程或外部供应商调用，不能推断真实采集耗时、模型配额、附件容量或生产最大用户数；显式写锁等待仅代表本轮临时库实测。

输出另含实际已观测阶段 P50/P95（包括显式写锁等待）、父进程 CPU 秒数、进程生命周期 RSS 峰值及所在磁盘可用量；CPU 不包含一次性模型子进程，RSS 不是单次导入增量，不能将它们当整机资源。

加 `--attachments` 实际从回环 HTTP 服务下载1438字节合成 DOCX，经原件绑定、真实本地读取、校验及保存；同一文本的 ZIP 元数据固定，1/3/5及后续对比使用相同字节摘要。这是合成来源适配器的实际HTTP下载，不是教学网登录或爬虫适配器性能；未覆盖大附件、PDF渲染或视觉模型。

每约500ms采样主机 CPU、内存和磁盘，输出峰值及采样数；主机指标含其他进程，峰值不等于本次独占资源。磁盘为用量不是I/O吞吐；云端用 `TMPDIR` 指向空的实体磁盘测试目录，不用 tmpfs 保存基准数据库，结束只删除该临时目录。

显式运行 `python server/benchmark_source_capacity.py --real-model --accounts 1 3 5` 才使用当前环境供应商与合成文本，会消耗 API 额度；采集仍为替身，附件仅在另加 `--attachments` 时处理，不称为真实教学网采集，实际调用与用量从生产方观测输出，脚本不增加模型重试。

测试与生产数据卷必须独立；禁止用用户的生产材料做并发压测，发布与回滚沿用 [环境指南](ENVIRONMENTS.md)，保留完整数据备份及既有多来源覆盖配置。

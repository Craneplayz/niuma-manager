# 主机资源探针与基础故障告警（#189）

`operations_probe.py` 是主机每分钟启动一次的短进程；API 不导入探针、不挂 Docker socket，探针只通过带 Compose 项目及 api/web 服务标签的 Docker CLI 查询 RestartCount 和运行状态。

容器还须具备 Compose 的 `config-hash` 且 `oneoff=False`，排除继承镜像标签的临时验证容器和一次性作业，避免它们退出后误报服务故障。

## 接口与隔离

API 必须先调用 `operations.initialize(business_database_path)`；探针使用同一 `operations.database(path)`、`connection(path)`，不初始化或迁移数据库，不创建缺失的业务库或 sidecar。

业务库父目录已有的 `.niuma-environment.json` 必须与私有配置的 environment 一致；test/production 使用独立数据目录、sidecar、Compose 项目、健康端口、配置及锁文件。配置中的 database 是主机能访问的实际业务数据库绝对路径（可为现有数据卷的主机路径），不是 API 容器内的 `/data/...` 路径。

资源写入 `resources(at,value)`，JSON 白名单为 `cpuPercent,memoryUsedBytes,memoryTotalBytes,diskUsedBytes,diskTotalBytes,restarts,available,serviceAvailable`，缺数值为 null；available 表示 CPU/内存/磁盘至少一项采样成功，不受 HTTP 健康影响，避免服务故障时隐藏 CPU 界面。restarts 是 api/web 所有容器 RestartCount 的整数总和，任一服务未知或 Docker 无法读取时为 null。容器重建后计数可归零，不把它当累计故障数。

Linux CPU 从 `/proc/stat` 两次间隔 100ms 的差值计算，排除重复 guest 计数；内存从 MemTotal−MemAvailable 计算。磁盘 used=total−调用者可用 free（包含保留空间），以实际可用空间判断告警。macOS 等无 /proc 的主机 CPU/内存为 null，磁盘仍采样。

HTTP GET 只检查 `/api/health` 返回 200，不读正文、不跟随重定向、不使用环境代理。HTTP 失败或任一指定服务无容器/未运行时 serviceAvailable=false；HTTP 成功且两个服务均运行时 true；Docker 无法读取且 HTTP 成功时 null。每个 Docker 命令与 HTTP 各有 5 秒超时。

业务库以 SQLite `mode=ro` 打开，只查询 queued 时间戳；同步/重识别取 snapshot 内 `COALESCE(updated_at,created_at)`，公共来源取 created_at，主动上传材料取作业编号的散列与 sidecar 最近 queued 观测时间。Python 不取得完整 snapshot，不读取用户、凭据、会话或正文；未关联 manual_material_jobs 的微信作业不查询。失败聚合仅读 sidecar 的 run 阶段 code/quantity/at。只保存聚合告警状态，不保存业务编号或时间戳。字符串时间戳必须包含时区，sidecar 观测用有限 Unix 秒；数据库不可读、JSON/时间戳损坏时对应判断为未知，不伪造恢复。

## 告警语义

| code | 触发条件 |
| --- | --- |
| service_unavailable | serviceAvailable=false 连续至少 120 秒 |
| import_failures | 过去 600 秒已观测 stage=run、operation 为 sync/materials/reprocessing/public 的终态事件 quantity 合计至少 3，且全部 code=failed；立即触发（sync.record 不计入） |
| queue_backlog | 同步、重识别、公共来源及主动上传材料任一 queued 作业等待严格超过30秒，并持续至少60秒；同步/重识别的 updated_at 缺失或null时回退 created_at，中途让出运行槽后重新排队从本次保存时间计时 |
| disk_low | 可用磁盘严格少于总容量 10% 或严格少于 1 GiB，持续至少 120 秒 |

持续失败仅计入 succeeded/partial_success/failed/stopped/interrupted 五种终态；部分成功、主动停止或中断会阻止“全部失败”告警，queued/running 不计入分子或分母。四类导入操作在当前环境内合并判断，仅统计已启用观测后落库的 run 事件，不把启用前未观测的历史当成成功或失败。未观测材料排队时间不猜测等待年龄：其他已知作业积压时仍触发；否则保持未知而不发送恢复。模型进程内队列由管理员页实时展示，不从宿主机猜测进程内等待票据。

积压阈值为180秒准入上限预留处理时间：每分钟采样时，连续等待约90–150秒可触发，而不是等到作业已超时后才告警；短时排队及中途未知采样不会满足持续条件。

每个环境各 code 一条状态，成功通知后至少 1800 秒才重复，条件消失发送一次 recovered；发送失败持久化 delivery_error=1，下分钟重试（每次仍须重新预留共享邮件额度）。失败不更新 last_notified，返回非零退出码并输出固定 `notice_delivery_pending`。恢复投递失败同样重试；告警与恢复均只含环境名、固定 code、状态，不含资源值、地址、异常详情或业务内容。

未知指标不触发新告警或恢复；pending 阶段未知值会重置连续计时，采样间隔超过 180 秒也重置 pending；已激活告警继续保留并重试待投递通知。已知恢复后若发送失败，条件再次异常则撤销待发恢复，继续原事件。未成功投递的触发在恢复时转为 recovered，保留原 first_seen，避免向收件人发送已经过时的故障状态。

不需新增数据库字段：复用 alerts 的 state（pending/firing/active/recovering/resolved）、first_seen（本轮首次异常）、last_seen（最后已知判断）、active_since（本轮激活时间，恢复成功后清空）、last_notified（最后成功投递时间）、delivery_error。展示层须识别这些状态；resolved 是历史记录而非当前故障，pending 尚未满足持续时间。资源保留 14 天，与 operations 的裁剪规则一致。

状态先提交、投递后确认，提供至少一次投递；SMTP 已接受但进程中断/响应丢失时无法保证恰好一次，重试可能重复且每次消耗额度。不退还失败或不确定尝试的额度。

## 私有 SMTP 配置与依赖

告警和恢复通知只发送私有配置 `recipient` 指定的运维负责人，不读取用户邮箱、不向普通用户发送；验证码邮件与告警分属独立用途。

安装依赖为 #177 的 `mail_delivery.py`/`mail_budget.py` 共享实现（接口参考 bb33179）；启用通知前须先安装并验证共享额度配置。探针不复制或另建计数器，依赖缺失时告警状态照常落库、delivery_error=1、退出 1，SMTP 不连接。

CLI 通过独立 `smtp_notifier(config)` 生成 `send_notice(environment,code,transition)` callable；测试或集成可向 run_once 传替身。没有 shell 通知命令入口；Docker 参数用列表传递且不执行 shell。

尚未启用自动邮件时，在探针命令后加 `--sample-only`，只保存资源与告警状态，不构建发信器、不调用SMTP、不预占邮件预算、不更新 last_notified；已有待投递状态保留，返回0仅表示采样与状态处理成功。此模式的私有配置无需 recipient、smtp_tls 和 mail，只有明确启用通知后才按下文配置发信并移除此参数。

SMTP 复用 `mail_delivery._configuration`、地址校验及 `mail_delivery.mail_budget.configuration/reserve`（bb33179 实际共享配额接口名是 mail_budget）。仅支持私有配置显式 `smtp_tls=implicit`、465 端口和已获共享实现允许的主机，使用默认 CA 验证的 SMTP_SSL；禁用明文回退及 SMTP 调试。

必须配置共享 `NIUMA_MAIL_BUDGET_FILE/DAILY/TOTAL` 三项；不接受共享实现的无配额 None 模式。预算文件必须指向验证码服务实际使用的同一持久化文件，保留既有 mail_attempts/mail_limits，不清零、不换独立文件扩充额度；同一发信账号在 test/production 也共用该计数器。总额度应取账号剩余免费额度的保守值，由部署人核对，不能将配置上限视为供应商免费承诺。验证码服务也须启用同一预算配置后再启用探针通知；API 若仍允许缺省无限额发送，不能声称已实现全局额度约束。

配置文件仅放服务器私有目录，由执行 UID 所有且权限严格为 0600；以下全为占位值，不包含可用凭据或真实收件人：

```json
{
  "environment": "test",
  "database": "/srv/niuma-test/data/niuma.sqlite3",
  "disk_path": "/srv/niuma-test/data",
  "compose_project": "niuma-test",
  "health_url": "http://127.0.0.1:1431/api/health",
  "smtp_tls": "implicit",
  "recipient": "operations@example.invalid",
  "mail": {
    "NIUMA_MAIL_HOST": "smtpdm.aliyun.com",
    "NIUMA_MAIL_PORT": "465",
    "NIUMA_MAIL_FROM": "sender@example.invalid",
    "NIUMA_MAIL_PASSWORD": "REPLACE_PRIVATELY",
    "NIUMA_MAIL_TIMEOUT": "5",
    "NIUMA_MAIL_BUDGET_FILE": "/srv/niuma-private/shared-mail-budget.sqlite3",
    "NIUMA_MAIL_BUDGET_DAILY": "10",
    "NIUMA_MAIL_BUDGET_TOTAL": "100"
  }
}
```

## systemd 模板（部署人替换路径后安装）

以下文件为 Linux 主机安装模板；使用既有主机与数据卷。探针部署不需要更改 Compose 的入口、API 权限或微信开关，微信采集继续禁用。

`/etc/systemd/system/niuma-probe@.service`：

```ini
[Unit]
Description=Niuma host operations probe (%i)
After=docker.service network-online.target

[Service]
Type=oneshot
User=niuma-probe
Group=niuma-probe
SupplementaryGroups=docker
WorkingDirectory=/srv/niuma/current
UMask=0077
RuntimeDirectory=niuma-probe-%i
RuntimeDirectoryMode=0700
ExecStart=/usr/bin/flock -n /run/niuma-probe-%i/timer.lock /usr/bin/python3 /srv/niuma/current/server/operations_probe.py --config /etc/niuma-private/probe-%i.json
TimeoutStartSec=55
NoNewPrivileges=true
PrivateTmp=true
```

`/etc/systemd/system/niuma-probe@.timer`：

```ini
[Unit]
Description=Sample Niuma operations every minute (%i)

[Timer]
OnCalendar=*-*-* *:*:00
AccuracySec=1s
Unit=niuma-probe@%i.service

[Install]
WantedBy=timers.target
```

为执行用户预置读取业务库及其环境标记、写 sidecar/其父目录和共享额度文件的权限；不 chmod 整个生产数据卷。Docker 组属于主机管理权限，只授予专用主机账号，API 容器不获得该权限。外层 flock 与 CLI 内部 `<database>.operations.sqlite3.probe.lock` 均防重叠；所有调度与手动调用使用同一路径，锁文件不能在进程运行时删除。CLI 是 Unix 主机程序；Windows 不执行该入口，但采样函数在无 /proc 时可降级。

先在独立 test 环境用通知替身验证，再由部署人按授权启用真实收件人。启用模板命令示例为 `systemctl enable --now niuma-probe@test.timer`；production 必须有独立配置并核对现有卷、Compose 项目和健康端口后再启用。不要为了制造告警停止生产服务、填满磁盘或读取真实微信数据。

发布、备份或回滚前先暂停对应环境的 timer 并等待在途 oneshot 结束，再停止 API 备份完整数据卷（含 sidecar）；恢复 API/网页并验证健康后再恢复 timer，避免备份期间写入或把计划维护当成故障。

## 验证

仓库根目录运行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest server.test_operations_probe -v
```

测试使用临时数据库、合成 /proc、Docker 命令替身和 SMTP 替身，覆盖阈值、持续时间、重复间隔、失败重试、恢复、环境隔离、缺指标、共享额度拒绝、显式 TLS、隐私及 flock。`test_linux_proc_real` 仅在 Linux 执行真实 /proc 采样；macOS 验证真实磁盘采样及 CPU/内存 null 降级，跳过 Linux 实测。Windows 配置入口返回固定 unsupported_platform，POSIX 权限及 flock 用例跳过，其余逻辑可回归。部署前在独立 Linux 测试候选环境运行同一测试，再验证指定 Compose 容器及健康端点的触发/恢复；不通过停止生产或破坏共享测试环境制造故障。

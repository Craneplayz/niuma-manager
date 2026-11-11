# 微信附件契约 v0.3.1

本版修正 PR #98 的校验、安全、状态及审核规则；仍是阶段 A 契约，不能表示上传、鉴权、文件读取或模型链路已生产就绪。v0.2 运行逻辑不变。

## 版本与消费入口

- `contract_version` 必须为 `0.3.1`；字段结构沿用 [v0.3 草案](text-attachments-v0.3.md)，下述规则优先；不新增 context、附件或链接字段。
- 原 `*v0.3*` Schema 与样例保持冻结供旧草案回归，不能用于新运行端；v0.3.1 数据不得降级交给旧校验器。PR #103 需显式更换入口、样例及版本，不能只换字符串继续采用旧规则。
- 当前标准输入/输出为 `source-to-text/text-attachments-v031-request.json`、`text-to-app/text-attachments-v031-output.json`；其他成对场景见 `examples/text-attachments-v031-cases.json`。
- `schemas/bundle-v0.3.1.schema.json` 是自包含包，全部引用均为包内 JSON Pointer；默认根只接受七种约定信封之一，空对象不能通过。接口应选择明确入口，避免将合法重试体误当导入请求。

| bundle 入口 | 对应对象 |
| --- | --- |
| request / result | 完整识别输入 / 输出 |
| asset_ack | 文件上传回执 |
| ack / job | 导入或重试回执 / job 查询响应 |
| error / retry | HTTP 错误 / 重试请求 |

可直接取独立根 Schema，无需注册 URN 或联网解析：

```python
from jsonschema import Draft202012Validator, FormatChecker
from contracts.check_v031_examples import root_schema, check_request, check_pair, check_job

Draft202012Validator(root_schema("ack"), format_checker=FormatChecker()).validate(ack)
check_request(request)       # 结构 + 输入边界 + ID/上下文等语义
check_pair(request, result)  # 再检查证据、审核与状态汇总
check_job(request, job)      # 再检查进度及 job/result 一致性
```

其他语言可以读取 bundle，去掉顶层 oneOf，设置 `$ref` 为 `#/$defs/request`、`#/$defs/result` 或 `#/$defs/wechat-import__ack`（其余导入入口同名前缀），保留完整 `$defs`；必须开启 format 检查，并实现下文语义规则。不要直接验证只有 `$defs` 的 common/import 源 Schema 根对象。

修改四份 `*v0.3.1.schema.json` 源文件后运行 `python contracts/build_v031_bundle.py` 生成包，CI 的 `--check` 防止源文件与包漂移；不可手改生成包。

## 跨字段语义与审核

- input_id 在请求内唯一；asset_id 在主记录内唯一；block_id 在附件内唯一；context ID 不与主记录重叠，同 ID 上下文必须相同。不同消息可以引用同一文件，不能用 hash 跳过新上下文。
- evidence 必须引用本记录或显式 context；附件只能属于主记录；source 元数据原样复制，禁止外来 asset/block、伪造文字块引文、缺失任务必需证据。
- **任何候选只要使用 attachment_file + model_reported 证据，就必须 needs_review=true 且有 review_reasons，即使文件清晰、完整。** 前端不能自动入库，用户确认后才可应用；这是 v0.3.1 对原草案规则的明确收紧。
- 任一附件部分读取、正文不完整、质量 uncertain，整条记录的候选均待审核；规则与 model_reported 各自独立，不能删除文件证据来绕过覆盖疑点。
- success=完整处理且有候选；partial_success=未完整处理但有候选、错误及警告；no_tasks=完整可靠处理且无任务；failed=无可用候选且有错误。failed/partial 不得伪装为 no_tasks。
- 每条输入恰好一个 record_result；task_ids 与实际候选对应；候选 ID 唯一；批次全 failed 才 failed，存在 failed/partial 则 partial_success，否则有任务 success，全无任务 no_tasks。
- finished_records、failed_records 以记录为单位，满足 `0 <= failed <= finished <= total == 输入数`；终态 finished=total，failed 包含 failed/partial_success；job 状态须映射实际 result/record 状态；非终态 result=null。
- 请求级失败必须是 failed，且无伪 record_results/tasks；错误 ID 指向当前记录/附件/块，不能吞掉上游错误。

Schema 负责结构和可表达的条件；`check_pair/check_job` 负责跨对象关系。两者都必须运行，不能只因结构通过就自动入库。

## URL、文件名和边界

source_url 仅用于来源展示，不是下载授权，不自动抓取；无法给出稳定链接时用 null，保留稳定来源 ID。

- URL ≤2048 字符，仅 HTTP(S)、默认端口、无 userinfo、无空白/控制字符/反斜线/片段；拒绝本机、内网、非全局 IP、IPv4 映射私有 IPv6、缩写或异常数字地址、单标签和 .local/.localhost/.internal/.intranet/.lan/.home 等内部名称。
- 查询参数最多 20 个且键不重复，仅允许稳定导航键 `id,page,view,mode,method,context,course_id,handle,content_id,announcement_id,p,lang`（忽略大小写、下划线/连字符）；值≤256 字符。签名、凭据、到期参数及未知参数一律拒绝，不自动删掉后假装仍是原链接。
- 不得将供应商临时链接或路径式鉴权链接当来源 URL。离线校验无法识别任意服务的路径签名，也不解析 DNS；上游必须提供稳定原页面。将来实际抓取时，调用方须检查全部新解析的 A/AAAA 地址均为全局地址（可调用 check_resolved_source_url），连接固定在已验证 IP，并重新校验每次跳转，防止 DNS 重绑定；未实现该步骤不得启用抓取。
- file_name 只接受≤255 字符显示名；拒绝 `.`/`..`、路径分隔符、Windows 特殊字符/设备名、尾部空格或点、控制/方向格式字符以及编码后的路径穿越；文件存储路径由服务端生成，不能拼接显示名。

| 项目 | 上限（字符串按字符计，字节明确标注） |
| --- | --- |
| source_label / source.label / course_or_project | 512 |
| source_item_id / 各类 ID | 256 |
| MIME / reader_version / extractor_version / timezone | 128 |
| upstream_hints | 容器深度 4（根为第 1 层）、每对象≤32 键、每数组≤32 项、总节点≤256、规范化 UTF-8≤16,384 bytes |
| hints 键 / 字符串值 | 64 / 4096，键无控制字符，数值必须有限 |
| 单附件 errors / 整批 errors | 20 / 200 |
| 单条错误 message | 1024 |
| warnings / quality_notes / review_reasons / unread_locations | 各≤20 项，每项≤512 |
| 输入 / 输出 JSON | 各≤2,000,000 UTF-8 bytes（HTTP 层先限制实际请求体，校验器再限制规范化内容） |
| 每批 records / 每记录附件 / 上下文 | 20 / 10 / 100 |
| 每记录正文+上下文+可选文字块 | 100,000 字符 |

超限返回 limit_exceeded，不静默截断输入、引文或错误。原件≤20 MiB，实际读取还须遵守供应商更低的大小/页数/令牌预算；Schema 不能检查磁盘或远端真实字节。

## 幂等、重试和文件生命周期

| 操作 | 规范行为 |
| --- | --- |
| 相同账号、request_id、规范化内容 | 200 返回原 job 及当前状态，不重复入队或收费；JSON 键顺序/空白不影响内容指纹 |
| 同账号、同 request_id、内容不同 | 409 request_conflict；包括文件引用或采集时间改动，客户端应沿用原请求重传或另发新 request_id |
| 不同 request_id 的增量缓存 | 内容键包含消息/上下文/可信时间/文件 hash/读取器和模型配置版本；采集时间或 file_ref 重签发不构成新内容，不同消息不能仅按 hash 共用语义结果 |
| 同账号相同文件字节再次上传 | 校验实际字节后可返回原有效 file_ref（200），首次为 201；重用已验证元数据，不跨账号暴露去重结果 |
| hash/MIME/size/file_ref 与已授权存储元数据不一致 | 400 asset_metadata_mismatch，retryable=false；先检查文件归属，再比对，避免泄露他人文件信息 |
| 重复 job_id + retry_id | 返回原尝试的 job 状态（200）；不得创建第二次收费调用；重试目标在首次接受时固定 |
| 新 retry_id | 仅 failed/partial_failed 且仍存在可恢复的失败记录时 202；其他状态 409 invalid_job_state |

原件下载/上传失败、file_ref 失效、不支持格式需先恢复文件或读取器，不能反复调模型；当前服务端定向重试仅允许 retryable=true 的 read/recognize 阶段 agent_timeout、agent_unavailable、file_read_failed。成功结果和用户操作必须保留。

状态迁移：queued→running→succeeded/partial_failed/failed；初始化失败允许 queued→failed；同一尝试进度只能增加，queued 重放不改进度；终态只能原样读取。合法新重试使 failed/partial_failed→queued→running→终态，进度只扣除选定失败记录，其他成功/未重试记录继续计入 finished。

原始请求、时间基准、成功候选和用户处理状态不变；部分成功记录重试也保留已有候选 ID/内容，补充新候选必须去重，不能覆盖已确认任务。`check_job_transition/check_retry_result/check_replay` 是可执行参考检查；账号作用域、并发原子性、重试历史和用户编辑保存仍须 #96 的持久化测试证明。

文件生命周期由 #96 实现：

1. file_ref 是不可猜测、绑定账号和内容版本的句柄，不是凭据。上传、导入、读取、重试均以当前账号重新鉴权；不存在、他人、已撤销/删除句柄对外统一 404 not_found。
2. 初次上传但未被 job 引用的文件默认 24 小时到期；可配置但不得短于一次正常导入窗口。导入/引用绑定与失效检查必须原子执行。
3. 排队、运行、允许重试或仍有候选/结果引用的原件不按临时 TTL 清理；引用释放后按 24 小时未引用 TTL 清理。生命周期/配额必须显示在运行配置，不能把无限保留当默认清理策略。
4. 已认证 `DELETE /api/sources/wechat/assets/{file_ref}` 撤销自己的句柄并返回 204；重复删除自己的已删记录幂等，未知或他人记录 404。撤销后不能再次读取/重试；仍在运行的 job 停止后续读取并记录 file_ref_unavailable，不生成 no_tasks。此前已成功候选按产品删除规则处理，不自动改写其语义。
5. 删除来源/账号时撤销相关句柄并删除原件及临时渲染/缓存；共享底层字节须用引用计数，不能误删其他仍合法使用的数据。处理中的撤销/删除有竞态测试；审计记录不保留文件内容或可用下载凭据。
6. 句柄撤销/到期不可复活；重新上传签发新句柄。导入凭据撤销阻止之后请求，不等于删除文件；已接受的 job 仍归账号所有，删除账号/原件必须进一步撤销文件读取权限。

其他 HTTP 错误仍为 `{error:{code,message,retryable}}`：400 invalid_request/unsupported_version，401 unauthorized，403 forbidden，404 not_found，409 request_conflict/invalid_job_state，413 limit_exceeded，503 queue_busy；接收失败不伪造 job 成功结果。

## 时区、暂不支持的内容与 v0.4

- v0.3.1 的 timezone 仅接受可解析 IANA 名称（含 UTC），不接受含义不唯一的 CST 或 `+08:00` 字符串；固定偏移只出现在 ISO 日期时间 value 中，并校验它与所选 IANA 时区一致。
- timezone_basis=source 仅用于原消息或可信来源配置明确给定的时区；caller_default 是调用者明确配置的回退，不得仅凭语言/文件名/模型猜测；unknown 对应 timezone=null。context 使用自己的时间及时区，不继承当前消息时间冒充原始时间。
- GIF/WebP、语音、视频、PPT/PPTX、Excel/XLSX、压缩包等本轮不支持；采集端仍输出附件元数据、status=unsupported、file_ref=null 和 collect/unsupported_type，不上传原件、不悄悄丢弃。有正常正文的记录仍可输出部分结果，但需审核。
- 本轮不新增 context 原始消息标识/关联原因，也不新增 link/card 结构；列为 v0.4 设计项：原消息稳定标识、明确回复/引用的关联原因、多公开链接、卡片元数据及无公开 URL 的小程序卡片。v0.3.1 只接明确关联的已有 context，无法证明关联时不自动拼接。
- 不承诺完整支持卡片；上游可保留明确可见文字及稳定来源信息，无法表达的内容报告 incomplete_content，不能把卡片静默当作已完整读取。未来字段变更发布 v0.4，不挤入补丁版本。

## 验证与运行端门槛

```powershell
python -m pip install -r contracts/requirements-dev.txt
python contracts/build_v031_bundle.py --check
python contracts/check_v031_examples.py
python -m unittest discover -s contracts/tests -p 'test_*.py' -v
```

Schema 包、语义校验和反例覆盖七种入口、ID/证据、状态/进度、模型引用审核、输入边界、元数据冲突、重试保留成功结果和不支持格式；旧 v0.3 样例独立回归，旧 v0.2 运行测试仍需通过。

这些检查不验证真实鉴权、上传/存储原子性、TTL/删除、DNS/网络、模型语义或真实文件覆盖。#103 必须切换 v0.3.1、补齐对应持久化/HTTP 测试；#94 读取适配器、#95 采集及 #96/#97 联调完成前不开放附件入口。PNG 曾有合成能力试探成功，PDF/DOCX 仍须 adapter/reader 联调验证后逐格式启用。

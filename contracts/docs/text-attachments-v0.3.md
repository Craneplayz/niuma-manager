# 文本与微信附件交接 v0.3

本规约对应 #94–#97；Schema 与样例是交接基线，当前 PR 仅提供契约，不代表应用已支持这些接口。

## 版本与入口

- v0.2 请求、输出和现有接口保持原样；v0.3 必须显式声明 `contract_version:"0.3"`，响应也必须为 v0.3，不能直接换版本号后交给 v0.2 校验器。
- v0.3 仍接收统一文本 JSON；图片、PDF、DOCX 先由本地微信模块解析/OCR，上传文字块与元数据，原始二进制文件留在本地。
- 首版支持 JPG/PNG、原生或扫描 PDF、DOCX；DOCX 表格、嵌入图片也属于覆盖范围，未读部分必须声明，其他格式返回 `unsupported`。
- 来源模块运行一次采集命令即可解析并上传，持久后台定时器不属于本轮附件协议的前置要求。
- 先合并契约，再并行实现 #95 采集、#94 Agent 适配、#96 后端；#97 前端按样例开发，联调通过后才开放入口。

| 文件 | 校验对象 |
| --- | --- |
| `source-to-text/request-v0.3.schema.json` | 完整识别请求 |
| `text-to-app/result-v0.3.schema.json` | 完整候选结果 |
| `source-to-app/wechat-import-v0.3.schema.json#/$defs/ack` | 导入/重试回执 |
| 同文件 `#/$defs/job` | 进度查询响应 |
| 同文件 `#/$defs/retry` | 重试请求体 |
| 同文件 `#/$defs/error` | HTTP 错误响应 |
| `schemas/common-v0.3.schema.json` | 上述 Schema 共享定义，不直接用于验证完整请求 |

Schema 使用 JSON Schema Draft 2020-12；调用方须启用 `format` 校验并按 `$id` 注册这四份本地 Schema，不能只加载导入 Schema 根对象（其根只有定义），也不能依赖联网解析 `$ref`。

## 请求

顶层固定为 `{contract_version, request_id, records}`；每条记录保留 v0.2 字段，并新增必填 `attachments:[]`，所有可空字段明确写 `null`。

| 字段 | 约定 |
| --- | --- |
| `input_id` / `source_item_id` | 用户作用域内稳定、不暴露原账号的消息 ID；重采集同条消息保持不变 |
| `text` / `text_quality` | 聊天正文及其完整程度；纯附件可用 `""` / `missing`，但必须至少提供一个附件元数据对象，即使文件读取失败 |
| `published_at` | 当前消息发送时间；`{value,precision,basis}`，未知三项为 `null/unknown/unknown` |
| `collected_at` | 本次采集时间，可空，不能充当原消息或原附件发布时间 |
| `timezone` / `timezone_basis` | IANA 时区及来源；未知为 `null/unknown`，日期时间带偏移量 |
| `source_url` / `source_label` | 原消息链接和可读名称；没有链接写 null，不编造 URL |
| `course_or_project` | 可信上游课程名或 null，缺课程名本身不强制待审核 |
| `context` | 相关文字消息，沿用 v0.2 字段；本版不嵌套附件，ID 不与本批主记录重叠，同一上下文 ID 内容必须一致 |
| `upstream_hints` | 沿用 v0.2 提示对象或 null；只能引用其中真实原文，不将猜测当证据 |
| `attachments` | 与该主消息直接关联的附件；相邻消息只能作线索，不能仅凭相邻就确定其 DDL 适用于附件 |

`input_id/asset_id/block_id` 分别在请求、主记录、附件范围内唯一；同一附件出现在不同消息里仍按各自指令识别，不能仅按文件哈希去掉另一条消息。

## 附件与文字块

| 附件字段 | 约定 |
| --- | --- |
| `asset_id` | 稳定、不透明的附件 ID，文件名相同不代表同一附件 |
| `file_name` / `mime_type` | 仅显示名与实际 MIME，不传本地绝对路径或 `file://` |
| `size_bytes` / `sha256` | 原文件大小和 SHA-256，小写 64 位十六进制；尚未取得文件时可为空 |
| `origin` | `original/forwarded/unknown`，不把转发当作新发布 |
| `original_published_at` | 附件内容的可信原始发布时间或未知；禁止用文件修改时间代替 |
| `status` | `complete/partial/failed/unsupported`，见下表 |
| `extractor_version` | 可复现的解析/OCR 版本；有已解析内容时必填，失败且未解析时可为空 |
| `coverage` | `{complete,unread_locations,truncated}`，如未读第 2 页或整个附件；任何丢弃都必须记录 |
| `blocks` | 有顺序的原始文字块；不把不同页面、文件的文字无标记拼成一段 |
| `errors` | `{stage,code,message,retryable}[]`，`stage` 为 collect/extract；完整成功时为空 |

每个块固定为 `{block_id,text,locator,method,quality,quality_notes}`；`method=native/ocr`，`quality=normal/uncertain`，不确定必须附可读原因。

`locator={page,paragraph,bbox}` 中页码和段落号从 1 开始，至少一项非空；`bbox` 为 `[left,top,right,bottom]` 的 0–1 归一化坐标，必须有页码且面积大于零，未提供时为 null。

DOCX 段落按文档阅读顺序编号，表格单元格按行顺序计入；嵌图关联锚定段落，存在可靠分页时再填写页码，不能随意给所有嵌图填“第1页”。

| 附件状态 | 覆盖与块 | 后续处理 |
| --- | --- | --- |
| complete | 全部读取、未截断、无错误；真正空白文件允许 blocks=[] | 正常识别；OCR 质量仍可 uncertain |
| partial | 未完整读取，有部分文字块，说明未读位置并有错误 | 保留可用任务并待审核，不能伪装完整成功 |
| failed | 无可用文字块，有读取/解析错误 | 显示失败，不能转换成“没有任务” |
| unsupported | 格式未支持、无文字块并有错误 | 提示换格式或人工查看 |

`coverage.complete` 和 `quality` 分开：全页扫描完但文字模糊属于 complete + uncertain；OCR 失败不能声称原文件空白。

## 识别输出与证据

顶层为 `{contract_version,request_id,status,record_results,tasks,errors}`；候选保留 v0.2 字段结构，新增的附件证据形如：

```json
{"input_id":"wechat:message:002","field":"attachment","asset_id":"asset-001","block_id":"block-001","quote":"原始文字块中的连续原句","supports":["title","deadline"]}
```

- 文本证据仍为 `{input_id,field,quote,supports}`；附件证据只能指向本记录实际存在的附件与块，`quote` 必须逐字出现在原块中。
- 上下文证据只能引用本条输入显式提供的 context；sources 元数据从输入复制，不能补造账号、链接或来源。
- 页码、段落、bbox 由应用按 ID 查原请求，不接受模型另写位置；当前阶段不要求浏览器能打开本地原文件。
- `deadline.value` 只放机器可读日期/日期时间，解释在 basis/review_reasons；日期仅精确到天时不得补成 23:59。
- 同一任务同时出现“尽快”和明确截止时间时采用明确时间；补年遵循既有最近合理年份规则，单纯缺年份不触发待审核。
- 转发旧截图且原始年份未知、通知是否仍适用不明是独立疑点，不能以转发或采集时间把旧作业变成本年新作业。
- 完整、可靠地读完仍没有日期，才可给行动 `deadline.status=not_provided`；未读页可能含日期时使用 unknown，不得判断“原文没写日期”。
- 长文件内部切块必须保留 input/asset/block ID、任务与末页日期的关联；不能逐块丢弃上下文后当独立完整消息处理。

`record_results` 每条输入恰好一条，字段为 `{input_id,status,task_ids,warnings,coverage,attachments}`：

- `coverage={complete,uncertain}`：complete 表示正文/附件覆盖完整且识别也完成；uncertain 表示上游正文不完整或存在不确定 OCR，不是模型自信程度。
- 首版保守处理：任一块 uncertain，该记录的候选都标待审核；若将来只降级受影响候选，应单独修改契约。
- `attachments=[{asset_id,status,block_count,uncertain_block_ids}]` 必须忠实复述输入解析状态，模型超时不会把附件的解析成功改成失败。
- `task_ids` 对应该记录输出候选的 ID；请求级失败以外不能漏掉未产任务的记录。

| 记录状态 | 条件 |
| --- | --- |
| success | 全部内容处理完成且产出候选；有 OCR/语义疑点时仍须 needs_review=true |
| no_tasks | 内容读取、识别完整且无影响判断的疑点，确实没有应保留任务 |
| partial_success | 提取或识别仅部分完成，但已有可用候选；附 errors、warnings，候选全部待审核 |
| failed | 未完整完成或质量不足以判断，并且没有可用候选；必须附 errors |

批次全部记录 failed 才是 failed；任一 failed/partial_success 则为 partial_success；否则有候选为 success，全部无候选为 no_tasks。

`errors` 每项固定为 `{input_id,asset_id,block_id,stage,code,message,retryable}`；请求级错误三种 ID 为空且 stage=request，其他错误绑定当前输入，附件/块可进一步定位；上游附件错误必须原样保留 code/stage，不能被空 tasks 吞掉。

错误码包含 invalid_request/invalid_input/unsupported_version、asset_missing/decrypt_failed/unsupported_type/limit_exceeded/parse_failed/ocr_failed/incomplete_content，以及 low_ocr_quality/agent_timeout/agent_unavailable/invalid_agent_output；具体可用值以 Schema 为准。

## 异步导入 API（#96 实现）

| 操作 | 请求 | 正常响应 |
| --- | --- | --- |
| POST `/api/sources/wechat/imports` | 完整 v0.3 请求 | 校验、持久化并入队后 202 + ack，不等待模型 |
| GET `/api/sources/wechat/imports/{job_id}` | 无 body | 200 + job，仅当前账号可见 |
| POST `/api/sources/wechat/imports/{job_id}/retry` | `{"retry_id":"客户端生成且此次重试保持不变的ID"}` | 202 + ack，仅重试有条件重试的失败识别单元 |

ack 为 `{contract_version,job_id,request_id,status}`；同请求重放返回原 job 和当前状态（200），不重新调用模型。

job 为 `{contract_version,job_id,request_id,status,progress,result}`；status 取 queued/running/succeeded/partial_failed/failed，queued/running 不提供最终结果（result=null），终态返回完整 v0.3 结果。

`progress={total_records,finished_records,failed_records}`，单位是输入记录，不是页或模型调用；failed_records 包含 failed 和 partial_success，始终满足 `failed <= finished <= total`；终态 finished=total，no_tasks 也计作完成。

终态映射为识别 success/no_tasks → succeeded，partial_success → partial_failed，failed → failed；阶段 B/C/D 的进度 UI 不得将“没任务”显示成“未处理”。

同 job 同 retry_id 重放返回该次重试状态，不新增收费调用；新 retry_id 只允许在终态且存在可重试识别失败时提交，运行中返回 409 invalid_job_state。

重试期间已成功的候选和用户处理状态保留；进度中已成功记录计入 finished，只清除待重试记录的本轮完成计数，因此重试的 queued 状态也可以有已完成记录；再次终态返回原成功与本次重试的合并结果，不重复新增候选。

缺文件、解密失败、OCR 失败需回到本地获取/解析后提交新内容版本，不能对同一空文字反复调用模型；retryable 表示该阶段错误存在恢复办法，不代表所有错误都能用服务端 retry 接口修复。

| HTTP 状态 | error.code | 约定 |
| --- | --- | --- |
| 400 | invalid_request / unsupported_version | 格式、版本或请求规则不满足 |
| 401 / 403 | unauthorized / forbidden | 未认证或凭据无导入权限 |
| 404 | not_found | job 不存在或不属于当前账号，不泄露其他账号是否有该 job |
| 409 | request_conflict / invalid_job_state | 相同 request_id 内容不同，或当前状态无法重试 |
| 413 | limit_exceeded | 超过请求/记录预算；不能静默截断后识别 |
| 503 | queue_busy | 未入队，可按退避规则重试，不得响应虚假成功 |

非 2xx 响应为 `{error:{code,message,retryable}}`，接收前被拒绝的请求不创建伪识别结果。

## 身份、幂等与预算

- 本地上传使用后端签发、绑定当前账号、可撤销且仅限微信导入/进度/重试的凭据；签发/撤销入口由 #96 负责实现并补充规约，不复用微信密码或模型 API Key。
- 账号由凭据或现有登录会话确定，不接受 payload 自报 user_id；所有 job、附件证据、候选查询都按账号隔离。
- 请求幂等键为账号 + request_id；比较规范化 JSON 内容，键顺序/空白变化不视为不同，内容不同返回 409。
- 跨请求内容去重至少纳入稳定消息 ID、正文、相关上下文、发布时间/时区/课程、附件哈希和解析器版本/提取内容；采集时间、临时路径不作为内容版本。
- 本地相同输入/解析器失败后的重跑可以上传新版本；更换文件内容或解析器后不能命中旧成功缓存，同一文件不同消息说明也不能共用语义结果。
- 默认上限：每次 JSON 实际 UTF-8 请求体 2,000,000 bytes，1–20 records，每记录最多 10 附件、100 条上下文，正文 + 上下文 + 块文字合计 100,000 字符。
- 单个原文件上限 20 MiB；超限不读取全文，仍可上传 status=failed/limit_exceeded 的元数据说明，不能把截断文件标成 complete。
- JSON 范围内的部分解析也必须声明未读位置；超过服务器 JSON/文字总预算则拒绝整个请求，由本地重排批次或保留失败元数据，不能丢块后谎称完整。
- 识别复用 #69 的并发限制、约 90 秒识别单元超时；一次导入不把全部文件串行塞进一个 HTTP 超时窗口，具体切块调度由 #94 适配实现。
- 不上传绝对路径、原账号标识、Cookie、密钥；任务原文仍按已有隐私和模型服务授权配置处理，契约不会自动扩大授权范围。

## 离线校验与验收边界

在仓库根目录执行：

```powershell
python -m pip install -r contracts/requirements-dev.txt
python contracts/check_v03_examples.py
python -m unittest discover -s contracts/tests -p 'test_*.py' -v
```

`check_v03_examples.py` 检查 Schema、外键/引用、原句、来源、覆盖程度、状态聚合与进度；实际 HTTP 字节上限、鉴权、去重、重试并发还需 #96 的服务测试，离线重序列化大小检查不能代替服务器原始请求体限制。

主样例是“无聊天正文的图片 + 明确关联的前文截止要求”；examples 中覆盖纯文字、PDF 多任务/日期仅到天、DOCX 原生与嵌图、无日期行动、资讯/空白、部分解析、OCR 模糊、文件缺失/不支持、旧图转发、部分识别超时以及跨页截止。

所有样例都是人工合成契约期望，SHA-256/大小为占位元数据，不对应真实原文件；#95 负责真实文件/OCR 测试，#94 后续负责模型与切块语义测试，#96/#97 负责导入/审核/展示联调，Schema 通过不代表模型判断正确。

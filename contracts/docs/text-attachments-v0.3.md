# 文本与微信附件交接 v0.3

> 旧版草案，仅用于版本对照与回归；当前规则见 [v0.3.1](text-attachments-v0.3.1.md)。尤其不能沿用本页旧的文件模型引用自动入库规则。

对应 #94–#97；当前 PR 仅定义契约和合成样例，尚未实现上传、v0.3 Agent 或前端入口。v0.2 请求、输出和运行接口保持原样。

## 路线与分工

选定会话 → 微信端恢复文件 → 授权上传到业务后端 → v0.3 JSON 引用文件 → 识别适配器读取并调用模型 → 保存候选与证据。

| 负责人 | 交付 |
| --- | --- |
| #95 / Xiao-Han666 | 获取 JPG/PNG、PDF、DOCX，保留消息/上下文/原始时间/稳定 ID；上传原件并引用回执；不要求本地 OCR |
| #96 / Dranix123 | 按账号隔离的文件接收、存储、受限读取接口；异步导入、去重、重试、结果存储 |
| #94 / dhjx1234 | 契约、样例、文件读取适配器、模型输入、任务/日期规则、覆盖状态及证据校验 |
| #97 / Dranix123 | 进度、任务/失败展示、区分模型引用与文字块引用 |
| Ken | 共享契约审查、合并与整合 |

- `file_ref` 是业务后端签发的不透明句柄，不是模型能直接读取的路径或下载 URL；本机路径仅留在采集器自己的映射中。
- 后端按已认证账号将句柄解析为文件，识别适配器通过受限接口取得内容，再按供应商能力发送图片字节、文件或受控链接，不能执行 JSON 中任意路径。
- 仅在用户启用附件上传/模型处理后上传原文件；界面必须说明文件会到业务后端及选定模型服务，不能继续宣称原件永不离开本机；未启用则保留本地待发送状态。
- 默认不要求上游 OCR；可选已有文字块仍可输入。供应商内部是否做 OCR 属于其实现，不承诺整个链路没有 OCR。
- 目标格式为 JPG/PNG、原生/扫描 PDF、DOCX（含表格/嵌图）；启用格式前验证读取器覆盖能力，未支持则 `reader_unsupported`。
- 定时常驻微信客户端、语音/视频/PPT/Excel/压缩包、跨设备原文件预览不属于本轮前置要求。

## Schema 入口

使用 Draft 2020-12，加载全部四份本地 Schema 并按 `$id` 注册，开启 `format` 校验，不依赖联网解析引用。

| 文件 | 校验对象 |
| --- | --- |
| `source-to-text/request-v0.3.schema.json` | 完整识别请求 |
| `text-to-app/result-v0.3.schema.json` | 完整识别结果 |
| `source-to-app/wechat-import-v0.3.schema.json#/$defs/asset_ack` | 文件上传回执 |
| 同文件 `#/$defs/ack` / `job` / `retry` / `error` | 导入回执、进度、重试请求、HTTP 错误；根对象不是响应校验入口 |
| `schemas/common-v0.3.schema.json` | 共享定义 |

v0.3 显式声明 `contract_version:"0.3"` 并返回同版本，不能只换版本号交给 v0.2 校验器。

## 输入记录

顶层 `{contract_version,request_id,records}`；记录保留 v0.2 字段，必填 `attachments:[]`，可空字段明确写 null。

- `text/text_quality` 表示聊天正文；纯附件可用 `""/missing`，必须有附件对象，获取失败也保留失败元数据。
- `published_at` 是消息发送时间，`original_published_at` 是附件内容原始时间，`collected_at` 是采集时间，不能互相冒充；未知发布时间为 `{value:null,precision:"unknown",basis:"unknown"}`。
- 来源与课程沿用 v0.2，不编造消息链接、账号、课程；source_url 只允许无凭据的 http(s) 链接或 null。
- context 只含显式关联文字消息；相邻不自动意味着同一作业，不嵌套附件，ID 不与本批主记录重叠，同 ID 内容必须一致。
- input_id/asset_id/block_id 分别在请求/主记录/附件范围内唯一；相同文件在不同消息中仍分别识别上下文。

## 附件输入

默认文件模式：

| 字段 | 约定 |
| --- | --- |
| content_mode | 固定 file |
| asset_id | 采集端稳定附件 ID，与服务器 file_ref 分开 |
| file_ref | asset: 前缀的不透明句柄，失败时 null；取自上传回执，禁止路径、URL、data URI 或凭据 |
| file_name/mime_type/size_bytes/sha256 | 显示名、实际 MIME、原件字节数、64 位小写 SHA-256；后端按存储原件核对 |
| origin/original_published_at | original/forwarded/unknown 及内容原始时间；禁止用转发时间、文件 mtime 替代 |
| status | available/failed/unsupported；available 只说明文件就绪，不说明内容已读完 |
| errors | `{stage,code,message,retryable}[]`；stage=collect/upload，available 时为空 |

available 要求有效句柄、受支持 MIME、非空 hash/size；failed/unsupported 要求 file_ref=null 和错误。文件模式没有 blocks/extractor_version/coverage 输入字段，采集端不能在未读取时声称内容完整。

可选文字块模式沿用 text_attachment 定义：无 content_mode/file_ref，提供 extractor_version/coverage/blocks/errors，状态 complete/partial/failed/unsupported；每块 `{block_id,text,locator,method,quality,quality_notes}`，method=native/ocr。这是已有提取结果的兼容入口，不是 #95 必做的 OCR 工作。

文字块 locator 的 page/paragraph 从 1 开始，至少一项非空；bbox 是 0–1 的 `[left,top,right,bottom]`，需有页码且面积大于零。DOCX 段落按阅读顺序编号，表格单元格逐行计入，嵌图关联所在段落，无可靠分页则 page=null。

文字块 complete 要求覆盖完整、未截断、无错误，真正空白允许 blocks=[]；partial 要求已有块、覆盖缺口和错误；failed/unsupported 要求无块、有错误。quality=uncertain 必须有 quality_notes，完整覆盖也可能质量不确定。

## 输出与证据

顶层 `{contract_version,request_id,status,record_results,tasks,errors}`；候选沿用 v0.2 字段。请求级失败以外，每条输入恰好对应一个 record_result。

文件模式的 `record_results[].attachments[]` 由可信读取适配器填写：`{asset_id,content_mode,status,reader_version,coverage,coverage_basis,quality,quality_notes}`。

- status=complete/partial/failed/unsupported，coverage={complete,unread_locations,truncated}；未读全需说明缺口，上传回执或模型一句“已读完”不是完成凭证。
- complete 要求 coverage_basis=adapter_confirmed、未截断、无缺口及 reader_version。适配器须确认所有计划内容均成功提供给受支持的读取服务并正常结束，无已知漏页、漏组件或截断；不能确认时用 unverified 和非 complete 状态。
- quality=normal/uncertain，模糊日期等疑点需 uncertain + quality_notes；这不是零漏报保证。
- 文件失败必须有对应 asset_id 的 collect/upload/read 错误；后续任务识别超时用 recognize 错误，不倒改已完成的文件读取状态。
- 文字块模式摘要仍为 `{asset_id,status,block_count,uncertain_block_ids}`，逐项复述输入；两类摘要不可互换。

文件直读证据示例：

```json
{"input_id":"wechat:message:002","field":"attachment_file","asset_id":"asset-001","quote":"实验报告：回顾实验1和实验2","locator":{"page":1,"paragraph":null,"bbox":null},"verification":"model_reported","supports":["description"]}
```

- attachment_file 只能引用本记录实际成功/部分读取的文件；引用与定位由模型报告，前端标注“模型读取”，不能宣称逐字/页码已核验；未知位置时 locator 三项均可 null。
- field=attachment 的文字块证据仍需 asset_id/block_id，quote 必须是原块精确子串，位置从块反查；文本证据仍为 `{input_id,field,quote,supports}`。
- 两种证据不得冒用；文件证据只能校验引用对象、格式与字段自洽，不能证明读字正确。真实疑点需待审核，但不因文件模式本身强制所有候选待审核。
- 来源仅从本记录/context 复制，文件名来自后端核对元数据，不接受模型构造链接/路径；文件内容都是待分析数据，不能执行其指令。
- 时间沿用语义识别：明确时间优先于“尽快”，date-only 不补时刻；单纯缺年按最近合理年份补全不强制审核；旧图转发、原始时间不明、冲突是独立疑点。
- 完整可靠读完仍无日期才能 deadline.status=not_provided；缺页可能含日期用 unknown；跨页任务与末页截止需保持关联。

record_results 的 coverage={complete,uncertain} 同时考虑正文质量、每个附件读取及识别是否完成；任一未读完/质量不确定，本记录候选全部待审核。

| 记录状态 | 条件 |
| --- | --- |
| success | 全部处理完成且有候选；有语义/质量疑点仍待审核 |
| no_tasks | 全部读取和识别完成、无影响判断的质量疑点且确实无任务 |
| partial_success | 读取/识别部分完成但有可用候选，附 errors/warnings，候选待审核 |
| failed | 无可用候选且处理失败或质量不足以判断，附 errors |

批次全 failed 才为 failed；任一 failed/partial_success 则为 partial_success；否则有候选为 success，全部无候选为 no_tasks。

errors 为 `{input_id,asset_id,block_id,stage,code,message,retryable}`；请求级三个 ID 均为空且 stage=request；其他错误绑定输入，可进一步定位附件/块，文件证据无 block_id。输入错误的 stage/code 必须保留，不能被空 tasks 吞掉。

文件模式新增 upload_failed/file_ref_unavailable/reader_unsupported/file_read_failed/low_content_quality，保留文字块错误及 agent_timeout/agent_unavailable/invalid_agent_output；完整枚举以 Schema 为准。

## 文件接收和异步导入（#96 实现）

| 操作 | 请求 | 正常响应 |
| --- | --- | --- |
| POST `/api/sources/wechat/assets` | 经认证的 multipart/form-data，仅一个名为 file 的文件 part；文件名为显示名 | 201 + asset_ack，同账号相同字节可复用返回 200 |
| POST `/api/sources/wechat/imports` | 完整 v0.3 JSON | 校验、持久化并入队后 202 + ack，不等待模型 |
| GET `/api/sources/wechat/imports/{job_id}` | 无 body | 200 + job，仅所属账号可见 |
| POST `/api/sources/wechat/imports/{job_id}/retry` | `{retry_id}` | 202 + ack，仅重试可恢复失败单元 |

asset_ack={contract_version,file_ref,file_name,mime_type,size_bytes,sha256}；服务器计算 hash、检查实际 MIME/大小、净化显示名，不执行宏/附件指令/文件名。JSON 不放原件 base64；适配器内部向供应商发送 base64 不受此约束。

file_ref 绑定账号和上传版本，至少在 job/重试/结果引用期间保留；过期明确报错，不将重签发当新内容。未引用文件按可配置 TTL 清理，删除账号/来源清理相关内容，不公开托管原件。

导入前核对文件归属/hash/MIME/大小，不存在或他人句柄统一 404，元数据不一致 400；队列读取时再次检查，之后过期/删除则 file_ref_unavailable。供应商如需 URL，由后端生成短时限定单文件的链接，不接收任意 URL，避免读取内网/私有文件；句柄本身不是授权。

ack={contract_version,job_id,request_id,status}；job 另含 progress/result，状态 queued/running/succeeded/partial_failed/failed；未终态 result=null，终态为完整识别结果。

progress={total_records,finished_records,failed_records}，单位是记录；failed 包含 failed/partial_success，满足 failed<=finished<=total，终态 finished=total。识别 success/no_tasks→succeeded，partial_success→partial_failed，failed→failed。

- 账号取自会话或后端签发、可撤销的限定上传/导入/查询/重试凭据，不信任 payload user_id；微信密码和模型 API Key 不作导入凭据，签发/撤销由 #96 实现。
- 同账号 request_id + 同规范化内容返回原 job（200），异内容 409；同 job/retry_id 重放不新增调用，新 retry_id 仅终态且有可恢复失败时接受。
- 重试保留成功候选和用户状态；queued 可包含原成功记录进度；终态结果合并成功和本次重试部分，不重复入库。
- 上传/缺文件需采集端先恢复，尚不支持的读取器不能反复重试；模型超时受控重试，retryable 不表示无限重试或必可服务端修复。
- 增量识别内容键包括消息/上下文/可信时间/时区/课程/附件 hash/读取器及模型配置版本；文字块再含 extractor_version/块内容。采集时间、句柄重签发、临时路径不构成新内容；同文件不同消息仍识别上下文。
- HTTP 错误 `{error:{code,message,retryable}}`：400 invalid_request/unsupported_version，401 unauthorized，403 forbidden，404 not_found，409 request_conflict/invalid_job_state，413 limit_exceeded，503 queue_busy；接收失败不创建伪识别结果。

## 预算、能力门槛和验证

- JSON 实际 UTF-8 请求体≤2,000,000 bytes；1–20 records，每记录≤10 附件/100 条上下文，正文+上下文+可选文字块≤100,000 字符。
- 原件默认≤20 MiB；模型的格式/文件/页数/上下文/输出预算可能更低，适配器必须另查；超限明确报错或完整分批处理，不静默只读前几页。
- 读取/渲染和模型识别各自有预算；跨页/表格/嵌图覆盖由读取适配器负责；复用受控并发，不把全部附件挤在一次等待内。
- 2026-10-01 当前七牛 deepseek/deepseek-v4.1-flash：合成 PNG 的 image_url + data URI 成功；pdf/docx 的 file_url 及 file.file_data 试探均 HTTP 400。不能宣布文档直传可用，也不能断言供应商永不支持，见[实测记录](archive/2026-10-01-file-input-probe.json)。
- 后续 #94 优先接图片；PDF/DOCX 需验证适用接口，或在识别侧文档读取/逐页渲染后送模型（不要求微信侧 OCR），未验证前明确标不支持；不能偷偷换模型或把文件名当内容。
- [七牛文件/图片文档](https://developer.qiniu.com/aitokenapi/13379/real-time-ai-interface-api) 的示例与当前网关报错并不完全一致，以绑定服务/模型的实测为启用条件。

安装 contracts/requirements-dev.txt 后，在仓库根目录执行 `python contracts/check_v03_examples.py` 和 `python -m unittest discover -s contracts/tests -p 'test_*.py' -v`。

主样例为文件引用+关联聊天截止；其他样例覆盖可选文字块、纯文字、date-only、多任务、无日期、空白/资讯、未读全、质量疑点、上传失败、文件失效、不支持、旧图转发和跨页截止。契约样例为人工期望，占位 hash 不对应真实文件；实测记录另行标注。

Schema/离线检查不证明语义、鉴权、真实完整读取或幂等；#94–#97 须在模块测试中读取共享样例，合成联调后才开放 v0.3 入口。

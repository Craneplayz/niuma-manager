# 爬虫模块

```text
crawler/
├── README.md                         本模块安装与运行说明
├── .gitignore                        忽略爬取数据、日志和本地凭据
├── package.json                      定义命令、依赖和 Node.js 版本
├── pnpm-lock.yaml                    锁定 JavaScript 依赖版本
├── tsconfig.json                     配置 TypeScript 编译
├── eslint.config.js                  配置 ESLint 检查
├── vitest.config.ts                  配置 Vitest 测试
├── secret                            本地校园卡凭据文件，不提交到 Git
├── data/                             保存原始页面、候选记录、规范记录和审阅网页
├── logs/                             保存结构化运行日志
├── src/                              爬虫及课程采集实现
│   ├── public-import-cli.ts         现有三个公开适配器的受限网页预览导出入口
│   ├── cli.ts                        公共事件爬取、重处理与审阅入口
│   ├── crawler.ts                    调度抓取、解析、过滤和存储
│   ├── index.ts                      导出爬虫公共接口
│   ├── logger.ts                     写入结构化运行日志
│   ├── normalizer.ts                 将候选事件规范化
│   ├── review-report.ts              生成事件审阅网页
│   ├── pku-homework-cli.ts           北大课程采集命令行入口
│   ├── pku-homework.ts               登录并采集课程内容
│   ├── pku-attachments.ts            同源附件下载、PDF/TXT 文本提取、图片/DOCX/扫描页暂存
│   ├── pku-assignment.ts             只读解析作业详情、到期日期与提交状态
│   ├── pku-teaching-evidence.ts      严格导出独立正文与附件证据，报告无法表达的记录
│   ├── pku-teaching.ts               为受控同步包装 v0.4 证据、学期上下文与原生提交状态
│   ├── pku-recognition.ts            转换课程记录为识别请求
│   ├── pku-source.ts                 解析课程页面和来源记录
│   ├── adapters/                     各公开来源的发现与解析规则
│   │   ├── bio.ts                    解析生命科学学院页面
│   │   ├── cs.ts                     解析计算机学院页面
│   │   ├── gse.ts                    解析教育学院页面
│   │   ├── helpers.ts                提供适配器解析辅助函数
│   │   ├── index.ts                  注册公开来源适配器
│   │   └── source-adapter.ts         定义来源适配器接口
│   ├── core/                         共享类型、时间和校验逻辑
│   │   ├── schemas.ts                定义 Zod 数据结构
│   │   ├── time-filter.ts            按指定时刻筛选未来事件
│   │   ├── time.ts                   解析事件时间
│   │   ├── types.ts                  定义爬虫数据类型
│   │   └── utils.ts                  提供通用解析工具
│   ├── fetcher/                      HTTP 获取实现
│   │   └── public-http-fetcher.ts    请求公开网页并处理重试与限速
│   └── storage/                      原始网页和 JSONL 存储实现
│       ├── jsonl-store.ts            读写 JSONL 文件
│       └── raw-snapshot-store.ts      保存原始网页及来源信息
├── tests/                            自动化测试
│   ├── export-teaching-attachment-smoke.ts  合成附件经实际解析器导出受控快照和文件收据
│   ├── adapters.test.ts              测试公开来源适配器
│   ├── crawler.test.ts               测试抓取与重处理流程
│   ├── fetcher.test.ts               测试 HTTP 获取器
│   ├── normalizer.test.ts             测试事件规范化
│   ├── pku-homework-cli.test.ts       测试课程采集命令行
│   ├── pku-homework-html.test.ts      测试课程页面解析
│   ├── pku-homework.test.ts           测试课程采集规则
│   ├── pku-attachment-download.test.ts 测试流式大小限制、重定向及鉴权边界
│   ├── pku-attachments.test.ts        测试附件读取、大小限制和失败状态
│   ├── pku-pdf-process.test.ts        测试 PDF 子进程退出、崩溃与超时恢复
│   ├── pku-assignment.test.ts         测试作业详情页的安全只读解析
│   ├── pku-navigation.test.ts         测试课程页面导航
│   ├── pku-recognition.test.ts        测试识别请求转换
│   ├── pku-provenance.test.ts         测试课程入口证据和快照版本
│   ├── pku-teaching-evidence.test.ts 测试独立正文、引用与 v0.4 契约交接
│   ├── pku-teaching.test.ts           测试受控同步、学期与原生提交状态交接
│   ├── pku-source.test.ts             测试课程来源记录
│   ├── review-report.test.ts          测试审阅网页生成
│   ├── time-filter.test.ts            测试未来事件筛选
│   ├── time.test.ts                   测试时间解析
│   └── fixtures/                      保存脱敏的 HTML 测试样例
│       ├── bio/                       生命科学学院测试页面
│       │   └── list.html              生命科学学院列表页样例
│       ├── cs/                        计算机学院测试页面
│       │   └── list.html              计算机学院列表页样例
│       ├── gse/                       教育学院测试页面
│       │   ├── detail.html            教育学院详情页样例
│       │   └── list.html              教育学院列表页样例
│       ├── pku-assignment/            脱敏的作业详情与提交历史页面
│       └── malformed.html             异常结构测试页面
└── docs/                              爬虫模块文档
    └── archive/                       已归档的运行记录
        ├── crawler-2026-09-27.md      搬迁前的爬虫说明
        ├── crawler-homework-live-2026-09-25.md  2026-09-25 课程采集验证记录
        └── crawler-source-live-2026-09-26.md    2026-09-26 来源采集验证记录
```

## 安装与检查

需要 Node.js 20.19+、22.13+ 或 24+，以及 pnpm 10.17.1；所有命令均从 `crawler/` 运行。

```bash
cd crawler
pnpm install
pnpm lint
pnpm typecheck
pnpm test
```

## 公开活动爬取

支持北大计算机学院（`cs`）、生命科学学院（`bio`）和教育学院（`gse`）；默认只输出当前 `Asia/Shanghai` 时刻之后且时间可解析的活动。

```bash
pnpm crawler crawl all
pnpm crawler crawl cs,bio --as-of=2026-09-22T12:00:00+08:00
pnpm crawler reprocess <run-id>
pnpm crawler review <run-id>
```

原始页面写入 `data/raw/<source>/<run-id>/`，候选记录写入 `data/extracted/<run-id>.jsonl`，规范记录写入 `data/normalized/<run-id>.jsonl`，日志写入 `logs/<run-id>.jsonl`，审阅网页写入 `data/review/<run-id>.html`。

## 北大课程采集

将校园卡用户名和密码分别写在 `crawler/secret` 的前两行；从 `crawler/` 运行命令时默认读取该文件，且该文件会被忽略规则排除在 Git 之外。

```bash
pnpm pku:homework
pnpm pku:homework -- --term=26-27学年第1学期
pnpm pku:homework -- --all-terms
pnpm pku:homework -- --all-sections
pnpm pku:homework -- --format=recognition
```

默认只采集名称含“作业”、`Assignment` 或 `Homework` 的栏目，不读取课程首页公告；找不到作业栏目的课程会在 `coverage_warnings` 中记录课程名。`--all-sections` 恢复课程公告和全部内容栏目的采集。默认输出完整来源记录和兼容作业视图的 JSON，`--format=recognition` 输出 v0.2 JSONL 请求；结果写到标准输出，不会自动发送给识别服务。

课程列表中的原生作业链接会以只读方式打开：只读取“作业信息”中的名称、教师说明和“到期日期 / Due Date”。按“已提交视为完成”规则，原生页面的“复查提交历史记录 / Review Submission History”标题记录 submitted；提交历史详情仍标 partial / submission_history_only，不读取提交文件、作答、成绩或尝试时间，不推断是否需要重交；采集过程不会点击、填写或提交作业控件。列表 input_id / source_item_id 保持不变，课程 / 作业 ID 分别取自来源与原生详情链接，供 #110 后端在受控采集入口按账号匹配，不按标题匹配。

正文和作业详情中的同源教师 PDF / UTF-8 txt 附件会下载提取，引用逐项记录读取状态、原因和提取文字；仅页面装饰图标与菜单不影响完整性，真实图片和未读链接仍保留 partial。下载上限 5 MiB、15 秒、3 次同源重定向，PDF 在独立 Node 子进程解析，等待清理与退出后返回；解析超过 15 秒强制结束子进程、旧生代堆上限 128 MiB，子进程崩溃只将当前附件标 parse_failed。PDF 上限 50 页、提取文字上限 50,000 字符；图片、stencil mask、矢量图、注释、未知绘制操作、空白页或解析退化警告保留 partial，ZIP / Office / OCR / 外链与嵌套目录暂不支持。列表与详情合并超限时保留完整列表正文及详情 too_large 核对原因，其他记录继续导出；空占位容器不阻断后续教师正文，附件缓存按 URL 与格式区分。交接字段及下游边界见 [采集快照约定](../contracts/docs/teaching-source-snapshot-v0.2.md)。

教师容器自身或祖先属于私人表单、提交、尝试、成绩或导航区域时排除整块文字及引用；原生作业页允许已知 uploadAssignmentForm 外层表单，先移除私人区域和控件，再读取教师说明、标题与教师信息行中的日期。未找到教师说明时保留列表并标记详情未读，单条追加超限不会影响共享附件缓存；响应式图片的 srcset / picture 候选同样保留未读，PDF 文档级内嵌附件保留 partial_pdf，不递归提取。

日期支持教师信息区内的相邻值节点，遇下一字段标签（包括原生 label / th 和 ARIA 行列头）、行或说明边界即停止，父容器查找只穿过纯标签包装；表格只读取同一行紧邻的一个 td 值单元格。空值保持未读，明确“未设置 / Not set”表示无日期，读取失败或值冲突时提示人工核对。过滤后的“作业信息 / Assignment Information”标题仍可识别页面，嵌套说明只读取最外层匹配容器，独立说明按页面顺序保留。

### Course provenance handoff (#107 / #109 / #124)

Live collection retains `course-provenance-v1` facts in each source's `provenance`, and exports them in v0.2's open `upstream_hints.provenance`. The course ID comes from the actual listing URL. The observed course-list anchor text and sanitized launcher URL are accepted as metadata only when their origin and course ID match the listing. A single observed academic-term label is preserved as `term_label`; missing/conflicting labels remain unknown. The `term` CLI filter and collection date never supply this evidence.

A course label does not establish semester boundaries or prove that a particular notice/material is newly assigned. `applies_to` remains `unknown`; no dates or `current_assignment` authority are inferred. The actual platform must provide further applicability evidence before #109 can use a trusted semester to fill a missing year.

`upstream_hints.snapshot_id` versions the final source record, including body, references, read failures, publication, collection time and course metadata. The hash is a content identifier, not proof of account ownership and not #124's `verified_snapshot_digests`. Server must retain/retrieve the full controlled snapshot under the current application/teaching account, then build and validate the v0.4 entry and calculate its separate digest. No credentials or account identifiers are exported by this addition. Older synthetic/imported records without provenance retain their existing request shape.

New sources additionally export `teaching-body-v1` body evidence. Native teacher details successfully merged into that body are identified by `included_reference_urls`; they are represented once in v0.4. Attachment text stays in its own reference, and unread/failed native details keep the body incomplete. The legacy v0.2 `text` remains compatible and can still include attachment text.

### Teaching v0.4 export

Run `pnpm pku:homework -- --format=teaching-v04 --all-sections` to collect and export #124's v0.4 JSONL requests without calling a model or saving results. Each request contains at most 20 records and 2,000,000 UTF-8 bytes; each record's combined body/metadata/reference evidence stays within 100,000 characters. Records lacking stable course/item IDs, separate body evidence, or valid read facts are reported as `teaching_export_skipped` on stderr; other records continue and exit code 2 signals an incomplete export. Raw collection remains available with `--format=raw`.

The producer uses `contracts/schemas/teaching-request-v0.4.schema.json`, copied unchanged from #124 head `6312dca`; shared source cases replay both v0.2 and v0.4 output and Python semantic validation. It emits `course_term=null` until platform evidence provides actual semester dates and assignment applicability, and never emits a trust flag. The consumer must verify its account-bound full entry and compute the v0.4 entry digest separately. Old snapshots without separated body evidence are not upgraded by stripping attachment text or relabeling completeness.

`--format=teaching-v04` keeps the strict #127 evidence export and partial-export exit 2 behavior. `--format=teaching` selects teaching sections and emits one v0.4 request per source; `--format=teaching-capture` wraps the same verified evidence with bound native assignment state for the server. Both integration formats fail explicitly on invalid evidence or duplicate identities; course labels never replace body/attachment evidence.

教师附件支持同源 Blackboard `dt-content` 和通知栏 `dt-announcement` 路径，含无扩展名的 xid 文件地址（格式由附件标签识别）；仍拒绝私人提交文件与外站地址。

单条采集诊断：`pnpm pku:homework -- --format=raw --source-page=<已保存通知所在页URL> --source-item=<原始条目ID> --course-label=<课程名> --section-label=<栏目名>`。四个参数必须同时提供，不能与 `--all-sections` / 学期扫描组合；仅读取指定页面中的该条通知及其引用，不调用模型、不写账号数据，原始输出只保存到本地忽略目录。

公开活动已接入网页“导入 → 公开活动”，后台调用 `public-import-cli.ts <cs|bio|gse> <临时输出目录>`；当前仅返回未来活动，最多 6 页，页面和跳转均限制在所选网站来源内，原始页面仅保存在临时目录并在结束时移除。

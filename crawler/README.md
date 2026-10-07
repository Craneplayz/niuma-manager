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
│   ├── cli.ts                        公共事件爬取、重处理与审阅入口
│   ├── crawler.ts                    调度抓取、解析、过滤和存储
│   ├── index.ts                      导出爬虫公共接口
│   ├── logger.ts                     写入结构化运行日志
│   ├── normalizer.ts                 将候选事件规范化
│   ├── review-report.ts              生成事件审阅网页
│   ├── pku-homework-cli.ts           北大课程采集命令行入口
│   ├── pku-homework.ts               登录并采集课程内容
│   ├── pku-attachments.ts            限定同源教师附件下载并提取 PDF / UTF-8 文本
│   ├── pku-assignment.ts             只读解析作业详情、到期日期与提交状态
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
│   ├── adapters.test.ts              测试公开来源适配器
│   ├── crawler.test.ts               测试抓取与重处理流程
│   ├── fetcher.test.ts               测试 HTTP 获取器
│   ├── normalizer.test.ts             测试事件规范化
│   ├── pku-homework-cli.test.ts       测试课程采集命令行
│   ├── pku-homework-html.test.ts      测试课程页面解析
│   ├── pku-homework.test.ts           测试课程采集规则
│   ├── pku-attachment-download.test.ts 测试流式大小限制、重定向及鉴权边界
│   ├── pku-attachments.test.ts        测试附件读取、大小限制和失败状态
│   ├── pku-assignment.test.ts         测试作业详情页的安全只读解析
│   ├── pku-navigation.test.ts         测试课程页面导航
│   ├── pku-recognition.test.ts        测试识别请求转换
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

正文和作业详情中的同源教师 PDF / UTF-8 txt 附件会下载提取，引用逐项记录读取状态、原因和提取文字；仅页面装饰图标与菜单不影响完整性，真实图片和未读链接仍保留 partial。下载上限 5 MiB、15 秒、3 次同源重定向，PDF 上限 50 页、提取文字上限 50,000 字符；图片、stencil mask、矢量图、注释、未知绘制操作、空白页或解析退化警告保留 partial，ZIP / Office / OCR / 外链与嵌套目录暂不支持。列表与详情合并超限时保留完整列表正文及详情 too_large 核对原因，其他记录继续导出；空占位容器不阻断后续教师正文，附件缓存按 URL 与格式区分。交接字段及下游边界见 [采集快照约定](../contracts/docs/teaching-source-snapshot-v0.2.md)。

教师容器自身或祖先属于私人表单、提交、尝试、成绩或导航区域时排除整块文字及引用；原生作业页允许已知 uploadAssignmentForm 外层表单，先移除私人区域和控件，再读取教师说明、标题与教师信息行中的日期。未找到教师说明时保留列表并标记详情未读，单条追加超限不会影响共享附件缓存；响应式图片的 srcset / picture 候选同样保留未读，PDF 文档级内嵌附件保留 partial_pdf，不递归提取。

日期支持教师信息区内的相邻值节点，遇下一字段标签（包括原生 label / th 和 ARIA 行列头）、行或说明边界即停止，父容器查找只穿过纯标签包装；表格只读取同一行紧邻的一个 td 值单元格。空值保持未读，明确“未设置 / Not set”表示无日期，读取失败或值冲突时提示人工核对。过滤后的“作业信息 / Assignment Information”标题仍可识别页面，嵌套说明只读取最外层匹配容器，独立说明按页面顺序保留。

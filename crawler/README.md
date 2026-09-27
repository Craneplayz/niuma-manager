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
pnpm pku:homework -- --format=recognition
```

默认输出完整来源记录和兼容作业视图的 JSON，`--format=recognition` 输出 v0.2 JSONL 请求；结果写到标准输出，不会自动发送给识别服务。

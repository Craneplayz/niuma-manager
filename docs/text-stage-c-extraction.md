# 阶段 C：识别 skill、日期与证据处理

关联 #5、#6，依赖已合并的阶段 B（PR #12）。本阶段交付共享识别规则及“Agent 任务草稿 → 规范候选”的程序；真实 DeepSeek/Codex 调用和 Codex 自动发现 skill 的验证仍属于 #9。未进行真实自然语言识别或准确率验收，不因此关闭 #5、#6。

## 现在能做什么

程序读取同一份 skill 及其引用资料，作为 `DraftClient.generate(instructions, record_json, settings, attempt)` 的指令参数；本条输入和给定上下文作为独立 JSON 参数。Agent 负责判断任务、拆分行动和选择原文证据，程序负责验证草稿、复制来源、计算日期和形成最终输出。整个接口可注入可信客户端，但没有默认网络客户端、模型地址或密钥。

已实现零到多候选、无日期保留、前置步骤、课程证据、上下文引用、变更对象引用、延期/取消语义字段及待核实原因。只有当前正文的证据才可触发候选；仅有上下文旧任务的草稿会被拒绝。来源链接、平台和原记录 ID 从输入复制，不接受模型生成值。缺失来源链接允许为空。

日期支持完整年月日、今天/明天/明晚/后天、本周/下周的具体星期、N 小时后及数字时刻。相对日期只用被引用消息自己的可信发布时间；仅日期不补 23:59；活动时间作为参考必须标为推断。冲突日期保留候选；缺年份、缺基准、格式不支持或日期无效时保留任务并提示核实。

## 文件怎么分工

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](../skills/text-task-extraction/SKILL.md) | 原创识别规则、触发范围与失败处理；规则版本 0.1.0。 |
| [draft-schema.md](../skills/text-task-extraction/references/draft-schema.md) | 模型应返回的中间草稿格式；对外仍是原来的 v0.1 格式。 |
| [examples.md](../skills/text-task-extraction/references/examples.md) | 加群、多任务、无日期、延期、取消和注入内容等正反例。 |
| [extraction.py](../ddl_text/extraction.py) | 加载共享规则、调用可替换客户端、校验证据并生成候选。 |
| [dates.py](../ddl_text/dates.py) | 根据原文和发布时间确定日期，保留不确定性与冲突。 |
| [test_extraction.py](../tests/test_extraction.py) | 日期、来源、上下文、草稿校验和命令行回归测试。 |
| [text-stage-c-request.json](examples/text-stage-c-request.json) | 合成通知输入。 |
| [text-stage-c-drafts.json](examples/text-stage-c-drafts.json) | 与该输入严格匹配的人工 Agent 草稿，供离线测试。 |

原有入口、批量处理、超时、配置等文件见[阶段 B 文件速查](text-stage-b-runner.md#文件用途速查)。

## 如何运行与检查

在仓库根目录使用 Python 3.9 或更新版本，无需额外运行依赖：

```text
python -m ddl_text --input docs/examples/text-stage-c-request.json --drafts docs/examples/text-stage-c-drafts.json
python -m ddl_text --input docs/examples/text-stage-c-request.json --drafts docs/examples/text-stage-c-drafts.json --format json
python -m unittest discover -s tests -v
python scripts/check_text_contract_examples.py
git diff --check
```

演示应有两项任务：加群的参考时间为 `2026-09-16T18:40:00+08:00`，并标为推断；学习计划无日期，保留并标待核实。这里真正执行了日期计算、证据校验和结果生成，但任务草稿由人工提供，不代表模型自动读懂正文。输入改动后必须同步制作匹配的合成草稿；不能用旧草稿冒充新文本识别结果。`--drafts` 不能与 `--replay`、`--demo` 或真实模式混用。

2026-09-24，Windows / Python 3.9.12：51 项行为测试和 17 个阶段 A 样例检查通过。skill-creator 的格式检查通过（Windows 下使用 `python -X utf8` 运行其 `quick_validate.py`）。格式检查不等于模型行为验证。测试确认指令与正文分开传入、无默认网络调用；这不证明未来模型能抵抗全部提示注入。

PR #13 评审修正：未知时间与冲突保留引用的时间类型及一致的来源时区；无日期任务保留当前来源时区。前置步骤须逐字包含在已核对的 evidence 引文中，缺少引用时按无效草稿报错。另将“明晚12点”等跨日边界及矛盾时段保留待核实，避免错误换算成中午。对外字段不变，契约说明明确 reference 也可以处于 unknown/conflict 状态。

阶段 C 的离线实现已完成以上修正；PR 仍需队友评审。完整语义识别与 skill 实际加载验收仍依赖 #9 的真实接入，不能以离线测试通过代替。2026-09-24 用户确认尚无服务，暂按未来 API 接入准备。

## 接入时需完成的工作

- 通过 `ExtractionAdapter(client)` 接入 `process_request`。客户端必须可由 multiprocessing spawn 导入，并在可信环境中遵守 `DraftClient` 协议；不要把客户端定义在局部函数中。沿用阶段 B 的超时与有限重试，调用脚本需有 `if __name__ == "__main__":`。
- `instructions` 作为固定识别规则，`record_json` 作为不可信待分析数据；真实协议应保留此区别，禁用无关工具并验证模型返回草稿协议。传入客户端的 model/endpoint 等参数仍由原 `Settings` 提供。
- skill 以仓库内 `skills/text-task-extraction` 目录作为可携带源文件，Python 适配器已经读取其内容。当前没有安装到个人全局配置，没有验证 Codex 自动发现或实际执行；实际加载位置和工具权限在 #9 的可信运行环境中确认，不能把“文件已创建”写成“Codex 已加载”。
- Agent 对任务含义、前置步骤、截止语义和对象匹配的判断仍需真实模型评测。证据逐字匹配只能证明引用存在，不能证明模型理解正确，也不能保证没有漏掉任务。
- 目前日期表达是保守子集：不自动补年份，不自动解析任意自然语言区间、分散日期片段或带偏移的 ISO 时间戳。上海现代日期和 UTC 不依赖系统时区库；其他地区需要可用的系统 IANA 时区数据，否则标待核实。历史上海日期与夏令时歧义也不猜测。

本阶段的 skill 与读取该 skill 的代码组成一个可独立评审的目标，放在同一 PR，分别以规则和程序行为提交；避免规则与实现版本错配。最终清单增删改仍由下游负责。

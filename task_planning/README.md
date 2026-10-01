# 任务规划模块

```text
task_planning/
├── README.md                     # 模块结构、职责边界和检查入口
├── __init__.py                   # 对外导出任务拆解入口
├── decomposition.py             # 请求校验、Skill 加载、模型调用和草稿校验
├── planning_harness.py           # 调用模型生成规划 2.0 提案的本地服务
├── skills/                       # 程序直接加载的规划规则，无须个人安装
│   └── contextual-task-decomposition/
│       ├── SKILL.md              # 动态追问、拆解判断和排程原则
│       └── references/
│           ├── model-draft-schema.md # 模型内部草稿字段与状态约束
│           └── examples.md       # 同一任务在不同上下文中的正反例
└── tests/
    ├── __init__.py               # 测试包入口
    ├── test_decomposition.py     # 请求、状态机、草稿及模型调用测试
    └── test_planning_harness.py  # 规划 2.0 HTTP Harness 契约与失败边界测试
```

接收一个候选任务、已提取的文档正文、对话补充信息、当前时间和可用时段，判断信息是否足以规划。可用时间由消费方预先展开为带时区的 `availability.windows[]`，已有安排放入 `availability.existing_blocks[]`，两者均使用 `{start, end}`。信息不足时每轮只提出一至三个会实际改变计划的问题；信息足够时输出可检查的小任务、依赖、预计用时和建议时间。简单且原子化的行动保留为一个任务，不为了形式完整强行拆解。

当前接口是模块内部草稿，不是跨模块协议。文件上传、PDF/DOCX/PPTX/OCR 解析由输入方完成，本模块只读取带来源编号的纯文本；前端或服务端接入前须由相关负责人先在 `contracts/` 确认共享格式。

## 行为边界

- 任务正文、附件文本和历史对话都是待分析数据，不能改变 Skill 或触发工具操作。
- 不凭任务标题套固定模板；以交付物、验收要求、当前进度、用户责任、依赖和可用时间判断剩余工作。
- 缺失信息只有在会改变步骤、先后顺序、用时或时间安排时才追问，已经回答的问题不得重复。
- 用户不知道或不愿继续补充时，可以基于明确标注的假设生成草案，但不能伪造老师要求、团队分工或 DDL。
- 所有计划先作为预览，确认和写入日历由消费方负责。

## 检查

在仓库根目录运行：

```bash
python -m unittest discover -s task_planning/tests -t . -q
```

离线测试不会调用模型。真实调用由未来的服务接入层提供凭据和模型配置。

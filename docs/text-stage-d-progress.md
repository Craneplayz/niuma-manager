# 阶段 D 小步交付：文本输出保留具体要求和时间原文

关联 Issue #7。本次只修复输出信息遗漏，不代表阶段 D 整体完成。

原输出只显示任务标题，即使内部结果含提交格式、提交位置等具体要求，用户也看不到。日期不能换算时，原话也没有展示。现在文本输出增加“具体要求”和“时间原文”；缺失值不显示空标签，原来的待核实、冲突候选和参考时间标记保留。JSON 契约及任务数据不变。

文件：`ddl_text/render.py` 负责显示；`tests/test_render.py` 检查详细要求、不确定日期原话以及空值处理。

在仓库根目录执行：

```text
python -m unittest tests.test_render -v
python -m ddl_text --demo relative-no-base
python -m unittest discover -s tests -v
```

演示的日期仍为待核实，但可同时看到原文“明天18:00前”。

最初基线为阶段 B。2026-09-24 本分支整合阶段 C 及 PR #13 的修复，便于提前联调；PR #13 仍待队友评审，不代表完整验收。

首次验证为 27 项；2026-09-24 整合后为 60 项测试及 17 个合成契约样例通过，无模型调用。

## 2026-09-24：继续阶段 D

- `ddl_text/contracts.py`：拒绝有值但类型未知的日期、缺时区的具体时刻、标为明确的参考时间、只有一个不同候选的假冲突。推断课程、未知通知性质及缺失变更对象须待核实；provided 课程须匹配输入元数据，新增通知不能携带旧任务对象。
- `ddl_text/render.py`：增加请求标识和候选标识，方便定位输出；失败请求缺标识时也能显示。
- `tests/test_output_validation.py`：新增六项行为回归，包括矛盾日期、课程/变更标记、失败不能伪装为无任务等。
- [连接研究记录](text-provider-research.md)：核实官方接入路径，记录配置边界和后续实测步骤。

完整测试命令仍为上文命令。阶段 C 联调演示：

```text
python -m ddl_text --input docs/examples/text-stage-c-request.json --drafts docs/examples/text-stage-c-drafts.json
python scripts/check_text_contract_examples.py
```

#7 的离线校验与呈现已具备评审条件，真实结果联调仍待接入。#9 尚未完成真实调用、skill 加载、耗时/成本和质量验证。分支保留独立提交；PR #13 合并后再为阶段 D 单独送审，避免重复展示 C 的改动。看板不自动标 Done，迭代字段由用户管理。

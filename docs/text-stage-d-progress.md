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

基线为已合并的阶段 B（PR #12），不依赖待评审的阶段 C（PR #13）。阶段 C 合并后，这项显示修复可保留在原有 `render_text` 中。

本次验证：Windows / Python 3.9.12，新增 3 项显示回归测试与原有 24 项测试通过。尚待后续完成更全面的输出语义校验、真实 DeepSeek/Codex 接入研究及实际验证。为控制本轮消耗，本次仅本地提交，不创建 PR 或更新线上看板。

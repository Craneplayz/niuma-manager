只输出一个 JSON 对象，使用下列字段名。三个状态都含 status、summary（简短说明剩余任务）、confidence（0–1）、facts（实际使用的 passages 编号，可为空）。假设存在时添加 assumptions 字符串数组。

需要追问的形状为 `{"status":"needs_clarification","summary":"…","confidence":0.6,"facts":[1],"questions":[{"prompt":"…？","why":"回答将决定哪些步骤","options":["…","…"]}]}`。列 1–3 个问题。options 可省略。

可执行的形状为 `{"status":"ready_to_plan","summary":"…","confidence":0.9,"facts":[1],"goal":"验收成果","strategy":"结合进度、为什么按这个顺序","steps":[{"title":"具体行动","outcome":"能检查的完成标准","minutes":30,"refs":[1],"after":[]},{"title":"下一步","outcome":"…","minutes":15,"refs":[1],"after":[1]}]}`。

no_split 同样返回 steps，但恰好 1 步。ready_to_plan 至少 2 步。after 引用本计划前面步骤序号（第一步为 1），空数组表示可独立开始。每个 refs 至少引用一个与该步骤相关的已给 passage。基于个人假设的步骤加 `assumed:true` 并写明 assumptions。可附 warnings 字符串数组。在明确要求给具体时段而且有可用时间窗时，步骤可选 start/end（带偏移的 ISO 日期时间，长度须等于 minutes）；其余情况省略。不要输出跟踪编号、来源路径、引文、复杂度或数据库字段，这些由程序生成。

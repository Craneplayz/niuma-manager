# 时间提取参考与本项目取舍

查阅日期：2026-09-25。以下是公开原始来源；仅借鉴设计思想并自行实现，没有安装第三方 skill、MCP 服务或复制其代码。它们不是对本项目准确率的背书。

- [Temporal Cortex datetime skill](https://github.com/temporal-cortex/skills/blob/main/skills/temporal-cortex-datetime/SKILL.md)：借鉴先确定时间基准和时区，再用确定性工具换算的顺序。本项目使用每条通知的元数据，不采用系统“现在”，也不引入其 MCP 安装步骤。
- [fcalendar-skill](https://github.com/youngfreeFJS/fcalendar-skill/blob/main/SKILL.md)：查阅其中文时间提取、显式 `--today` 基准和忠实保留计算结果的规则。借鉴把原始短语和基准分开、让确定性代码负责计算的方式；本项目仍输出 JSON，不采用它的展示格式、系统日期默认值或依赖安装步骤。不增加农历、节假日、调休等尚未验证的支持。
- [extract-date 的省略年份说明](https://github.com/gajus/extract-date#date-resolution-without-year)：这是日期提取库而不是 skill；参考其“省略年份需要有可说明的选择策略”和“完整日期优先匹配”的设计。本项目采用日历距离最近的相邻年份，而非该库的月份阈值算法。

项目规则：明确年份优先；否则以被引用消息的可信发布时间为基准，只有月日缺年时可退回该消息的采集时间，并标记推断和依据。最近日期可能刚过期，不强制滚到未来；不存在的日期不矫正成其他月日。只有日期不补时刻，事件时间不直接升级为截止时间。来源原话保持不变。

这些规则维护在 SKILL.md，程序在 `ddl_text/dates.py` 执行，回归位于 `ddl_text/tests/test_extraction.py`。本参考页不随私人文本发送给模型；实际模型调用加载 SKILL.md、草稿协议和正反例。

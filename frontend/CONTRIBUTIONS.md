# 前端贡献与设计来源

仓库前端的历史基础来自 **潘勇圳（Yongzhen23）** 的 [niuma-frontend](https://github.com/Yongzhen23/niuma-frontend)，参考提交 `18d5421`。此来源记录继续保留；历史整合说明见 [归档](docs/archive/2026-09-27-contributions.md)。当前界面已重写为任务与导入两个主要区域，历史截图中的其他功能不属于当前产品界面。

当前信息组织参考以下官方产品资料：

- [Todoist 项目管理](https://www.todoist.com/zh-CN/project-management)：任务列表、查询与逐步处理的组织方式。
- [Todoist 任务详情](https://www.todoist.com/help/todoist/features/use-the-task-view-to-manage-tasks-in-todoist-eDeRDO0C)：从清单进入详情，集中处理任务属性。
- [Todoist 快捷键与侧栏操作](https://www.todoist.com/help/todoist/features/use-keyboard-shortcuts-in-todoist-Wyovn2)：导航与任务操作的可达性。
- [滴答清单月视图](https://help.dida365.com/articles/6950640298334617600)和[周视图](https://help.dida365.com/articles/6950641140068515840)：用时间范围切换辅助查询任务。
- [滴答清单功能介绍](https://dida365.com/features?language=zh_CN)：清单和日历互补的产品组织。

本项目根据自己的范围实现截止日期日历；上述资料是交互与信息组织参考，不代表复制完整产品功能。当前视觉以用户确认的黑白文字层级、留白和少量雾蓝选中态为准，规格见 [设计说明](docs/design.md)。

小组仓库维护账号持久化、教学网采集、文本识别、候选核对、任务与证据保存。前端的工作区适配保留既有任务元数据；服务端契约与业务实现的职责保持独立。维护分工见 [维护指南](docs/maintenance.md)和[小组协作规范](../CONTRIBUTING.md)。

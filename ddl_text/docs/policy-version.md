# 文本规则版本与重识别交接（#112）

`text_policy_manifest` 为内置真实文本适配器生成可持久化的版本与组成清单；本步不新增 v0.2 字段、HTTP 路由或数据库表，不触发重识别。

```python
from ddl_text.policy_version import text_policy_manifest

# settings 必须是本工作进程实际调用识别器的 Settings，provider 与真实客户端一致。
policy = text_policy_manifest(settings, provider='qiniu')
# server 在创建运行时保存 policy['version'] 和整个 policy，再关联识别结果。
# 输出仍是既有 process_request(request, adapter, settings) 的 v0.2 JSON。
```

- 版本覆盖实际加载的文本 skill/草稿说明/示例，以及时间、分类、证据、输出校验、调用与重试代码；逐文件 SHA-256 清单便于解释差异，文件缺失直接报错。
- 实际模型 ID、服务地址、超时、重试、契约版本、Python/系统/tzdata 及可选 `deployment_revision` 参与哈希；密钥、输入正文、账号、运行时间、Git 分支和文档不参与，也不读取 `.env`。
- UTF-8 BOM、CRLF/LF、仓库路径与密钥轮换不造成代码版本变化；模型/规则变化会变化，不能拿进程启动时刻充当版本。
- 在固定发布目录中、工作进程启动时生成一次，再用于该工作进程执行的运行；部署改规则后重启进程。不要运行中改源文件再单独刷新哈希，否则已导入的代码与磁盘文件可能不一致。
- 服务商在相同模型 ID 下切换权重、系统时区库更新或自定义客户端不能仅靠源码哈希完全追踪；已知发布变更用 `deployment_revision` 标记。当前仅内置 qiniu/codex 的 v0.1/v0.2 真实文本流程支持本标识，图片/PDF 和自定义适配器需独立版本配置，不能冒用。

## 消费方接入要求

1. 版本属于 server 的运行元数据；不往现有请求/结果随意添加字段，需要新外部字段时先经过 contracts 审查。记录实际执行时的版本，旧记录没有版本就保持未知，不能写成当前版本。
2. 内容摘要、账号与稳定来源 ID 继续用于增量同步；版本变化只标记可能需重新识别，不自动无限重放历史消息。
3. 显式重识别涵盖 success/no_tasks/failed；新识别失败保留旧结果。未处理候选可更新，已确认、忽略、编辑或完成的决定须保留。
4. 版本不是运行 ID 或幂等键；消费者仍需运行序号/状态迁移，防止旧运行迟到覆盖新运行。候选 ID 带位置，同条来源多任务重新排序时不能当永久任务身份。

## 回归输入

`ddl_text.tests.reprocessing_cases.cases()` 提供 9 组完全合成案例：旧成功但误审核、旧 no_tasks、新识别失败、5 种用户处理状态、跨账号同来源 ID，以及多任务重新排序；`replay(case, 'before'/'after')` 返回经真实编译和校验的 v0.2 结果，无网络或数据库写入。

外层 `account_alias/user_decision/expected_consumer_action` 只是内部测试说明，不能直接作为接口协议；before/after 草稿模拟旧新模型回答，不是两套生产规则，也不能作为真实语义评测。保留用户决定、账号隔离、并发/重启和 UI 行为需由后端在临时数据库中进一步验证，本模块没有把这些验收标为完成。

```powershell
python -m unittest ddl_text.tests.test_policy_version -v
```

#108/#109 的上下文字段等待契约草案和 #107；#110 的 submitted 处理属后端，#111 另见 PR #113；以上合并并接通后，还需补相应端到端迁移测试，不能用本组通用样例替代。

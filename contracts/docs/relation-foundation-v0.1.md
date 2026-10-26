# 跨课程关系基础 v0.1

本阶段提供证据快照、关系契约和待验证提案审计；不检索候选、不调用模型、不合并任务、不自动完成、不改变现有课程上下文隔离。

## 身份与来源

- 来源身份绑定登录用户、教学网账号、原生课程、学期、input_id 和原生 content_id；标题、模型输出顺序和采集快照不参与身份分配，内容变化只改变版本。
- 行动身份使用数据库持久任务ID或候选ID，不使用模型候选序号；候选保存成任务通过已有 recognitionHistory 记录显式 promotion，保留两个节点身份，不按标题猜成同一行动。
- 已提交但没有任务的原生来源仍进入快照；忽略、已消费候选保留原状态，已存在任务保守标记 protected，人工提交控制也进入保护和版本指纹。
- 原生课程/内容/学期身份变化不继承旧行动；来源选择没有覆盖任务全部历史时标记 partial_task_provenance，禁止把该行动写入关系单元。
- 来源保留原文URL、课程名、快照、学期起止、正文及附件；正文 part_id=body，来源元数据=metadata，附件=ref:原ref_id，附件保留名称和校验后的 SHA-256。
- 每个片段保留读取状态和内容指纹；最多24000字符，截断标为 partial，不把不可读部分或借来的 course_context 当作当前来源证据。
- 引用 offset 使用 Unicode 码点，quote 必须与绑定片段完全相同；引用校验只证明出处，不证明两项作业语义相同。

## 关系与提交覆盖

| kind | 含义 | 交付单元 |
| --- | --- | --- |
| duplicate | 多来源重复描述同一交付 | 恰好1个 |
| complement | 内容与提交要求互补 | 恰好1个 |
| bundle | 一个提交批次包含多个部分 | 至少2个 |
| independent | 有关联但应保持独立 | 至少2个 |

每个来源有 content/submission/correction/reference 角色及自己的证据；每个交付单元记录稳定ID、渠道、行动引用和证据。同章书面、POJ、MOOC可以用 independent 的不同单元表达；模型置信度不构成操作权限。

coverage 显式记录某个原生提交来源覆盖哪些交付单元，extent 为 complete/partial/unknown；同一入口只能出现一次。coverage_summary 分开返回覆盖范围、未覆盖范围、可信采集状态和来源核对状态，不累加多个不明覆盖范围，application_allowed 永远为 false。本阶段既不提升模型提供的 submitted，也不把“结构合法”升级为“语义已确认”。

deadline 可为空或仅日期；有时刻时必须明确给出，均附原文证据。日期证据的语义判定属于后续阶段，不自动采用较新或较早日期。缺少学期、学期不同或起止窗口冲突时可以查看快照，但不能保存关系提案。

单元ID由后续受控提案生产者分配并随提案持久保存；本阶段不声称已实现跨模型运行的交付身份匹配，也不按标题/章节为新单元去重。

## 接口与持久化

登录后 POST `/api/recognition/relation-context`：

```json
{"source_account_id":"已有教学网来源账号ID","input_ids":["来源1","来源2"]}
```

每次选择1至20条现有来源、最多200个行动；返回 `context` 根，包含证据和 revision。接口只读，使用同一数据库读事务，不读取任意URL/路径，不返回密码、会话或密钥；外部账号/来源统一404，未登录401，格式、范围超限返回有明确code的400。鉴权由正常应用会话处理，没有测试环境专用入口。

内部 `record_proposal` 消费 `relation` 根，在显式事务中重新检查账号、当前完整快照及证据，并原子写入提案和审计；没有开放HTTP写入或apply接口。重复operation_id和相同内容返回原回执，不重写；同键不同内容、过时依据拒绝。回执是当时记录，不能冒充当前可执行状态。

审计保留 actor、操作ID、前后状态、关系摘要和完整证据快照；所有记录都处于 proposed，task_mutations=0。`read_proposal` 同时校验用户和当前教学网账号；不提供改写历史审计的方法。关闭/撤销合并以及实际任务写回属于后续阶段。

新增两个账号范围存储表 relation_foundation_proposals/events；现有账号测试清理按 user_id 清除。没有定时作业、模型开销、前端页面或新增环境配置要求；不依赖尚未合入的 #213，即可在当前开发分支使用，且不会放宽 #213 的课程隔离。

## 验证

仓库根目录、PYTHONPATH包含根目录和server：

```sh
python -m contracts.relation_foundation --check
python -m unittest contracts.tests.test_relation_foundation server.test_relation_foundation -v
```

共享合成样例见 `examples/relation-foundation-v01.json`。授权数据先按单一用户导出，不包含users、sessions或可用凭据；source_credentials.secret必须为not-exported占位。真实快照回放在新的 test 数据目录创建账号，注册邮箱发送用测试替身，其余使用真实HTTP、鉴权、SQLite、来源和已保存附件，不调用模型或教学网：

```sh
NIUMA_ENVIRONMENT=test python -m server.run_relation_foundation_replay \
  --snapshot /private/authorized.sqlite3 --database /new/test/niuma.sqlite3 \
  --scenario /private/expected.json --output /private/new-report.json
```

expected.json由独立预期给出 input_ids、material_input_ids、submission_input_id、expected_submission_state，以及每个input_id对应的原文quotes；它不进入业务实现。脚本核对每条来源的原文、身份、学期、URL、附件原字节哈希、重复查询稳定性及业务表未改变，再验证多材料/单提交入口关系保存、重放与审计。临时HTTP服务在finally关闭；私有快照和报告不得提交GitHub。

本阶段的真实数据通过只能证明原有数据与关系基础接口一致，不能证明模型会找到关系、未来自动合并正确或新一轮采集成功；后续须单独完成这些验收。

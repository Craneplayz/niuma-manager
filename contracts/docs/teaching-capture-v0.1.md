# 教学网受控采集信封 v0.1

`teaching-capture/0.1` 仅用于后端启动的已登录爬虫输出，不是浏览器上传接口；`request` 是一条既有 v0.4 输入，`assignment` 为 null 或原生作业页取得的 course_id/content_id/state/basis，身份须与该输入一致。独立根 Schema 为 `schemas/teaching-capture-v0.1.schema.json`，跨字段入口为 `contracts.teaching_capture.check_capture`。

submitted 的 basis 必须是 review_submission_history_title，active 对应 active_assignment_page，unknown 对应 unknown；普通栏目、正文中的“已提交”、PDF、模型判断不具备提交状态权限。缺少原生作业身份时输出 null，不猜测。

来源状态的副作用统一服从 [submission-policy/0.1](submission-policy-v0.1.md)：`submitted` 只证明原生页面的提交状态，不能证明整份正文及附件都属于同一个提交行动；只有服务端永久行动映射或人工确认支持 `submission_only` 时，才可整条跳过模型。

本 PR 的运行端尚无上述永久行动映射，故已提交来源一律按 `scope=unknown` 处理：继续识别并保留同来源全部行动，以 `submission_scope_unknown` 提示人工核对，不批量撤候选、不自动入库，也不自动完成已有任务。标题、作业栏目和模型判断不能提升关联权限；后续 #110 接入映射后才可只处理明确关联的提交行动、保留其他/未知行动。

后端按账号绑定保存原生状态和各版本来源关系，拒绝迟到采集覆盖较新的状态。submitted 后出现 active/unknown，持久设置 `review_pending`；包括再次 submitted 在内的后续观察均暂停模型及自动入库，保留现有候选、结果和用户决定，重启不解除。既有候选仍可由用户明确确认、忽略或撤回忽略，但这些操作不等同于解除来源核对；解除操作须由后续 #110 的 `resolve_source_review` 运行端按共享契约接入，本 PR 不提供隐式解除入口。

升级时仅恢复本 PR 旧实现以 `source_submitted` 自动撤出的、具有账号/来源绑定的候选，增加版本并在恢复表保存原撤出原因；不改原模型结果，不恢复用户手动忽略/已应用的候选，也不处理无绑定的旧记录。

原始 v0.4 及 v0.2 导出仍可用，但不能凭纯文本取得跳过权限；HTTP 运行请求仍仅接受服务器签发的 request_id。示例由 crawler 与 server 的合成测试贯通验证，修改 Schema 后运行 `python -m contracts.build_teaching_v04 --check`。

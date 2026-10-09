# 教学网受控采集信封 v0.1

`teaching-capture/0.1` 仅用于后端启动的已登录爬虫输出，不是浏览器上传接口；`request` 是一条既有 v0.4 输入，`assignment` 为 null 或原生作业页取得的 course_id/content_id/state/basis，身份须与该输入一致。独立根 Schema 为 `schemas/teaching-capture-v0.1.schema.json`，跨字段入口为 `contracts.teaching_capture.check_capture`。

submitted 的 basis 必须是 review_submission_history_title，active 对应 active_assignment_page，unknown 对应 unknown；普通栏目、正文中的“已提交”、PDF、模型判断不具备提交状态权限。缺少原生作业身份时输出 null，不猜测。

当前测试集成按用户要求实施**原生已提交作业来源跳过**：首次及后续同步不把该原生作业记录发送给模型；同账号、同来源已绑定的未处理候选退出活动区，保留原始结果与撤出原因。作业挂靠的题目与附件随该来源一起跳过；其他独立课程通知、教学内容来源继续识别。

这是一项来源选择策略，不等于 `submission-policy/0.1` 的逐行动完成实现：不根据候选标题自动完成已入库任务，不改用户忽略、删除或已应用的决定。若一条已提交作业来源仍附带独立的后续行动，应将该行动从独立通知/手工输入补入；需要保留混合来源内各行动的版本仍须按 #110 永久行动映射接入，不能宣称本版本已经支持。

后端按账号绑定保存原生状态和各版本来源关系，拒绝迟到采集覆盖较新的状态。识别及入库前均重新核对当前状态；submitted 后回到 active/unknown 时，新识别候选强制人工复核，不能自动恢复为未完成任务。旧数据缺少账号/来源绑定时不按课程或标题批量清理，需重新采集建立可信关系。

原始 v0.4 及 v0.2 导出仍可用，但不能凭纯文本取得跳过权限；HTTP 运行请求仍仅接受服务器签发的 request_id。示例由 crawler 与 server 的合成测试贯通验证，修改 Schema 后运行 `python -m contracts.build_teaching_v04 --check`。

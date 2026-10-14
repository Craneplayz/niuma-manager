# 公开活动导入接口 v0.1

`public-import/0.1` 将现有计算机学院、生命科学学院、教育学院采集器接到导入页。仅支持目录内的固定公开来源，不接受任意 URL，不使用教学网凭据或模型服务。

| 请求 | 输入 / 输出 | 行为 |
| --- | --- | --- |
| GET `/api/sources/public/catalog` | catalog | 返回三个可选来源。 |
| POST `/api/sources/public/runs` | start → run | 登录用户提交 `operation_id` 和 `source_id`，202 返回后台采集记录；同操作编号复用原记录，不重复采集。 |
| GET `/api/sources/public/runs` | runs | 返回当前用户最近十次采集及结果。 |
| POST `/api/sources/public/runs/{run_id}/import` | import → imported | 提交所选 `event_ids` 和 `tasks_version`，在同一事务中创建通知及去重标记。 |

所有路由使用站点登录会话。采集每用户同时一轮、全服务同时两轮；来源限定固定 HTTPS 域名，每轮至多六页、100 条结果，150 秒超时。进程重启把未完成轮次标为失败，用户可重新获取。`pages_failed` 大于零表示部分网页未取得，不应声称覆盖完整来源。

采集结果先预览，由用户勾选后入库；每个账号按稳定活动 ID 去重，重复导入不会新增，也不会复活已经删除的活动。活动以 `content_type=notice` 保存，`due=null`；活动起止时间保留在 `publicEvent` 和描述里，不推断成截止时间。任务版本过期返回 409，前端刷新后重新确认；不存在或跨账号记录返回 404。

`schemas/public-import-v0.1.schema.json` 为自包含 bundle；独立根用 `#/$defs/capture`、`start`、`run`、`runs`、`catalog`、`import`、`imported` 选择。Python 使用 `contracts.public_import.check(kind, value)`，额外检查活动 ID 唯一性、运行状态与结果一致性、来源一致性。生产者是 `crawler/src/public-import-cli.ts`，消费者是 `server/public_sources.py` 和 `frontend/src/public-sources-ui.js`；共享样例为 `examples/public-import-v01.json`。

```powershell
python -m contracts.public_import --check
python -m unittest contracts.tests.test_public_import -q
```

上述检查只验证协议与合成数据，外站网络、robots 规则和页面改版仍须在目标环境验收。

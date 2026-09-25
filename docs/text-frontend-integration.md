# 文本识别与前端对接（v0.2）

最新状态（2026-09-25）：前端 PR #15/#16、文本 API PR #14、七牛接入 PR #17 已合并。七牛真实联调和网页候选确认已通过，详见[阶段 E 验收](text-stage-e-review.md)；有限样例成功不代表长期延迟已验收。前端目录为 `frontend/`。

对接历史：2026-09-24 最初基于前端 [PR #15](https://github.com/Craneplayz/niuma-manager/pull/15) 的 `3251c8eabf1224df9090b0752b94d1c9e3abc0b8` 增加 v0.2 输出与 HTTP 服务；当前团队主线已同时包含两端代码，无需手动叠加旧分支。

## 数据如何流动

前端“待确认”页提交原文 → 账户后端 `/api/recognition/batches` → `TEXT_HARNESS_URL` → 已配置的模型识别 → 裸 v0.2 JSON → 前端显示候选 → 用户确认 → 账户后端保存任务。当前可使用七牛 API 直连，或原 DeepSeek + Codex 方式；见 [API 配置说明](text-api-setup.md)。更换供应商不改变前端 JSON 契约。

识别服务不管理账号、数据库或最终任务。账户、候选持久化、重复确认和取消操作由前端 PR 自带的后端负责。

## 最短启动步骤

Python 3.9+；Windows 需要可用的 IANA 时区数据库，若 `python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Asia/Shanghai'))"` 报错，运行 `python -m pip install tzdata`。

1. 按 API 配置说明填写 `.env` 对应供应商的密钥。在项目根目录启动识别服务；修改 `.env` 后需停止旧识别进程再重新启动：

   ```powershell
   python -m ddl_text.http_server --real
   ```

2. 新开一个终端，启动前端 PR 的账户后端：

   ```powershell
   $env:TEXT_HARNESS_URL = "http://127.0.0.1:1333/recognize"
   python server/app.py
   ```

3. 新开终端，先 `cd frontend`，再按前端说明安装依赖并启动网页：`npm ci`、`npm run dev`。登录后进入“待确认”，输入一段合成通知，点击“识别为候选”，核对后确认。最新前端代码已独立到 `frontend/`，不要在仓库根目录运行 npm。

`GET http://127.0.0.1:1333/health` 只检查服务是否启动，不调用模型。端口可用 `--port` 修改；同时修改 `TEXT_HARNESS_URL`。不要把真实 API 密钥填入前端页面或 `VITE_*` 变量。

识别服务目前只监听 `127.0.0.1`，默认适用于三个进程运行在同一电脑。可选的服务间令牌通过两个终端中相同的 `$env:TEXT_HARNESS_TOKEN` 设置，与 DeepSeek 密钥无关。该令牌不是网页登录密码。账户后端不自动加载 `.env`，上述 URL/令牌必须导出到它的进程环境。文本 `.env` 中若有合并后的前端配置项，识别配置加载器会忽略这些项，不把它们传给模型。

这是本地联调入口；远程部署、TLS、生产限流和反向代理配置另行安排。当前单次批量识别共用 20 秒预算，早于前端后端的 25 秒、浏览器的 30 秒等待上限；剩余记录超时明确返回错误，可用新请求重试失败记录。繁忙时立即返回 503，避免并发启动多份付费识别。结束本地等待不保证供应商停止计费。

## JSON 的关键差异

- 请求必须显式 `contract_version: "0.2"`，输入元数据未知时填写 `null`。完整字段见[输入样例](examples/text-frontend-v02-request.json)。
- 每条证据新增 `supports`，由当前草稿的行动、时间、对象、课程引用生成，不给历史 v0.1 结果事后补证据。
- 完整原文确实没有时间要求：`deadline.status="not_provided"`、`value=null`，可以保留无 DDL 任务；正文残缺或时间无法确定：`unknown`，要求核实。
- 活动参考时间仍为 `reference/inferred`，不能自动当作截止时间。参考时间不能换算时用 `unknown`，解释保留在 `basis`。
- 冲突时间保留全部 `alternatives` 和时间证据；`raw` 是其中一条连续原文，其他引文见 `evidence`，不拼接伪造原句。
- HTTP 返回的是结果对象本身，不包裹 `data`，也不是文字说明。业务失败仍为 HTTP 200，通过 `status/errors/record_results` 区分，不能显示成“没有任务”。

CLI 现在默认输出 JSON；需要人工阅读时显式加 `--format text`。v0.1 CLI 请求和旧样例继续可用，输出版本跟随请求；HTTP 接口只收 v0.2，版本错误返回 400。

## 看样例、跑测试

直接查看：[输入](examples/text-frontend-v02-request.json)、[人工草稿](examples/text-frontend-v02-drafts.json)、[离线处理输出](examples/text-frontend-v02-output.json)、[真实模型联调结果](validation/frontend-v02-live-2026-09-24.json)。两个输入合计三个候选：加群（参考时间）、学习计划（未提供日期）、实验报告（明确 DDL）。这些全部是合成数据。

```powershell
# 不调用 API，输出可直接解析的 JSON
python -m ddl_text --input docs/examples/text-frontend-v02-request.json --drafts docs/examples/text-frontend-v02-drafts.json

# 本模块测试；默认不调用付费 API
python -m unittest discover -s tests -q
python scripts/check_text_contract_examples.py

# 完整 HTTP 联调：临时数据库和测试账号，退出清理，不修改正式账号/数据
# 合并前可把 --frontend-dir 指向前端 PR 的单独检出目录
python scripts/check_frontend_integration.py --frontend-dir .

# 显式真实模型联调：只允许内置两条合成通知，可能计费
python scripts/check_frontend_integration.py --frontend-dir . --live --output local-data/frontend-live-new.json
```

可单独启动 `python -m ddl_text.http_server --drafts docs/examples/text-frontend-v02-drafts.json` 做无费用接口联调；该模式仅匹配这些样例的完整记录，不能识别任意粘贴文本，遇到其他文本明确失败。

## 本次验证与下一步

本模块 81 项测试、17 个旧契约样例通过；包含实际 Codex CLI 对本地模拟服务的检查。使用前端 PR 的原始后端和原始校验器，离线及真实 API 两种模式均通过：候选接收、确认前不写任务、请求重放、同 ID 不同正文拒绝、账号隔离、确认新增、重复确认不重复新增。真实模型两条通知输出三项，明确截止为 `2026-09-30T18:00:00+08:00`。本次验证了 HTTP 与数据库链路，没有进行浏览器逐项人工验收。

按项目负责人要求，独立召回率评测暂缓，不作为本次合并门槛；上述结果也不代表召回率。阶段 E 剩余重点：同学按本文启动网页做人工验收、提供少量脱敏真实通知、修复实际发现的问题，并完成非作者 PR 评审。

合并 PR #14/#15 时应保留双方 `.env.example` 配置说明、`.gitignore` 的 `local-data/` 与前端数据库忽略规则。`ddl_text/contract_v02.py` 是前端校验器的已验证快照；后续协议修改应同步两端并重跑本联调脚本。

## 新文件职责

| 文件 | 作用 |
| --- | --- |
| `ddl_text/contract_v02.py` | 与前端一致的 v0.2 请求、候选、结果校验 |
| `ddl_text/http_server.py` | 本地 JSON HTTP 服务、令牌校验、大小限制和忙碌处理 |
| `tests/test_frontend_contract.py` | 无 DDL、证据用途、冲突、兼容性与 HTTP 异常回归 |
| `scripts/check_frontend_integration.py` | 启动真实前端后端和识别服务，检查确认/存储完整链路 |

原有文件说明见[阶段 C 说明](text-stage-c-extraction.md)，密钥配置见[API 配置说明](text-api-setup.md)。

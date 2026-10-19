// Diagnostics contain only server-allowlisted categories and aggregate numbers.
const reasons = {
  invalid_json: "JSON 格式无效", duplicate_keys: "JSON 字段重复", oversized_output: "输出超过长度限制",
  schema: "任务结构不符合约定", evidence: "引用未能在原文找到", time: "时间或日期引用无效",
  candidate: "最终任务校验失败", unknown: "未分类校验失败",
};
const number = value => Number.isFinite(value) && value >= 0 ? value : 0;
export function renderEmailDiagnostics(rows = []) {
  const safeRows = Array.isArray(rows) ? rows.filter(row => row?.stage === "validation" && Object.hasOwn(reasons, row.reason)) : [];
  const body = safeRows.length ? `<div class="table-wrap"><table><thead><tr><th>原因</th><th>次数</th><th>平均校验耗时</th></tr></thead><tbody>${safeRows.map(row => `<tr><td>${reasons[row.reason]}</td><td>${number(row.count)}</td><td>${number(row.avg_duration_ms).toFixed(1)} ms</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">此时间范围暂无校验失败诊断</p>';
  return `<article><h2>邮件识别校验诊断</h2><p>仅统计固定原因分类，不记录邮件内容或模型响应。校验耗时不包含模型生成。</p>${body}</article>`;
}

// Reasoning tokens are part of completion tokens; absent provider fields stay unknown.
export function renderEmailTokens(summary = {}) {
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
  const attempts = count(summary?.attempts);
  const fields = [["prompt_tokens","输入"],["completion_tokens","输出（含推理）"],["total_tokens","总计"],["reasoning_tokens","其中推理"]];
  const body = attempts ? `<div class="table-wrap"><table><thead><tr><th>Token 类型</th><th>累计数量</th><th>平均每次</th><th>有用量数据的调用</th></tr></thead><tbody>${fields.map(([field,label])=>{
    const row=summary?.tokens?.[field], reported=count(row?.reportedAttempts);
    const known=reported>0 && Number.isSafeInteger(row?.total) && row.total>=0;
    return `<tr><td>${label}</td><td>${known?row.total.toLocaleString("zh-CN"):"—"}</td><td>${known?Math.round(row.total/reported).toLocaleString("zh-CN"):"—"}</td><td>${reported} / ${attempts}</td></tr>`;
  }).join("")}</tbody></table></div>` : '<p class="empty">此时间范围暂无 Token 用量记录</p>';
  return `<article><h2>邮件模型 Token 用量</h2><p>模型调用 ${attempts} · 响应失败 ${count(summary?.failed)} · 长度截断 ${count(summary?.truncated)}</p><p>按实际模型调用统计，包含失败和重试；缓存命中不消耗模型 Token。推理属于输出，不与输出重复相加；缺少提供方用量时显示“—”，平均值仅按有该字段的调用计算，不估算历史用量。</p>${body}</article>`;
}


export function renderEmailCapacity(capacity = {}) {
  const value = v => Number.isFinite(v) && v >= 0 ? Math.round(v).toLocaleString("zh-CN") : "—";
  const scheduler = capacity.scheduler || {}, mailbox = capacity.mailbox || {}, provider = capacity.provider || {}, model = capacity.modelQueue || {};
  const rows = [
    ["共享模型执行 / 上限", `${value(model.active)} / ${value(model.limit)}`],
    ["共享模型排队", value(model.queued)],
    ["识别执行 / 上限", `${value(scheduler.active)} / ${value(scheduler.global_limit)}`],
    ["调度缓冲队列", value(scheduler.queued)],
    ["未完成导入任务", value(scheduler.acceptedJobs)],
    ["待处理邮件", value(scheduler.pendingEmails)],
    ["最早未完成任务时长（秒）", value(scheduler.oldestUnfinishedAgeSeconds)],
    ["IMAP 连接 / 上限", `${value(mailbox.active_sessions)} / ${value(mailbox.session_limit)}`],
    ["正文缓冲占用（字节）", value(mailbox.reserved_body_bytes)],
    ["状态请求 P95（ms）", value(capacity.statusP95Ms)],
    ["状态响应平均大小（字节）", value(capacity.statusMeanBytes)],
    ["遥测写入丢失", value(capacity.droppedWrites)],
    ["模型请求 / 分钟配额", provider.requestsPerMinute ? value(provider.requestsPerMinute) : "未配置"],
    ["模型 Token / 分钟配额", provider.tokensPerMinute ? value(provider.tokensPerMinute) : "未配置"],
  ];
  return `<article><h2>邮件服务当前负载</h2><p>仅当前进程的即时状态；重启后重置，不受上方日期筛选影响。正文缓冲字节不包含 Python 对象开销。</p><div class="table-wrap"><table><tbody>${rows.map(([label, text]) => `<tr><th>${label}</th><td>${text}</td></tr>`).join("")}</tbody></table></div></article>`;
}

import { newId } from "./id.js";

const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const incidents = new Map();
let errorIdentities = new WeakMap();
const normalized = new WeakSet();
const actions = new Set(["tasks_load", "tasks_save", "tasks_delete", "tasks_restore", "text_import", "teaching_sync", "import_review", "notice_reprocessing", "task_evidence", "account", "feedback", "general"]);

export function describeUserError(error, action = "general") {
  if (error && typeof error === "object" && normalized.has(error)) return error;
  const text = String(error?.message || error || "");
  const code = error?.code || error?.kind;
  const report = (kind, message, details = "") => {
    const value = { kind, message, details, status: error?.status };
    normalized.add(value);
    return value;
  };
  const emailMessages = {
    email_not_configured: "邮件服务暂未启用，请稍后再试。",
    email_send_failed: "验证码邮件发送失败，请稍后重试。",
    email_code_invalid: "验证码不正确或已失效，请核对或重新发送。",
    email_code_expired: "验证码已过期，请重新发送。",
    email_code_rate_limited: "验证码请求较多，请稍后再试。",
    email_already_verified: "当前邮箱已验证，请重新打开账号查看。",
    email_invalid: "请填写有效的邮箱地址。",
    email_registered: "该邮箱已注册，请直接登录。",
  };
  if (Object.hasOwn(emailMessages, code))
    return report(["email_not_configured", "email_send_failed"].includes(code) ? "service_connection" : "validation", emailMessages[code]);
  if (["incomplete_source", "source_incomplete", "source_changed"].includes(code) || /通知内容.*不完整|来源待补全|incomplete_source|重试识别不会补齐/.test(text))
    return report("incomplete_source", "没有获取到完整通知，请重新获取来源，或粘贴完整通知内容。", "重新识别只会处理已取得的内容，不会补充缺少的网页或附件。");
  if (/^(邮箱或密码不正确|该邮箱已注册|两次输入.*不一致|新密码不能与当前密码相同)$/.test(text))
    return report("validation", text);
  if (/^示例预览/.test(text))
    return report("validation", "当前为示例页面，请登录后使用此功能。");
  if (/结果已保存.*刷新失败|任务已保存，刷新/.test(text))
    return report("service_connection", "结果已保存，暂时无法更新页面。请刷新结果查看。");
  if (/任务修改尚未保存|还有未保存的数据|任务保存冲突/.test(text))
    return report("conflict", "任务修改还未保存，请先回到任务页面处理保存提示，再继续导入。");
  if (/后台可能仍在运行/.test(text))
    return report("service_connection", "暂时无法读取进度，正在进行的导入可能继续。请刷新进度查看结果。");
  if (error?.status === 409 || /内容.*(?:已变化|发生变化)|数据已被其他页面修改|任务内容已变化/.test(text))
    return report("conflict", /当前草稿仍保留/.test(text) ? "内容已更新，请取消编辑后重新打开。当前草稿仍保留。" : "内容刚刚更新，请重新读取后再操作。");
  if (code === "credential_dependency" || /凭据加密依赖.*未安装/.test(text))
    return report("credential_dependency", "暂时无法保存教学网连接，需要维护人员处理。请反馈此问题。");
  if (code === "credential_storage" || /凭据加密密钥|无法解密教学网凭据/.test(text))
    return report("credential_storage", "无法读取教学网连接，请重新保存账号和密码；仍失败时可反馈此问题。");
  if (code === "crawler_dependency" || /教学网采集依赖.*未安装|(?:crawler|tsx).*(?:未安装|not found|cannot find)|cannot find.*(?:tsx|playwright)/i.test(text))
    return report("crawler_dependency", "暂时无法从教学网获取通知，需要维护人员处理。你可以先粘贴通知内容。");
  if (code === "teaching_model_configuration" || /教学网识别配置不可用/.test(text))
    return report("teaching_model_configuration", "暂时无法整理教学网通知，需要维护人员处理。你可以先手动添加任务。");
  if (code === "model_configuration" || /文本识别配置不可用|模型.*(?:未配置|缺少配置|配置不可用)|missing (?:model )?credential/i.test(text))
    return report("model_configuration", "任务识别暂时不可用，需要维护人员处理。你可以先手动添加任务。");
  if (code === "recognition_rules" || /识别规则.*不一致/.test(text))
    return report("recognition_rules", "任务识别正在更新，请稍后重试；仍失败时可反馈此问题。");
  if (code === "model_busy" || /排队.*(?:已满|超时)|正在处理其他请求/.test(text))
    return report("model_busy", "当前使用人数较多，请稍后重试。");
  if (code === "session" || error?.status === 401 || /请先登录|登录.*(?:过期|失效)/.test(text))
    return report("session", "登录已过期，请重新登录后继续。");
  if (code === "teaching_credentials" || /请填写有效的教学网账号和密码|请先.*保存教学网账号/.test(text))
    return report("teaching_credentials", "请先连接教学网，填写账号和密码后再获取通知。");
  if (code === "teaching_connection" || /教学网采集失败|教学网.*登录失败/.test(text))
    return report("teaching_connection", "无法连接教学网。请核对教学网账号和密码，再重试；仍失败时可反馈此问题。");
  if (code === "crawler_runtime" || /教学网采集无法启动或超时/.test(text))
    return report("crawler_runtime", "这次没有成功获取教学网通知，请稍后重试。");
  if (code === "service_version" || /尚未提供.*接口|接口不存在|版本不匹配|服务版本不支持|采集记录格式暂不受支持/.test(text) || error?.status === 404)
    return report("service_version", "这项功能暂时不可用，请刷新页面后重试；仍失败时可反馈此问题。");
  if (["model_connection", "agent_timeout", "agent_unavailable"].includes(code) || /识别服务.*(?:不可用|无法连接|连接失败)|识别.*超时/.test(text))
    return report("model_connection", "这次未能完成任务识别，请稍后重试，或先手动添加任务。");
  if (code === "service_connection" || /无法连接服务|网络连接中断|Failed to fetch|NetworkError|fetch failed/i.test(text))
    return report("service_connection", "暂时连接不上，请检查网络后重试；仍失败时可反馈此问题。");
  if (code === "timeout" || /请求超时|等待已超时/.test(text) || error?.name === "AbortError")
    return report("timeout", "等待时间较长，请重新读取结果，确认是否完成后再重试。");
  if (["invalid_result", "invalid_agent_output"].includes(code) || /v0\.|契约|协议|JSON|contract|request_id|结果未通过.*校验/i.test(text))
    return report("invalid_result", "这次没能正确整理出任务，请重试，或先手动添加任务。");
  // Only known input validation is echoed; raw system exceptions never reach users.
  if ((!error?.status || error.status === 400) && text.length < 220 && !/(api[_-]?key|authorization|bearer|password|secret|token|sk-[a-z0-9]|(?:密码|密钥)\s*[:=]|后端|配置|安装|server\/|crawler\/)/i.test(text) &&
      /^(?:请(?:填写|选择|确认|核对|输入|先)|每次最多|填写时刻前|新增任务无需|处理方式已经改变|只能|最多)/.test(text))
    return report("validation", text);
  const fallback = {
    tasks_load: "暂时无法读取任务，请稍后重试。",
    tasks_save: "这次修改未能确认保存，请重试保存。",
    tasks_delete: "暂时无法删除，请稍后重试。",
    tasks_restore: "暂时无法恢复，请稍后重试。",
    task_evidence: "暂时无法显示通知原文，请稍后重试。",
    account: "这次账号操作未完成，请稍后重试。",
    feedback: "反馈暂时未能发送，请稍后重试。",
  };
  return report("unknown", fallback[action] || "这次操作未完成，请重试；仍失败时可反馈此问题。");
}

function incidentFor(error, action, context) {
  const report = describeUserError(error, action);
  const status = Number.isInteger(report.status) && report.status >= 400 && report.status <= 599 ? report.status : null;
  // Keep redraws of an occurrence stable without treating later failures as sent.
  let identity = context;
  if (!identity && error && typeof error === "object") {
    if (!errorIdentities.has(error)) errorIdentities.set(error, newId());
    identity = errorIdentities.get(error);
  }
  const key = JSON.stringify([action, report.kind, status, identity || String(error)]);
  if (!incidents.has(key)) {
    incidents.set(key, { id: newId(), code: report.kind, action, status, occurred_at: new Date().toISOString(), state: "idle", result: "" });
  }
  return { report, incident: incidents.get(key) };
}

const buttonLabel = state => ({ pending: "正在提交…", sent: "已反馈", failed: "重试反馈" })[state] || "反馈此问题";
export function userErrorMarkup(error, action = "general", context) {
  if (!error) return "";
  if (!actions.has(action)) action = "general";
  const { report, incident } = incidentFor(error, action, context);
  return `<div class="user-error" role="alert"><p class="user-error-message">${esc(report.message)}</p>${report.details ? `<p class="user-error-help">${esc(report.details)}</p>` : ""}${report.kind === "validation" ? "" : `<div class="error-feedback"><button type="button" class="btn quiet" data-error-feedback="${esc(incident.id)}" ${["pending", "sent"].includes(incident.state) ? "disabled" : ""}>${buttonLabel(incident.state)}</button><span class="error-feedback-status" role="status" data-error-feedback-status="${esc(incident.id)}">${esc(incident.result)}</span></div>`}</div>`;
}

function updateIncident(incident) {
  for (const button of document.querySelectorAll("[data-error-feedback]")) {
    if (button.dataset.errorFeedback !== incident.id) continue;
    button.disabled = ["pending", "sent"].includes(incident.state);
    button.textContent = buttonLabel(incident.state);
  }
  for (const label of document.querySelectorAll("[data-error-feedback-status]"))
    if (label.dataset.errorFeedbackStatus === incident.id) label.textContent = incident.result;
}

const revision = value => /^[a-f0-9]{7,40}$/.test(value || "") ? value : "unknown";
export async function submitErrorFeedback(button, { send, frontendRevision, backendRevision }) {
  const incident = [...incidents.values()].find(item => item.id === button?.dataset?.errorFeedback);
  if (!incident || ["pending", "sent"].includes(incident.state)) return;
  incident.payload ||= {
    contract_version: "error-report/0.1", incident_id: incident.id,
    code: incident.code, action: incident.action, occurred_at: incident.occurred_at,
    frontend_revision: revision(frontendRevision), backend_revision: revision(backendRevision), status: incident.status,
  };
  incident.state = "pending"; incident.result = "只发送错误分类、操作和版本，不附带通知正文或账号密码。";
  updateIncident(incident);
  try {
    const result = await send(incident.payload);
    if (result?.contract_version !== "error-report/0.1" || result.saved !== true || !/^[a-f0-9]{24}$/.test(result.report_id || "")) throw new Error("unconfirmed");
    incident.state = "sent";
    incident.result = "已提交，感谢反馈。";
  } catch (error) {
    incident.state = "failed";
    incident.result = error?.status === 429 ? "反馈次数较多，请稍后再试。" : "反馈未能确认提交，请稍后重试。";
  }
  updateIncident(incident);
}

export function resetErrorFeedback() { incidents.clear(); errorIdentities = new WeakMap(); }

export function pruneErrorFeedback() {
  if (incidents.size <= 500) return;
  const visible = new Set([...document.querySelectorAll("[data-error-feedback]")].map(button => button.dataset.errorFeedback));
  for (const [key, incident] of incidents) {
    if (incidents.size <= 500) break;
    if (incident.state !== "pending" && !visible.has(incident.id)) incidents.delete(key);
  }
}

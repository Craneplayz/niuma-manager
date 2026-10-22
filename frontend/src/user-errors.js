import { newId } from "./id.js";
import recognitionErrors from "../../contracts/recognition-errors.json" with { type: "json" };

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
    const id = /^[0-9a-f]{24}$/.test(error?.diagnostic_id || '') ? error.diagnostic_id : '';
    const value = { kind, message, details: details + (id ? ` 诊断编号：${id}。` : ''), status: error?.status };
    if (id) value.diagnostic_id = id;
    if (typeof error?.diagnostics === 'string' && error.diagnostics.length <= 65536) value.diagnostics = error.diagnostics;
    normalized.add(value);
    return value;
  };
  const reason = error?.failure?.reason;
  if (typeof reason === "string" && Object.hasOwn(recognitionErrors, reason)) {
    const description = recognitionErrors[reason];
    const diagnostic = error.diagnostic;
    const location = diagnostic && typeof diagnostic.schema_path === "string" && diagnostic.schema_path.length <= 512 &&
      /^[A-Za-z0-9_$/]*$/.test(diagnostic.schema_path) &&
      /^(type|enum|const|required|additionalProperties|maxLength|minLength|maxItems|minItems|format|pattern|anyOf|allOf|oneOf|maximum|minimum)$/.test(diagnostic.rule)
      ? ` 校验位置：${diagnostic.schema_path || "结果"}；规则：${diagnostic.rule}。` : "";
    const missing = Array.isArray(diagnostic?.missing_fields) ? diagnostic.missing_fields.filter(v => typeof v === 'string' && /^[A-Za-z_]{1,100}$/.test(v)).slice(0,20) : [];
    const types = new Set(['object','array','string','number','integer','boolean','null','unknown']);
    const shape = types.has(diagnostic?.expected_type) && types.has(diagnostic?.actual_type)
      ? ` 预期类型：${diagnostic.expected_type}；实际类型：${diagnostic.actual_type}。` : '';
    const instance = typeof diagnostic?.instance_path === 'string' && diagnostic.instance_path.length <= 512 && /^[A-Za-z0-9_$/]*$/.test(diagnostic.instance_path)
      ? ` 字段位置：${diagnostic.instance_path || '结果'}。` : '';
    return report(reason, description.message, description.action + location + instance + (missing.length ? ` 缺失字段：${missing.join('、')}。` : '') + shape);
  }
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
  if (["incomplete_source", "source_incomplete", "source_changed", "source_missing"].includes(code) || /通知内容.*不完整|来源待补全|incomplete_source|重试识别不会补齐/.test(text))
    return report("incomplete_source", "通知内容不完整，请重新获取来源或粘贴完整通知内容。", "重新识别仅处理已获取的内容，不会补充缺失的网页或附件。");
  if (/^(邮箱或密码不正确|该邮箱已注册|两次输入.*不一致|新密码不能与当前密码相同)$/.test(text))
    return report("validation", text);
  if (/^示例预览/.test(text))
    return report("validation", "当前为示例页面，请登录后使用此功能。");
  if (/结果已保存.*刷新失败|任务已保存，刷新/.test(text))
    return report("service_connection", "结果已保存，但页面更新失败。请刷新结果。");
  if (/任务修改尚未保存|还有未保存的数据|任务保存冲突/.test(text))
    return report("conflict", "任务修改尚未保存，请返回任务页面处理保存提示后继续导入。");
  if (/后台可能仍在运行/.test(text))
    return report("service_connection", "暂时无法读取导入进度，导入可能仍在进行。请刷新进度以确认结果。");
  if (/^停止导入未完成/.test(text))
    return report("service_connection", "暂时无法确认导入已停止，请刷新进度后重试。");
  if (error?.status === 409 || /内容.*(?:已变化|发生变化)|数据已被其他页面修改|任务内容已变化/.test(text))
    return report("conflict", /当前草稿仍保留/.test(text) ? "内容已更新，请取消编辑后重新打开。当前草稿仍保留。" : "内容已更新，请重新读取后再操作。");
  if (code === "credential_dependency" || /凭据加密依赖.*未安装/.test(text))
    return report("credential_dependency", "暂时无法保存教学网连接，需要维护人员处理。请反馈此问题。");
  if (code === "credential_storage" || /凭据加密密钥|无法解密教学网凭据/.test(text))
    return report("credential_storage", "无法读取教学网连接信息，请重新保存账号和密码。如仍失败，请提交问题反馈。");
  if (code === "crawler_dependency" || /教学网采集依赖.*未安装|(?:crawler|tsx).*(?:未安装|not found|cannot find)|cannot find.*(?:tsx|playwright)/i.test(text))
    return report("crawler_dependency", "暂时无法从教学网获取通知，需要维护人员处理。可通过粘贴通知内容导入。");
  if (code === "teaching_model_configuration" || /教学网识别配置不可用/.test(text))
    return report("teaching_model_configuration", "暂时无法整理教学网通知，需要维护人员处理。可手动添加任务。");
  if (code === "model_configuration" || /文本识别配置不可用|模型.*(?:未配置|缺少配置|配置不可用)|missing (?:model )?credential/i.test(text))
    return report("model_configuration", "任务识别暂时不可用，需要维护人员处理。可手动添加任务。");
  if (code === "recognition_rules" || /识别规则.*不一致/.test(text))
    return report("recognition_rules", "任务识别正在更新，请稍后重试。如仍失败，请提交问题反馈。");
  if (code === "model_busy" || /排队.*(?:已满|超时)|正在处理其他请求/.test(text))
    return report("model_busy", "识别服务繁忙，请稍后重试。");
  if (code === "session" || error?.status === 401 || /请先登录|登录.*(?:过期|失效)/.test(text))
    return report("session", "登录状态已失效，请重新登录。");
  if (code === "teaching_credentials" || /请填写有效的教学网账号和密码|请先.*保存教学网账号/.test(text))
    return report("teaching_credentials", "请填写并保存教学网账号和密码后获取通知。");
  if (code === "teaching_connection" || /教学网采集失败|教学网.*登录失败/.test(text))
    return report("teaching_connection", "无法连接教学网，请核对账号和密码后重试。如仍失败，请提交问题反馈。");
  if (code === "crawler_runtime" || /教学网采集无法启动或超时/.test(text))
    return report("crawler_runtime", "教学网通知获取失败，请稍后重试。");
  if (code === "service_version" || /尚未提供.*接口|接口不存在|版本不匹配|服务版本不支持|采集记录格式暂不受支持/.test(text) || error?.status === 404)
    return report("service_version", "当前功能暂时不可用，请刷新页面后重试。如仍失败，请提交问题反馈。");
  if (["model_connection", "agent_timeout", "agent_unavailable"].includes(code) || /识别服务.*(?:不可用|无法连接|连接失败)|识别.*超时/.test(text))
    return report("model_connection", "任务识别未完成，请稍后重试或手动添加任务。");
  if (code === "service_connection" || /无法连接服务|网络连接中断|Failed to fetch|NetworkError|fetch failed/i.test(text))
    return report("service_connection", "暂时无法连接服务，请检查网络后重试。如仍失败，请提交问题反馈。");
  if (code === "timeout" || /请求超时|等待已超时/.test(text) || error?.name === "AbortError")
    return report("timeout", "请求超时，请重新读取结果，确认操作是否完成后再重试。");
  if (["invalid_result", "invalid_agent_output"].includes(code) || /v0\.|契约|协议|JSON|contract|request_id|结果未通过.*校验/i.test(text))
    return report("invalid_result", "任务识别结果异常，请重试或手动添加任务。");
  // Only known input validation is echoed; raw system exceptions never reach users.
  if ((!error?.status || error.status === 400) && text.length < 220 && !/(api[_-]?key|authorization|bearer|password|secret|token|sk-[a-z0-9]|(?:密码|密钥)\s*[:=]|后端|配置|安装|server\/|crawler\/)/i.test(text) &&
      /^(?:请(?:填写|选择|确认|核对|输入|先)|每次最多|填写时刻前|新增任务无需|处理方式已经改变|只能|最多)/.test(text))
    return report("validation", text);
  const fallback = {
    tasks_load: "暂时无法读取任务，请稍后重试。",
    tasks_save: "无法确认修改是否已保存，请重试保存。",
    tasks_delete: "暂时无法删除任务，请稍后重试。",
    tasks_restore: "暂时无法恢复任务，请稍后重试。",
    task_evidence: "暂时无法显示通知原文，请稍后重试。",
    account: "账号操作未完成，请稍后重试。",
    feedback: "反馈发送未完成，请稍后重试。",
  };
  return report("unknown", fallback[action] || "操作未完成，请重试。如仍失败，请提交问题反馈。");
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
    incidents.set(key, { id: newId(), code: report.kind, action, status, diagnostic_id: report.diagnostic_id, occurred_at: new Date().toISOString(), state: "idle", result: "" });
  }
  return { report, incident: incidents.get(key) };
}

const buttonLabel = state => ({ pending: "正在提交…", sent: "已反馈", failed: "重试反馈" })[state] || "反馈此问题";
export function diagnosticMarkup(error) {
  return typeof error?.diagnostics === 'string' && error.diagnostics.length <= 65536
    ? `<details class="test-error-diagnostics"><summary>识别诊断详情</summary><pre>${esc(error.diagnostics)}</pre></details>` : '';
}

export function userErrorMarkup(error, action = "general", context) {
  if (!error) return "";
  if (!actions.has(action)) action = "general";
  const { report, incident } = incidentFor(error, action, context);
  return `<div class="user-error" role="alert"><p class="user-error-message">${esc(report.message)}</p>${report.details ? `<p class="user-error-help">${esc(report.details)}</p>` : ""}${diagnosticMarkup(report)}${report.kind === "validation" ? "" : `<div class="error-feedback"><button type="button" class="btn quiet" data-error-feedback="${esc(incident.id)}" ${["pending", "sent"].includes(incident.state) ? "disabled" : ""}>${buttonLabel(incident.state)}</button><span class="error-feedback-status" role="status" data-error-feedback-status="${esc(incident.id)}">${esc(incident.result)}</span></div>`}</div>`;
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
    ...(incident.diagnostic_id ? { diagnostic_id: incident.diagnostic_id } : {}),
  };
  incident.state = "pending"; incident.result = "只发送错误分类、操作、版本和诊断编号，不附带通知正文或账号密码。";
  updateIncident(incident);
  try {
    const result = await send(incident.payload);
    if (result?.contract_version !== "error-report/0.1" || result.saved !== true || !/^[a-f0-9]{24}$/.test(result.report_id || "")) throw new Error("unconfirmed");
    incident.state = "sent";
    incident.result = "已提交，感谢反馈。";
  } catch (error) {
    incident.state = "failed";
    incident.result = error?.status === 429 ? "反馈提交过于频繁，请稍后重试。" : "无法确认反馈是否已提交，请稍后重试。";
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

import { icon } from "./icons.js";

// Resolve receipt names against the current workspace; receipt totals stay historical.
export function importResultStatus(item, category, host) {
  if (category === "error") return item.recovered
    ? { label: "已恢复", tone: "muted" } : { label: "错误", tone: "error" };
  const state = host.getState?.() || {};
  const matches = task => item.task_id && task.id === item.task_id || item.candidate_id &&
    (task.recognitionHistory || []).some(event => event.storageId === item.candidate_id);
  const task = (state.tasks || []).find(matches);
  if (task) return {
    label: task.content_type === "notice" ? "通知" : ["done", "completed"].includes(task.status) ? "✓ 已完成" : "未完成",
    tone: task.content_type !== "notice" && ["done", "completed"].includes(task.status) ? "success" : "muted",
    taskId: task.id,
  };
  if ((state.trash || []).some(matches)) return { label: "已移至回收站", tone: "trash", icon: "trash" };
  const candidate = item.candidate_id && host.getCandidate?.(item.candidate_id);
  if (candidate?.state === "pending") return { label: "待核对", tone: "review" };
  if (candidate?.state === "ignored") return { label: "已忽略", tone: "muted" };
  if (candidate?.state === "applied") return { label: "已核对", tone: "muted" };
  return { label: category === "review" ? "状态暂不可用" : "任务已移除", tone: "muted" };
}

export function importStatusMarkup(status) {
  return `<small class="sync-item-status sync-item-status-${status.tone}">${status.icon === "trash" ? icon("trash") : ""}${status.label}</small>`;
}

export function importErrorSummaryMarkup(items) {
  const recovered = items.filter(item => item.recovered).length;
  const all = recovered === items.length;
  const status = recovered ? `<span class="sync-error-summary-${all ? "success" : "review"}">${all ? "全部已恢复" : `部分已恢复 ${recovered} / ${items.length}`}</span>` : "";
  return `<span class="sync-error-count-${all ? "muted" : "error"}">错误 ${items.length} 条</span>${status ? ` · ${status}` : ""}`;
}

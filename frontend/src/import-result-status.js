import { icon } from "./icons.js";

const matchesTask = (item, task) => item.task_id && task.id === item.task_id || item.candidate_id &&
  (task.recognitionHistory || []).some(event => event.storageId === item.candidate_id);

// Resolve receipt names against the current workspace; receipt totals stay historical.
export function importResultStatus(item, category, host) {
  if (category === "error") return item.recovered
    ? { label: "已恢复", tone: "muted" } : { label: "错误", tone: "error" };
  const state = host.getState?.() || {};
  const matches = task => matchesTask(item, task);
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

function reviewed(item, host) {
  const candidate = item.candidate_id && host.getCandidate?.(item.candidate_id);
  if (candidate) return ["applied", "ignored"].includes(candidate.state);
  const state = host.getState?.() || {};
  return [...(state.tasks || []), ...(state.trash || [])].some(task => matchesTask(item, task));
}

// Counts describe the original import; colors and order describe current actions.
export function importCategorySummaries(collections, host, counts = {}) {
  const definitions = [["error", "错误", "条"], ["review", "待核对", "项"], ["added", "新增", "项"], ["updated", "修改", "项"]];
  return definitions.map(([key, label, unit]) => {
    const items = collections[key] || [], count = counts[key] ?? items.length;
    let tone = "success", priority = key === "added" ? 2 : 3, description = "导入结果已保存。";
    if (key === "error") {
      const recovered = items.filter(item => item.recovered).length;
      tone = recovered === count ? "success" : recovered ? "review" : "error";
      priority = recovered === count ? 5 : 0;
      description = `已恢复 ${recovered} / ${count} 条；仍需处理 ${count - recovered} 条。`;
    } else if (key === "review") {
      const handled = items.filter(item => reviewed(item, host)).length;
      tone = handled === count ? "success" : "review";
      priority = handled === count ? 4 : 1;
      description = handled === count ? `核对已处理 ${handled} / ${count} 项。` : `已处理 ${handled} / ${count} 项；其余仍待核对或确认状态。`;
    }
    return { key, label, unit, count, tone, priority, description };
  }).filter(group => group.count > 0).sort((a, b) => a.priority - b.priority);
}

import { icon } from "./icons.js";

const matchesTask = (item, task) => item.task_id && task.id === item.task_id || item.candidate_id &&
  (task.recognitionHistory || []).some(event => event.storageId === item.candidate_id);

// Resolve receipt names against the current workspace; receipt totals stay historical.
export function importResultStatus(item, category, host) {
  if (category === "error") return item.recovered
    ? { label: "已恢复", tone: "muted" } : { label: "错误", tone: "error" };
  const state = host.getState?.() || {};
  const matches = task => matchesTask(item, task);
  const candidate = item.candidate_id && host.getCandidate?.(item.candidate_id);
  const task = (state.tasks || []).find(matches);
  if (task) {
    const done = ["done", "completed"].includes(task.status);
    return { label: done ? "✓ 已完成" : "未完成", tone: done ? "success" : "muted",
      taskId: task.id, notice: task.content_type === "notice" };
  }
  const trash = (state.trash || []).find(matches);
  if (trash) return { label: "回收站", tone: "trash", icon: "trash", notice: trash.content_type === "notice",
    message: "这条内容在回收站中，可从回收站恢复" };
  const cancelled = (host.getCancelledTasks?.() || []).find(matches);
  if (cancelled) return { label: "已取消", tone: "muted", notice: cancelled.content_type === "notice",
    message: "这条任务或通知已取消" };
  const notice = candidate?.candidate?.content_type === "notice";
  if (candidate?.state === "ignored") return { label: "已忽略", tone: "muted", notice,
    message: "这条内容此前已忽略" };
  if (candidate?.state === "applied" || category !== "review") return { label: "已移除", tone: "muted", notice,
    message: "这条任务或通知已移除，无法打开详情" };
  return { label: "状态暂不可用", tone: "muted", notice,
    message: "暂时无法读取这条内容的状态，请刷新后重试" };
}

export function importTypeMarkup(status) {
  return status.notice ? '<span class="notice-label">通知</span>' : "";
}

export function importStatusMarkup(status) {
  return `<small class="sync-item-status sync-item-status-${status.tone}">${status.icon === "trash" ? icon("trash") : ""}${status.label}</small>`;
}

// Counts describe the original import; colors and order describe current actions.
export function importCategorySummaries(collections, host, counts = {}) {
  const definitions = [["error", "错误", "条"], ["review", "历史记录", "项"], ["added", "新增", "项"], ["updated", "修改", "项"]];
  return definitions.map(([key, label, unit]) => {
    const items = collections[key] || [], count = counts[key] ?? items.length;
    let tone = "success", priority = key === "added" ? 2 : 3, description = "导入结果已保存。";
    if (key === "error") {
      const recovered = items.filter(item => item.recovered).length;
      tone = recovered === count ? "success" : recovered ? "review" : "error";
      priority = recovered === count ? 5 : 0;
      description = `已恢复 ${recovered} / ${count} 条；仍需处理 ${count - recovered} 条。`;
    } else if (key === "review") {
      description = "旧版导入记录。";
    }
    return { key, label, unit, count, tone, priority, description };
  }).filter(group => group.count > 0).sort((a, b) => a.priority - b.priority);
}

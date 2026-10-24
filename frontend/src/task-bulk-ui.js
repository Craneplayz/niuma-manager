import { isNotice } from './task-model.js';

export const isBulkSelectable = task => Boolean(task?.id) && !task.reviewChange;

// Selection contains identities, never a live “select everything” flag: later imports
// cannot silently join an operation the user has already chosen.
export function selectedBulkTasks(visible, selected) {
  return visible.filter(task => isBulkSelectable(task) && selected.has(task.id));
}

export function recoveredBulkTasks(state, affectedIds, fallback = []) {
  const rows = [...state.tasks, ...state.trash], previous = new Map(fallback.map(task => [task.id, task]));
  const tasks = new Map();
  for (const id of affectedIds) {
    const candidateId = id.startsWith('review:') ? id.slice(7) : null;
    const task = rows.find(row => row.id === id || candidateId && row.recognitionHistory?.some(event => event.storageId === candidateId))
      || previous.get(id) || { id, ...(candidateId ? { reviewCandidateId: candidateId } : {}) };
    tasks.set(task.id, task);
  }
  return [...tasks.values()];
}

export function bulkLabels(tasks) {
  const notices = tasks.some(isNotice), actions = tasks.some(task => !isNotice(task));
  return {
    complete: notices ? actions ? '完成 / 已阅读' : '标记已阅读' : '完成',
    reopen: notices ? actions ? '重新打开 / 未阅读' : '标记未阅读' : '重新打开',
    trash: tasks.some(task => task.reviewCandidateId) ? '删除 / 忽略' : '移入回收站',
  };
}

export function renderBulkCheckbox(task, { selected, escapeHtml, disabled = false, trash = false }) {
  const title = escapeHtml(task.title || '未命名任务');
  return `<label class="task-select"><input type="checkbox" ${trash ? 'data-bulk-trash-select' : 'data-bulk-select'}="${escapeHtml(task.id)}" aria-label="选择：${title}" ${selected.has(task.id) ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span aria-hidden="true"></span></label>`;
}

export function renderBulkToolbar({ tasks, available, selected, busy = false, blocked = false, error = '', trash = false, escapeHtml }) {
  const labels = bulkLabels(tasks), count = tasks.length;
  const context = trash ? ' data-bulk-context="trash"' : '';
  const button = (action, label, disabled = false) => `<button type="button" class="btn quiet" data-bulk-action="${action}"${context} ${disabled ? 'disabled' : ''}>${label}</button>`;
  const all = available.length > 0 && available.every(task => selected.has(task.id));
  const disabled = busy || blocked || !count;
  const candidates = tasks.some(task => task.reviewCandidateId);
  return `<div class="bulk-toolbar${trash ? ' is-trash' : ''}" role="region" aria-label="批量操作" aria-busy="${busy}"><div class="bulk-selection"><strong role="status">${busy ? '正在保存' : '已选'} ${count} 项</strong>${button('select-all', all ? '取消全选' : trash ? '全选回收站' : '全选筛选结果', busy || !available.length)}${button('exit', '退出批量操作', busy)}</div><div class="bulk-actions">${trash ? button('restore', '恢复', disabled) : `${button('complete', labels.complete, disabled || !tasks.some(task => task.status !== 'done'))}${button('reopen', labels.reopen, disabled || candidates || !tasks.some(task => task.status === 'done'))}${button('deadline', '修改日期', disabled)}${button('trash', labels.trash, disabled)}`}</div>${candidates && !trash ? '<p class="bulk-hint">待核对内容完成或修改日期后转为普通任务；删除按忽略处理。</p>' : ''}${error ? `<div class="bulk-error" role="alert">${escapeHtml(error)}${button('retry', '重试', busy || blocked)}</div>` : ''}</div>`;
}

export function renderBulkDeadlineForm(count) {
  return `<header class="dialog-header"><h2 id="dialog-heading">修改 ${count} 项的日期</h2><button type="button" class="icon-button" data-action="dialog-close" aria-label="关闭日期修改">×</button></header><form id="bulk-deadline-form"><label class="field">日期<input type="date" name="date"></label><p class="field-hint">留空清除日期；已有具体时刻保留，清除日期时同时清除时刻。待核对内容应用日期后将转为普通任务或通知。</p><div id="bulk-deadline-error" role="alert"></div><footer class="form-actions"><button type="button" class="btn quiet" data-action="dialog-close">取消</button><button type="submit" class="btn primary">应用日期</button></footer></form>`;
}

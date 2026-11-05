import { candidateDue } from './candidate-data.js';
import { deadlineLabel, shiftDay } from './task-model.js';

// View-only rows: candidate drafts never enter the task save payload.
export function projectReviewTasks(tasks, items, today) {
  const rows = tasks.map(task => ({ ...task, reviewChanges: [] }));
  const seen = new Set();
  for (const item of items) {
    if (!item?.id || seen.has(item.id) || item.state !== 'pending' || item.batch?.demo) continue;
    seen.add(item.id);
    const candidate = item.candidate;
    if (!candidate || rows.some(task => task.recognitionHistory?.some(event => event.storageId === item.id))) continue;
    const change = ['update', 'postpone', 'cancel'].includes(candidate.notice_type);
    const date = candidateDue(candidate.deadline);
    // Only explicit task identity is sufficient to attach a change to a saved row.
    const target = change && rows.find(task => task.id === candidate.target_reference && task.status !== 'done');
    if (target) {
      target.reviewChanges.push({ id: item.id, title: candidate.title, action: candidate.notice_type,
        proposedDate: date.date ? deadlineLabel({ due: date.date, dueTime: date.time }, today) : '' });
      continue;
    }
    const knownDate = candidate.deadline?.status === 'explicit' && Boolean(shiftDay(date.date, 0));
    const source = candidate.sources?.[0];
    const record = item.batch?.request?.records?.find(record => record.input_id === candidate.input_id);
    const sourceName = ({ teaching_web: '教学网', manual: '文本导入', wechat: '微信', email: '邮箱' })[source?.platform || record?.platform]
      || source?.label || record?.source_label || '文本导入';
    rows.push({
      id: `review:${item.id}`, title: candidate.title, course: candidate.course_or_project || '',
      source: sourceName, content_type: candidate.content_type || 'task', status: 'todo',
      notes: candidate.description || '', due: knownDate ? date.date : '', dueTime: knownDate ? date.time : '',
      reviewCandidateId: item.id, reviewChange: change,
      reviewVersion: item.version,
      reviewDateLabel: !knownDate
        ? date.date ? `${deadlineLabel({ due: date.date, dueTime: date.time }, today)} · 日期待核对` : '日期待核对'
        : '',
      reviewActionLabel: ({ update: '更新内容', postpone: '调整截止', cancel: '取消任务', uncertain: '处理方式待核对' })[candidate.notice_type] || '',
    });
  }
  return rows;
}

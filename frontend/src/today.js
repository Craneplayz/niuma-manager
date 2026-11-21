import { isNotice, sortTasks } from './task-model.js';
import { renderTaskRow } from './task-views.js';
import { renderCourseBadge } from './course-identity.js';

export function renderToday({ items, classes, sessions, today, heading, esc, icon, reviewBusy, reviewStatus }) {
  const work = new Map(items.map(task => [task.id, task]));
  const scheduled = new Set();
  const agenda = classes.filter(e => e.date === today).map(e => ({
    time: e.startTime || '', until: e.endTime, title: e.title,
    detail: e.kind === 'exam' ? ['考试', e.period].filter(Boolean).join(' · ') : ['上课', e.location].filter(Boolean).join(' · '),
  }));
  for (const event of sessions.filter(e => e.date === today)) {
    const task = work.get(event.taskId);
    if (!task || task.status === 'done') continue;
    scheduled.add(task.id);
    const due = task.due === today ? '今天截止' + (task.dueTime ? ' ' + task.dueTime : '') : task.due && task.due < today ? '已逾期' : '';
    agenda.push({ time: event.startTime, until: event.endTime, title: event.title, id: task.id,
      course: task.course, detail: ['学习安排', due].filter(Boolean).join(' · ') });
  }
  agenda.sort((a, b) => a.time.localeCompare(b.time));
  const row = task => renderTaskRow(task, { escapeHtml: esc, icon, today, reviewBusy });
  const pending = sortTasks(items.filter(t => t.status !== 'done' && !t.recycleBin && (t.reviewCandidateId || t.reviewChanges?.length || !scheduled.has(t.id) && t.due && t.due <= today)));
  return `<section class="learning-page">${heading({ title: '今天', actions: '<button class="btn" data-action="task-new">添加任务</button>' })}
    <section class="course-block today-agenda"><header><h2>当日日程</h2><button class="btn quiet" data-action="today-calendar">查看日历</button></header>
    ${agenda.map(e => `<div class="agenda-row"><time>${esc(e.time || '时间待定')}${e.until ? '<small>' + esc(e.until) + '</small>' : ''}</time><div>${e.id ? `<button class="agenda-task" data-task-open="${esc(e.id)}">${esc(e.title)}</button>` : `<strong>${esc(e.title)}</strong>`}<small>${esc(e.detail)} ${e.course ? renderCourseBadge(e.course, { compact: true }) : ''}</small></div></div>`).join('') || '<p class="course-empty">今天还没有上课、考试或学习安排。</p>'}</section>
    <section class="course-block"><header><h2>需要处理 <small>${pending.length}</small></h2><button class="btn quiet" data-scope="all">查看全部任务</button></header>${reviewStatus}
    ${pending.map(row).join('') || '<p class="course-empty">没有其他到期或待核对事项。</p>'}</section></section>`;
}

import { deadlineLabel, isNotice, sortTasks } from './task-model.js';
import { renderTaskRow } from './task-views.js';
import { canSchedule } from './study-planning.js';
import { renderCourseBadge } from './course-identity.js';
import { newId } from './id.js';

function collectCourses(tasks, tables, profiles) {
  const courses = new Map();
  function group(rawName) {
    const name = rawName.trim();
    if (!courses.has(name)) courses.set(name, { name, tasks: [], meetings: [], profile: {} });
    return courses.get(name);
  }
  for (const profile of profiles) group(profile.name).profile = profile;
  for (const table of tables) for (const meeting of table.meetings)
    group(meeting.title).meetings.push({ ...meeting, first: table.firstMonday, last: table.lastDate });
  for (const task of tasks) if (task.course?.trim() && !task.recycleBin) group(task.course).tasks.push(task);
  return [...courses.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh'));
}

function courseFacts(course, today, esc) {
  const rows = [];
  function fact(label, value) {
    if (value) rows.push(`<dt>${label}</dt><dd>${esc(value)}</dd>`);
  }
  fact('课程全名', course.name);
  fact('任课教师', course.profile.teacher);
  const schedules = new Set(), exams = new Set(), terms = new Set();
  for (const m of course.meetings) {
    const parity = { odd: '单周', even: '双周', all: '每周' }[m.parity];
    schedules.add(`${parity} · 周${'一二三四五六日'[m.weekday - 1]} 第${m.startPeriod}${m.startPeriod === m.endPeriod ? '' : '–' + m.endPeriod}节 · ${m.startTime}–${m.endTime}${m.location ? ' · ' + m.location : ''}（${m.first} 至 ${m.last}）`);
    if (m.exam) exams.add([m.exam.date, m.exam.period].filter(Boolean).join(' '));
    if (m.first <= today && today <= m.last) {
      const week = date => 1 + Math.floor((Date.parse(date) - Date.parse(m.first)) / 604800000);
      terms.add(`第 ${week(today)} 周 / 共 ${week(m.last)} 周`);
    }
  }
  for (const line of schedules) fact('上课', line);
  for (const line of terms) fact('教学进度', line);
  for (const line of exams) fact('考试', line);
  fact('考勤要求', course.profile.attendance);
  fact('课程备注', course.profile.notes);
  return `<dl class="course-facts">${rows.join('')}</dl>`;
}

function gradingMarkup(weights, esc) {
  if (!weights.length) return '<p class="course-empty">还没有记录评分项目和占比。</p>';
  const total = weights.reduce((sum, item) => sum + item.percent, 0);
  return `<div class="course-weights">${weights.map(item => `<div><span>${esc(item.label)}</span><strong>${esc(item.percent)}%</strong><progress aria-label="${esc(item.label)}" value="${esc(item.percent)}" max="100"></progress></div>`).join('')}</div>${total < 100 ? `<small>已记录 ${Number(total.toFixed(2))}%</small>` : ''}`;
}

function workRows(tasks, options) {
  const { esc, today, signedIn, reviewBusy, icon } = options;
  return sortTasks(tasks).map(task => {
    if (task.reviewCandidateId || task.reviewChanges?.length) return renderTaskRow(task, { escapeHtml: esc, today, reviewBusy, icon });
    const steps = task.subtasks || [], remaining = steps.filter(step => step.status !== 'completed');
    const next = remaining.find(step => (step.dependsOn || []).every(id => steps.some(s => s.id === id && s.status === 'completed')));
    const progress = steps.length ? `步骤 ${steps.length - remaining.length}/${steps.length}${next ? ' · 接下来：' + next.title : ''}` : '';
    return `<article class="course-work"><button type="button" data-task-open="${esc(task.id)}"><strong>${esc(task.title)}</strong><small>${esc(deadlineLabel(task, today))}${task.course ? ' · ' + renderCourseBadge(task.course, { compact: true }) : ''}</small>${progress ? `<span>${esc(progress)}</span>` : ''}</button>${signedIn ? `<button type="button" class="btn quiet" data-study-split="${esc(task.id)}" aria-label="${steps.length ? '重新拆分' : 'AI 拆分'}：${esc(task.title)}">${steps.length ? '重新拆分' : 'AI 拆分'}</button>` : ''}</article>`;
  }).join('');
}

export function renderLearningPage(options) {
  const { tasks, heading, esc, signedIn, planningOnly, tables, profiles, selected, today, icon } = options;
  const pending = tasks.filter(t => !t.recycleBin && !isNotice(t) && t.status !== 'done');
  const schedule = (items, course) => signedIn && items.some(t => !t.reviewCandidateId && canSchedule(t))
    ? `<button class="btn primary" data-study-open ${course ? `data-study-course="${esc(course)}"` : ''}>${icon('calendar')}${course ? '安排本课时间' : '安排学习时间'}</button>` : '';
  const empty = text => `<p class="course-empty">${text}</p>`;
  const signIn = !signedIn ? '<p class="course-empty">登录后可以保存课程信息、拆分任务并安排学习时间。</p>' : '';
  if (planningOnly) return `<section class="learning-page">${heading({ title: '学习规划', actions: schedule(pending) })}${signIn}<section class="course-block"><h2>未完成任务 <small>${pending.length}</small></h2>${workRows(pending, options) || empty('没有未完成的任务。')}${pending.length && !pending.some(canSchedule) ? empty('给未完成的任务设置截止日期后，就可以安排时间。') : ''}</section></section>`;

  const courses = collectCourses(tasks, tables, profiles);
  const course = courses.find(c => c.name === selected) || courses[0];
  const add = signedIn ? '<button class="btn" data-course-add>添加课程</button>' : '';
  const picker = courses.length ? `<label class="course-picker">切换课程<select id="learning-course">${courses.map(c => `<option ${c === course ? 'selected' : ''} value="${esc(c.name)}">${esc(c.name)}</option>`).join('')}</select></label>` : '';
  const top = heading({ title: '我的课程', actions: add });
  if (!course) return `<section class="learning-page">${top}${signIn}${empty('暂无课程。可以添加课程，或从带有课程名的作业和课表开始。')}</section>`;
  const work = course.tasks.filter(t => !isNotice(t) && t.status !== 'done');
  const done = course.tasks.filter(t => !isNotice(t) && t.status === 'done');
  const notices = sortTasks(course.tasks.filter(isNotice));
  const rows = items => items.map(t => renderTaskRow(t, { escapeHtml: esc, today, icon, reviewBusy: options.reviewBusy })).join('');
  const edit = signedIn ? `<button class="btn quiet" data-course-edit="${esc(course.name)}">编辑课程信息</button>` : '';
  return `<section class="learning-page">${top}${signIn}<div class="course-mast"><div>${picker}<h2>${esc(course.name)}</h2></div>${schedule(work, course.name)}</div>
    <div class="course-columns"><div><section class="course-block"><h3>作业 <small>${work.length} 件待完成</small></h3>${workRows(work, options) || empty('本课没有待完成作业。')}${done.length ? `<details><summary>已完成作业 · ${done.length}</summary>${rows(done)}</details>` : ''}</section>
    <section class="course-block"><h3>通知 <small>${notices.length} 条</small></h3>${rows(notices) || empty('本课暂无通知。')}</section></div>
    <aside><section class="course-block"><header><h3>课程信息</h3>${edit}</header>${courseFacts(course, today, esc)}</section>
    <section class="course-block"><h3>成绩构成</h3>${gradingMarkup(course.profile.grading || [], esc)}${signedIn ? `<button class="btn quiet" data-course-edit="${esc(course.name)}" data-focus-grading>编辑成绩构成</button>` : ''}</section></aside></div></section>`;
}

export function createCourseEditor(host, { readCourses, saveCourses, accountVersion, select }) {
  const esc = host.escapeHtml;
  let draft;
  function open(name, grading) {
    const snapshot = readCourses();
    const profile = snapshot.value.find(c => c.name.trim() === name);
    draft = { snapshot, profile, name, epoch: accountVersion() };
    host.openDialog(`<form data-course-form><header class="dialog-header"><h2 id="dialog-heading">${name ? '编辑课程信息' : '添加课程'}</h2><button class="icon-button" type="button" data-action="dialog-close" aria-label="关闭">${host.icon('close')}</button></header>
    <label class="field">课程全名<input name="name" maxlength="120" required ${name ? 'readonly' : ''} value="${esc(name)}"></label>
    <label class="field">任课教师<input name="teacher" maxlength="120" value="${esc(profile?.teacher)}"></label>
    <label class="field">考勤要求<textarea name="attendance" maxlength="2000" rows="2">${esc(profile?.attendance)}</textarea></label>
    <label class="field">成绩构成<textarea name="grading" rows="4" maxlength="3000" placeholder="每行一项，例如：期末项目 40%">${esc((profile?.grading || []).map(w => w.label + ' ' + w.percent + '%').join('\n'))}</textarea></label>
    <p class="form-error" role="alert"></p><footer class="dialog-actions"><button class="btn primary">保存课程信息</button></footer></form>`);
    if (grading) document.querySelector('[data-course-form] [name=grading]').focus();
  }
  async function submit(form) {
    const current = draft;
    if (!current || current.busy || current.epoch !== accountVersion()) return;
    const fields = new FormData(form), name = fields.get('name').trim();
    const error = form.querySelector('[role=alert]');
    const button = form.querySelector('.primary');
    try {
      if (!name) throw new Error('请填写课程名称。');
      if (!current.name && current.snapshot.value.some(c => c.name.trim() === name)) throw new Error('这门课程已存在，请打开课程后编辑。');
      const grading = fields.get('grading').trim().split(/\r?\n/).filter(line => line.trim()).map(line => {
        const match = line.trim().match(/^(.+?)\s+(\d+(?:\.\d{1,2})?)\s*[%％]?$/u);
        if (!match || !Number(match[2]) || match[1].length > 80) throw new Error('每行填写评分项目、空格和百分比，例如「期末项目 40%」。');
        return { label: match[1].trim(), percent: Number(match[2]) };
      });
      if (grading.length > 30 || grading.reduce((sum, item) => sum + Math.round(item.percent * 100), 0) > 10000)
        throw new Error('评分项目最多 30 项，合计占比不能超过 100%。');
      const profile = { id: current.profile?.id || newId(), notes: '', url: '', ...current.profile, name, teacher: fields.get('teacher').trim(), attendance: fields.get('attendance').trim(), grading };
      current.busy = true; button.disabled = true; error.textContent = '';
      const value = current.snapshot.value.filter(c => c.id !== profile.id).concat(profile);
      await saveCourses(value, current.snapshot.version);
      if (current.epoch !== accountVersion()) return;
      host.render();
      if (!form.isConnected || !form.closest('dialog[open]')) return;
      select(name); host.closeDialog(); host.render(); host.toast('课程信息已保存');
    } catch (e) {
      if (current.epoch !== accountVersion()) return;
      if (e.status === 409) {
        error.textContent = '课程信息已在其他窗口修改。请复制需要保留的内容，关闭后重新打开编辑。';
        await host.refresh().catch(() => {});
      } else error.textContent = e.message;
    } finally { current.busy = false; button.disabled = false; }
  }
  return { open, submit };
}

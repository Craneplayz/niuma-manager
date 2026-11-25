import { deadlineLabel, isNotice, sortTasks } from './task-model.js';
import { updateDOM } from './dom-update.js';

// The service may queue a model call for three minutes before the call itself starts.
const modelProposalTimeoutMs = 280_000;
const weekdays = [[1, '一'], [2, '二'], [3, '三'], [4, '四'], [5, '五'], [6, '六'], [0, '日']];
const inShanghai = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
function localTime(iso) {
  if (typeof iso !== 'string' || !iso) return '';
  const instant = new Date(iso);
  return Number.isFinite(instant.getTime()) ? inShanghai.format(instant) : '';
}

export function studySessions(tasks) {
  return tasks.flatMap(task => isNotice(task) || task.recycleBin ? [] : (task.plans || []).flatMap(plan => {
    const start = localTime(plan?.startAt), end = localTime(plan?.endAt);
    if (!start || !end) return [];
    return [{ kind: 'study', taskId: task.id, title: plan.title || task.title, date: start.slice(0, 10),
      startTime: start.slice(11), endTime: end.slice(11), taskTitle: task.title }];
  })).sort((a, b) => (a.date + a.startTime).localeCompare(b.date + b.startTime));
}

export function studySessionMarkup(event, esc) {
  return `<button type="button" class="calendar-task study-session" data-task-open="${esc(event.taskId)}" aria-label="学习安排：${esc(event.title)}，${esc(event.startTime)} 至 ${esc(event.endTime)}"><span class="study-session-time">${esc(event.startTime)}–${esc(event.endTime)}</span><span class="calendar-task-title">${esc(event.title)}</span></button>`;
}

export function taskStepsMarkup(task, esc, enabled = true) {
  if (!task.id || task.reviewCandidateId || isNotice(task)) return '';
  const steps = (task.subtasks || []).filter(step => step?.id && step.title);
  if (!steps.length && (!enabled || task.status === 'done')) return '';
  const completed = steps.filter(step => step.status === 'completed');
  const button = enabled && task.status !== 'done'
    ? `<button type="button" class="btn quiet" data-study-split="${esc(task.id)}">${steps.length ? '重新拆分' : 'AI 拆分'}</button>` : '';
  return `<section id="drawer-study-steps" class="study-steps"><header><h3>任务步骤${steps.length ? ` <small>${completed.length}/${steps.length}</small>` : ''}</h3>${button}</header>${steps.map(step => {
    const depends = (step.dependsOn || []).map(id => steps.find(s => s.id === id)).filter(Boolean);
    const blockedBy = depends.filter(s => s.status !== 'completed');
    const following = completed.filter(s => s.dependsOn?.includes(step.id));
    const reason = step.status === 'completed'
      ? (following.length ? '先取消完成：' + following.map(s => s.title).join('、') : '')
      : (blockedBy.length ? '先完成：' + blockedBy.map(s => s.title).join('、') : '');
    return `<label class="study-step"><input type="checkbox" data-study-step="${esc(step.id)}" data-study-task="${esc(task.id)}" ${step.status === 'completed' ? 'checked' : ''} ${!enabled || reason || task.status === 'done' ? 'disabled' : ''}><span><strong>${esc(step.title)}</strong>${step.outcome ? `<small>${esc(step.outcome)}</small>` : ''}${reason ? `<small>${esc(reason)}</small>` : ''}</span>${step.estimatedMinutes ? `<small>${esc(step.estimatedMinutes)} 分</small>` : ''}</label>`;
  }).join('')}</section>`;
}

export function canSchedule(task) {
  const steps = task.subtasks || [];
  return task.status !== 'done' && !task.recycleBin && !isNotice(task) && task.due
    && (!steps.length || steps.some(step => step?.status !== 'completed'));
}

export function createStudyPlanning(host, { api, flushStore, savePlanning, readAvailability, accountVersion, session }) {
  const esc = host.escapeHtml;
  const dialog = document.getElementById('dialog');
  const taskFor = id => host.getState().tasks.find(item => item.id === id);
  const candidates = course => sortTasks(host.getState().tasks.filter(task =>
    canSchedule(task) && (!course || task.course?.trim() === course)));
  let active = null, requestController = null;
  const busyStepTasks = new Set();
  window.addEventListener("niuma:account-reset", () => busyStepTasks.clear());
  const activePanel = panel => active === panel && panel.epoch === accountVersion() && dialog.open && !!dialog.querySelector('[data-study-panel]');
  function reset() {
    active = null;
    requestController?.abort();
    requestController = null;
  }

  function availabilityForm(panel) {
    const { weekdays: days, startTime, endTime } = panel.availability;
    const items = candidates(panel.course);
    return `<fieldset class="study-picks"><legend>选择任务</legend>${items.map(task => `<label><input type="checkbox" name="taskId" value="${esc(task.id)}" ${panel.selected.includes(task.id) ? 'checked' : ''}><span><strong>${esc(task.title)}</strong><small>${esc(deadlineLabel(task))}${task.plans?.length ? ' · 已有安排，选中可重排' : ''}</small></span></label>`).join('') || '<p class="status-note">给一项未完成的任务设置截止日期后，即可安排学习时间。</p>'}</fieldset>
      <fieldset class="study-window"><legend>本次可用时间 <small>北京时间</small></legend><div class="study-days">${weekdays.map(([id, label]) => `<label><input type="checkbox" name="weekday" value="${id}" ${days.includes(id) ? 'checked' : ''}><span aria-label="星期${label}">${label}</span></label>`).join('')}</div><div class="study-hours"><label class="field">从<input type="time" name="startTime" value="${esc(startTime)}" required></label><label class="field">至<input type="time" name="endTime" value="${esc(endTime)}" required></label></div></fieldset>
      <footer class="dialog-actions"><button class="btn primary" ${items.length ? '' : 'disabled'}>生成排程预览</button></footer>`;
  }
  function splitForm(panel) {
    const questions = panel.proposal?.questions || [];
    if (!questions.length) return `<label class="field">补充进度或要求（可选）<textarea name="context" maxlength="4000" rows="3" placeholder="例如：资料已收齐，还需要写正文和做演示。">${esc(panel.context || '')}</textarea></label><footer class="dialog-actions"><button class="btn primary">生成步骤</button></footer>`;
    return `${questions.map((q, i) => `<label class="field">${esc(q.prompt)}<input name="answer${i}" maxlength="1000" required list="study-options-${i}" value="${esc(panel.answers?.[i] || '')}"></label><datalist id="study-options-${i}">${q.answer_options.map(option => `<option value="${esc(option)}"></option>`).join('')}</datalist>`).join('')}<footer class="dialog-actions"><button class="btn primary">继续拆分</button></footer>`;
  }
  function canApplySplit(panel) {
    const proposal = panel.proposal;
    return proposal?.requestId && proposal.taskId === panel.id
      && (proposal.status === 'ready_to_plan' || proposal.status === 'no_split');
  }
  function splitPreview(proposal) {
    const steps = proposal.plan.subtasks;
    const singleStepNote = proposal.requestId
      ? '确认后，这一步将替换原有的未完成步骤。'
      : '这项任务可以直接完成。';
    return `<p class="study-summary">${esc(proposal.status === 'no_split' ? proposal.assessment.summary : proposal.plan.strategy)}</p><ol class="study-proposed-steps">${steps.map(step => `<li><strong>${esc(step.title)}</strong> <small>${esc(step.duration_minutes)} 分钟</small><p>${esc(step.outcome)}</p>${step.depends_on.length ? `<small>先完成 · ${esc(step.depends_on.map(id => steps.find(s => s.id === id)?.title).filter(Boolean).join('、'))}</small>` : ''}${step.assumption ? '<small>包含假设，请核对</small>' : ''}</li>`).join('')}</ol>${noticeList([...(proposal.assessment.assumptions || []), ...(proposal.plan.warnings || [])])}${proposal.status === 'no_split' ? `<p class="status-note">${singleStepNote}</p>` : ''}`;
  }
  function noticeList(warnings) {
    return warnings.length ? `<ul class="study-warnings">${warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul>` : '';
  }
  function schedulePreview(proposal) {
    const grouped = new Map();
    for (const block of [...proposal.blocks].sort((a, b) => Date.parse(a.startAt) - Date.parse(b.startAt))) {
      const [date, time] = localTime(block.startAt).split(' ');
      if (!grouped.has(date)) grouped.set(date, []);
      grouped.get(date).push({ ...block, time });
    }
    return `<ul class="study-estimates">${Object.entries(proposal.estimates).map(([id, minutes]) => `<li><strong>${esc(taskFor(id)?.title || id)}</strong><span>预计 ${esc(minutes)} 分钟</span></li>`).join('')}</ul>${[...grouped].map(([day, blocks]) => `<section class="study-proposed-day"><h3>${esc(day)} <small>北京时间</small></h3>${blocks.map(b => `<div><time>${esc(b.time)}–${esc(localTime(b.endAt).slice(11))}</time><span>${esc(b.title)}</span></div>`).join('')}</section>`).join('')}${noticeList(proposal.warnings)}${proposal.replannedTaskIds?.length ? `<p class="status-note">确认后替换尚未开始且未锁定的安排 · ${esc(proposal.replannedTaskIds.map(id => taskFor(id)?.title || id).join('、'))}</p>` : ''}${!grouped.size && !proposal.warnings.length ? '<p class="status-note">所选任务没有可安排的时段。</p>' : ''}`;
  }
  function display(panel, opening = false) {
    const split = panel.kind === 'split', proposal = panel.proposal;
    const preview = proposal && (!split || proposal.status !== 'needs_clarification');
    const title = split ? 'AI 任务拆分' : 'AI 学习排程';
    const header = `<header class="dialog-header"><h2 id="dialog-heading">${title}</h2><button type="button" class="icon-button" data-action="dialog-close" aria-label="关闭${title}">${host.icon('close')}</button></header>`;
    const canApply = split ? canApplySplit(panel) : proposal?.blocks?.length;
    const body = panel.busy ? `<p class="study-wait" role="status">${panel.busy}</p>` : preview
      ? (split ? splitPreview(proposal) : schedulePreview(proposal)) + `<footer class="dialog-actions"><button type="button" class="btn quiet" data-study-back>调整${split ? '要求' : '条件'}</button>${canApply ? `<button type="button" class="btn primary" data-study-apply>${split ? '确认步骤' : '添加到日历'}</button>` : ''}</footer>`
      : `<form data-study-form>${split ? splitForm(panel) : availabilityForm(panel)}</form>`;
    const html = `<section data-study-panel>${header}${split ? `<h3 class="study-task-name">${esc(taskFor(panel.id)?.title || '')}</h3>` : ''}${panel.error ? `<p class="form-error" role="alert">${esc(panel.error)}</p>` : ''}${body}</section>`;
    if (opening) host.openDialog(html);
    else if (activePanel(panel)) updateDOM(dialog, html);
  }
  function open(kind, id, course) {
    if (!session.user) { host.toast('登录后可以使用 AI 规划'); return; }
    reset();
    const task = id && taskFor(id);
    if (kind === 'split' && (!task || isNotice(task) || task.status === 'done')) return;
    const panel = { kind, id, course, epoch: accountVersion(), conversation: [], availability: readAvailability(),
      selected: candidates(course).filter(t => !t.plans?.length && !t.planningExcluded).map(t => t.id) };
    active = panel;
    display(panel, true);
  }

  async function generate(panel, form) {
    let url, body, conversation = panel.conversation;
    if (panel.kind === 'split') {
      const questions = panel.proposal?.questions || [];
      panel.answers = questions.map((_, i) => String(form.get('answer' + i) || '').trim());
      if (questions.length) conversation = [...conversation,
        { role: 'assistant', content: questions.map(q => q.prompt).join('\n') },
        { role: 'user', content: panel.answers.map((a, i) => questions.length > 1 ? `${i + 1}. ${a}` : a).join('\n') }];
      else {
        panel.context = String(form.get('context') || '').trim();
        conversation = panel.context ? [{ role: 'user', content: panel.context }] : [];
      }
      if (conversation.length > 20) throw new Error('这一轮对话已满，请关闭后重新拆分并汇总之前的进度。');
      url = '/api/decomposition/proposals';
      body = { taskId: panel.id, conversation };
    } else {
      panel.selected = form.getAll('taskId');
      panel.availability = { startTime: form.get('startTime'), endTime: form.get('endTime'), weekdays: form.getAll('weekday').map(Number) };
      if (!panel.selected.length) throw new Error('请选择至少一项任务。');
      if (!panel.availability.weekdays.length || panel.availability.startTime >= panel.availability.endTime)
        throw new Error('请选择可用星期，并将结束时间设在开始时间之后。');
      url = '/api/planning/proposals';
      body = { taskIds: panel.selected, availability: panel.availability,
        replan: panel.selected.some(id => taskFor(id)?.plans?.length || taskFor(id)?.planningExcluded) };
    }
    panel.busy = panel.kind === 'split' ? '正在分析任务步骤…' : '正在估算工时、安排学习时段…';
    display(panel);
    await flushStore();
    if (!activePanel(panel)) return;
    requestController = new AbortController();
    const proposal = await api(url, body, 'POST', { signal: requestController.signal, timeoutMs: modelProposalTimeoutMs });
    if (!activePanel(panel)) return;
    if (panel.kind === 'split' && proposal.taskId !== panel.id) throw new Error('拆分结果与所选任务不匹配，请重新生成。');
    panel.proposal = proposal;
    panel.conversation = conversation;
    panel.answers = [];
  }
  async function perform(panel, action) {
    if (!activePanel(panel) || panel.busy) return;
    panel.error = '';
    try { await action(); }
    catch (error) {
      if (!activePanel(panel)) return;
      panel.error = error.message;
      if (error.status === 409) { panel.proposal = null; await host.refresh().catch(() => {}); }
    } finally {
      if (activePanel(panel)) { panel.busy = ''; display(panel); }
    }
  }

  async function click(button) {
    if (Object.hasOwn(button.dataset, 'studyOpen')) { open('schedule', null, button.dataset.studyCourse); return true; }
    if (Object.hasOwn(button.dataset, 'studySplit')) { open('split', button.dataset.studySplit); return true; }
    const panel = active;
    if (!panel || !activePanel(panel)) return false;
    if (Object.hasOwn(button.dataset, 'studyBack')) {
      panel.error = ''; panel.proposal = null; panel.answers = []; display(panel); return true;
    }
    if (!Object.hasOwn(button.dataset, 'studyApply')) return false;
    await perform(panel, async () => {
      const split = panel.kind === 'split', proposal = panel.proposal;
      if (!proposal?.requestId || (split && !canApplySplit(panel))) return;
      panel.busy = '正在保存…'; display(panel);
      const path = split ? '/api/decomposition/apply' : '/api/planning/apply';
      const body = { requestId: proposal.requestId, ...(split ? { taskId: panel.id } : {}) };
      await savePlanning(() => api(path, body), () => activePanel(panel));
      if (!activePanel(panel)) return;
      host.closeDialog();
      if (!split && proposal.blocks.length) host.showStudyDay(localTime(proposal.blocks[0].startAt).slice(0, 10));
      host.toast(split ? '任务步骤已保存' : '学习时段已加入日历');
    });
    return true;
  }
  async function setStep(input) {
    const epoch = accountVersion(), taskId = input.dataset.studyTask;
    if (busyStepTasks.has(taskId) || !session.user) { input.checked = !input.checked; return; }
    const completed = input.checked;
    input.disabled = true;
    busyStepTasks.add(taskId);
    try {
      await savePlanning(version => api('/api/decomposition/steps/status', { taskId, stepId: input.dataset.studyStep,
        status: completed ? 'completed' : 'pending', version }), () => epoch === accountVersion() && !!taskFor(taskId));
    } catch (error) {
      if (epoch === accountVersion()) {
        if (input.isConnected) { input.checked = !completed; input.disabled = false; }
        if (error.status === 409) await host.refresh().catch(() => {});
        host.toast(error.message);
      }
    } finally { if (epoch === accountVersion()) busyStepTasks.delete(taskId); }
  }
  function submit(form) {
    if (!active || !Object.hasOwn(form.dataset, 'studyForm')) return false;
    const panel = active, data = new FormData(form);
    void perform(panel, () => generate(panel, data));
    return true;
  }
  dialog.addEventListener('close', () => { if (!dialog.open) reset(); });
  return { click, submit, setStep, reset };
}

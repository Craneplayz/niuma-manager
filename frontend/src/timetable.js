const DAY = 86400000;
const labels = { all: '每周', odd: '单周', even: '双周' };

export function classEvents(tables, from, to) {
  const events = [];
  for (const table of tables) {
    const first = Date.parse(`${table.firstMonday}T00:00:00Z`);
    const last = Date.parse(`${table.lastDate}T00:00:00Z`);
    for (const meeting of table.meetings) {
      for (let day = first + (meeting.weekday - 1) * DAY; day <= last; day += 7 * DAY) {
        const date = new Date(day).toISOString().slice(0, 10);
        const week = Math.floor((day - first) / (7 * DAY)) + 1;
        if (date < from || date > to || meeting.parity !== 'all' && (week % 2 === 1) !== (meeting.parity === 'odd')) continue;
        events.push({ ...meeting, kind: 'class', date, week, id: `${table.id}:${meeting.id}:${date}` });
      }
    }
  }
  const exams = new Set();
  for (const table of tables) for (const meeting of table.meetings) {
    const exam = meeting.exam;
    if (!exam || exam.date < from || exam.date > to) continue;
    const id = JSON.stringify([meeting.title, exam.date, exam.period]);
    if (exams.has(id)) continue;
    exams.add(id);
    events.push({ id, kind: 'exam', title: meeting.title, date: exam.date, period: exam.period,
      startTime: '', endTime: '', location: '', notes: [meeting.notes, meeting.examNote].filter(Boolean).join(' ') });
  }
  return events.sort((a, b) => `${a.date}${a.startTime}`.localeCompare(`${b.date}${b.startTime}`));
}

export function classMarkup(event, esc, details = false) {
  const time = event.kind === 'exam' ? `考试 · ${event.period || '时刻待定'}` : `${event.startTime}–${event.endTime}`;
  const notes = [event.notes, event.examNote].filter(Boolean).join(' ');
  return `<div class="calendar-class${event.kind === 'exam' ? ' is-exam' : ''}"><span class="calendar-class-time">${esc(time)}</span><strong>${esc(event.title)}</strong>${event.location ? `<span>${esc(event.location)}</span>` : ''}${details && notes ? `<small>${esc(notes)}</small>` : ''}</div>`;
}

export function createTimetableUI(host, { api, session, accountVersion, readStoredTimetable, acceptStoredTimetable }) {
  const esc = host.escapeHtml;
  let stored = { value: [], version: 0 }, preview = null, file = '', filename = '', error = '', busy = false, generation = 0;
  let firstMonday = '', lastDate = '', term = null;
  const current = (epoch, serial) => epoch === accountVersion() && serial === generation;
  const visible = () => host.isDialogOpen('[data-timetable-panel]');
  const classes = () => classEvents(readStoredTimetable().value, '0001-01-01', '9999-12-31');
  function reset() {
    generation++;
    stored = { value: [], version: 0 };
    preview = null; file = ''; filename = ''; error = ''; busy = false; firstMonday = ''; lastDate = ''; term = null;
  }
  function paint() {
    const meetings = preview || [];
    const disabled = busy ? 'disabled' : '';
    const rows = meetings.map(m => `<article class="timetable-meeting"><div><strong>${esc(m.title)}</strong><p>周${'一二三四五六日'[m.weekday - 1]} · 第${m.startPeriod}${m.endPeriod !== m.startPeriod ? '–' + m.endPeriod : ''}节 · ${labels[m.parity]}${m.location ? ' · ' + esc(m.location) : ''}</p>${m.notes ? `<small>${esc(m.notes)}</small>` : ''}${m.exam ? `<p class="timetable-exam-note">考试：${esc(m.exam.date)} ${esc(m.exam.period || '具体时刻待定')}</p>` : m.examNote ? `<p>${esc(m.examNote)}</p>` : ''}</div><div class="timetable-clocks"><label>开始<input type="time" name="start:${m.id}" value="${m.startTime}" required ${disabled}></label><label>结束<input type="time" name="end:${m.id}" value="${m.endTime}" required ${disabled}></label></div></article>`).join('');
    const form = preview ? `<form data-timetable-form><div class="timetable-dates"><label class="field">第一周的星期一<input name="firstMonday" type="date" value="${esc(firstMonday)}" required ${disabled}></label><label class="field">最后上课日期<input name="lastDate" type="date" value="${esc(lastDate)}" required ${disabled}></label></div><p class="timetable-hint">作息已预填校本部时间，可调整。默认考试周 ${esc(term?.examStart || '')} 至 ${esc(term?.examEnd || '')}；具体考试以备注为准，未给出钟点时考试当天不自动排任务。</p><div class="timetable-preview">${rows}</div><footer class="dialog-actions"><button type="submit" class="btn primary" ${disabled}>确认导入 ${meetings.length} 个上课时段</button></footer></form>` : '';
    const saved = stored.value.length ? `<h3>已导入</h3><div class="timetable-saved">${stored.value.map(t => `<article><div><strong>${t.meetings.length} 个上课时段</strong><p>${esc(t.firstMonday)} 至 ${esc(t.lastDate)}</p></div><button type="button" class="btn quiet" data-timetable-delete="${esc(t.id)}" ${disabled}>删除课表</button></article>`).join('')}</div>` : '';
    host.openDialog(`<section data-timetable-panel><header class="dialog-header"><h2>导入课表</h2><button type="button" class="icon-button" data-action="dialog-close" aria-label="关闭课表">${host.icon('close')}</button></header><p>上传教务系统导出的 XLS，课程与考试将显示在日历中。</p><div class="timetable-file"><label class="btn" aria-disabled="${busy}">${preview ? '更换课表' : '选择 XLS 课表'}<input class="sr-only" data-timetable-file aria-label="选择课表文件" type="file" accept=".xls,application/vnd.ms-excel" ${disabled}></label>${filename ? `<span>${esc(filename)}</span>` : ''}</div>${error ? `<p class="form-error" role="alert">${esc(error)}</p>` : ''}${busy ? '<p role="status">正在处理课表…</p>' : ''}${form}${saved}</section>`);
  }
  async function open() {
    if (!session.user) { host.toast('请先登录，再导入课表'); return; }
    generation++; preview = null; file = ''; filename = ''; error = ''; busy = true;
    const epoch = accountVersion(), serial = generation;
    paint();
    try {
      const value = await api('/api/timetable');
      if (!current(epoch, serial)) return;
      stored = value; acceptStoredTimetable(value);
    } catch (e) { if (current(epoch, serial)) error = e.message; }
    finally {
      if (current(epoch, serial)) { busy = false; if (visible()) paint(); host.render(); }
    }
  }
  async function change(event) {
    if (event.target.dataset?.timetableFile === undefined || busy) return;
    const selected = event.target.files?.[0];
    if (!selected) return;
    const epoch = accountVersion(), serial = ++generation;
    preview = null; file = ''; filename = selected.name; error = ''; busy = true; paint();
    try {
      if (!selected.name.toLowerCase().endsWith('.xls') || selected.size > 512 * 1024) throw new Error('请选择不超过512 KB的 XLS 课表');
      const bytes = new Uint8Array(await selected.arrayBuffer());
      if (!current(epoch, serial) || !visible()) return;
      let text = '';
      for (let i = 0; i < bytes.length; i += 16384) text += String.fromCharCode(...bytes.subarray(i, i + 16384));
      const encoded = btoa(text);
      const result = await api('/api/timetable/preview', { file: encoded });
      if (!current(epoch, serial) || !visible()) return;
      file = encoded; preview = result.meetings; term = result.term;
      if (!firstMonday) firstMonday = result.term.firstMonday;
      if (!lastDate) lastDate = result.term.lastDate;
    } catch (e) { if (current(epoch, serial)) error = e.message; }
    finally { if (current(epoch, serial)) { busy = false; if (visible()) paint(); } }
  }
  async function submit(event) {
    if (event.target.dataset?.timetableForm === undefined) return;
    event.preventDefault();
    if (busy || !preview) return;
    const form = new FormData(event.target), epoch = accountVersion(), serial = generation;
    firstMonday = form.get('firstMonday'); lastDate = form.get('lastDate');
    const clocks = Object.fromEntries(preview.map(m => [m.id, [form.get(`start:${m.id}`), form.get(`end:${m.id}`)]]));
    preview = preview.map(m => ({ ...m, startTime: clocks[m.id][0], endTime: clocks[m.id][1] }));
    busy = true; error = ''; paint();
    try {
      const result = await api('/api/timetable', { file, firstMonday, lastDate, clocks, version: stored.version });
      if (!current(epoch, serial)) return;
      stored = result; acceptStoredTimetable(result); preview = null; file = ''; filename = '';
      host.toast(result.duplicate ? '这份课表已导入，无需重复添加' : '课表已导入日历');
      host.render();
    } catch (e) { if (current(epoch, serial)) error = e.message; }
    finally { if (current(epoch, serial)) { busy = false; if (visible()) paint(); } }
  }
  async function click(event) {
    const button = event.target.closest?.('[data-timetable-open],[data-timetable-delete]');
    if (!button || button.dataset.timetableOpen === undefined && button.dataset.timetableDelete === undefined) return;
    if (button.dataset.timetableOpen !== undefined) { await open(); return; }
    if (busy || !confirm('删除这份课表的全部上课安排？任务不会删除。')) return;
    const epoch = accountVersion(), serial = generation;
    busy = true; error = ''; paint();
    try {
      const result = await api('/api/timetable', { id: button.dataset.timetableDelete, version: stored.version }, 'DELETE');
      if (!current(epoch, serial)) return;
      stored = result; acceptStoredTimetable(result); host.render(); host.toast('课表已删除');
    } catch (e) { if (current(epoch, serial)) error = e.message; }
    finally { if (current(epoch, serial)) { busy = false; if (visible()) paint(); } }
  }
  document.addEventListener('click', click);
  document.addEventListener('change', change);
  document.addEventListener('submit', submit);
  return { reset, events: classes, day(date) {
    const events = classes().filter(e => e.date === date);
    return events.length ? `<section class="timetable-day" aria-label="当日课程"><h3>${events.some(e => e.kind === 'exam') ? '课程与考试' : '上课安排'}</h3>${events.map(e => classMarkup(e, esc, true)).join('')}</section>` : '';
  } };
}

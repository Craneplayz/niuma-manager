import test from 'node:test';
import assert from 'node:assert/strict';
import { isBulkSelectable, selectedBulkTasks, recoveredBulkTasks, bulkLabels, renderBulkToolbar, renderBulkDeadlineForm } from '../src/task-bulk-ui.js';
import { renderTaskRow, renderTaskList } from '../src/task-views.js';
import { filterTasks } from '../src/task-model.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const ordinary = { id: 'task', title: '合成任务', status: 'todo', course: '数学', due: '2026-10-24' };
const candidate = { ...ordinary, id: 'review:new', reviewCandidateId: 'new', title: '待核对新增' };
const change = { ...candidate, id: 'review:change', reviewCandidateId: 'change', reviewChange: true };
const notice = { ...ordinary, id: 'notice', content_type: 'notice', title: '合成通知' };
const toolbar = (tasks, extra = {}) => renderBulkToolbar({ tasks, available: tasks, selected: new Set(tasks.map(task => task.id)), escapeHtml: esc, ...extra });

test('bulk selection obeys query scope and excludes changes, missing identities and later imports', () => {
  const selection = new Set(['task', 'review:new', 'review:change', 'deleted']);
  const arrival = { ...ordinary, id: 'arrival', title: '之后导入' };
  const all = filterTasks([ordinary, candidate, change, notice, arrival], { scope: 'all', today: '2026-10-24' });
  assert.deepEqual(selectedBulkTasks(all, selection).map(task => task.id).sort(), ['review:new', 'task']);
  const searched = filterTasks(all, { query: '新增', today: '2026-10-24' });
  assert.deepEqual(selectedBulkTasks(searched, selection), [candidate]);
  assert.equal(isBulkSelectable(change), false);
  assert.equal(isBulkSelectable({}), false);
  assert.equal(selection.has('arrival'), false);
});

test('mixed notices and tasks use explicit completion and reading language', () => {
  assert.equal(bulkLabels([ordinary, notice]).complete, '完成 / 已阅读');
  assert.equal(bulkLabels([notice]).reopen, '标记未阅读');
  assert.equal(bulkLabels([ordinary]).trash, '移入回收站');
  assert.equal(bulkLabels([ordinary, candidate]).trash, '删除 / 忽略');
});

test('candidate selections expose conversion semantics and cannot be reopened', () => {
  const html = toolbar([candidate, { ...ordinary, status: 'done' }]);
  assert.match(html, /data-bulk-action="reopen"[^>]*disabled/);
  assert.match(html, /待核对内容完成或修改日期后转为普通任务/);
  assert.match(html, /data-bulk-action="complete"(?![^>]*disabled)/);
});

test('empty, saving and blocked selections cannot dispatch writes', () => {
  for (const extra of [{}, { busy: true }, { blocked: true }]) {
    const html = toolbar(extra.busy || extra.blocked ? [ordinary] : [], extra);
    for (const action of ['complete', 'reopen', 'deadline', 'trash']) assert.match(html, new RegExp(`data-bulk-action="${action}"[^>]*disabled`));
  }
});

test('ordinary and new candidate selection replaces completion without accepting proposed changes', () => {
  const source = { ...ordinary, reviewChanges: [{ id: 'postpone', action: 'postpone', proposedDate: '10 月 26 日' }] };
  const original = structuredClone(source);
  const html = renderTaskList([source, candidate, change], { today: '2026-10-24', bulk: { active: true, selected: new Set(['task', 'review:new']) } });
  assert.match(html, /data-bulk-select="task"[^>]*checked/);
  assert.match(html, /data-bulk-select="review:new"[^>]*checked/);
  assert.doesNotMatch(html, /data-bulk-select="review:change"/);
  assert.match(html, /data-import-review="postpone"/);
  assert.match(html, /data-import-review="change"/);
  assert.deepEqual(source, original);
});

test('candidate locks apply to the affected candidate and leave other candidates usable', () => {
  const options = { reviewBusy: id => id === 'new', bulk: { active: true, selected: new Set() } };
  assert.match(renderTaskRow(candidate, options), /data-bulk-select="review:new"[^>]*disabled/);
  const other = renderTaskRow({ ...candidate, id: 'review:other', reviewCandidateId: 'other' }, options);
  assert.doesNotMatch(other, /data-bulk-select="review:other"[^>]*disabled/);
});

test('trash recovery uses an independent selection surface and batch action', () => {
  const html = toolbar([ordinary], { trash: true });
  assert.match(html, /data-bulk-action="restore" data-bulk-context="trash"/);
  assert.doesNotMatch(html, /data-bulk-action="trash"/);
  assert.doesNotMatch(html, /data-bulk-action="complete"/);
});

test('rendered task selections escape titles and deadline form explains date precision', () => {
  const html = renderTaskRow({ ...ordinary, title: '"><script>unsafe</script>' }, { bulk: { active: true, selected: new Set() } });
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /aria-label="选择：&quot;&gt;&lt;script&gt;/);
  const form = renderBulkDeadlineForm(2);
  assert.match(form, /type="date" name="date"/);
  assert.doesNotMatch(form, /name="date"[^>]*required/);
  assert.match(form, /已有具体时刻保留/);
  assert.match(form, /清除日期时同时清除时刻/);
});


test('recovered undo locks new task identities instead of already-applied candidate projections', () => {
  const created = { ...ordinary, id: 'created', recognitionHistory: [{ storageId: 'new' }] };
  const result = recoveredBulkTasks({ tasks: [created], trash: [ordinary] }, ['review:new', 'created', 'task'], [candidate]);
  assert.deepEqual(result.map(task => task.id), ['created', 'task']);
  assert.equal(result[0], created);
});

test('recovered ignore undo retains the old candidate or its identity after a full reload', () => {
  assert.equal(recoveredBulkTasks({ tasks: [], trash: [] }, ['review:new'], [candidate])[0], candidate);
  assert.deepEqual(recoveredBulkTasks({ tasks: [], trash: [] }, ['review:new']), [{ id: 'review:new', reviewCandidateId: 'new' }]);
});

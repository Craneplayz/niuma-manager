import test from 'node:test';
import assert from 'node:assert/strict';
import { projectReviewTasks } from '../src/review-tasks.js';
import { renderTaskRow } from '../src/task-views.js';
import { filterTasks } from '../src/task-model.js';

const today = '2026-10-20';
const item = (id, fields = {}) => ({ id, state: 'pending', version: 1, candidate: {
  title: '课程报告', course_or_project: '数据结构', notice_type: 'create', content_type: 'task',
  deadline: { kind: 'deadline', status: 'explicit', precision: 'date', value: '2026-10-21' },
  sources: [{ platform: 'teaching_web' }], ...fields,
} });

test('pending rows participate in task filters without entering or modifying saved tasks', () => {
  const tasks = [{ id: 'saved', title: '原任务', status: 'todo', due: today }];
  const before = structuredClone(tasks);
  const rows = projectReviewTasks(tasks, [item('pending'), { ...item('ignored'), state: 'ignored' }, item('pending')], today);
  assert.equal(rows.length, 2);
  assert.deepEqual(tasks, before);
  assert.equal(rows[1].reviewCandidateId, 'pending');
  assert.equal(rows[1].due, '2026-10-21');
  assert.equal(filterTasks(rows, { query: '课程报告', course: '数据结构', source: '教学网', scope: 'all', today }).length, 1);
  assert.equal(filterTasks(rows, { scope: 'done', today }).length, 0);
});

test('inferred dates remain unconfirmed and notices appear only in the notice list', () => {
  const uncertain = item('uncertain', { deadline: { kind: 'deadline', status: 'inferred', precision: 'date', value: '2026-10-19' } });
  const rows = projectReviewTasks([], [uncertain, item('notice', { content_type: 'notice' })], today);
  assert.equal(rows[0].due, '');
  assert.match(rows[0].reviewDateLabel, /日期待核对/);
  assert.equal(filterTasks(rows, { scope: 'near', today }).some(row => row.id === 'review:uncertain'), false);
  assert.equal(filterTasks(rows, { scope: 'notices', today }).length, 1);
  assert.equal(filterTasks(rows, { scope: 'all', today }).length, 1);
});

test('only explicit target identity attaches a change and never replaces confirmed fields', () => {
  const original = { id: 'target', title: '原任务', status: 'todo', due: today, dueTime: '18:00' };
  const rows = projectReviewTasks([original], [item('change', { notice_type: 'postpone', target_reference: 'target' }), item('unlinked', { notice_type: 'cancel', target_reference: '原任务' })], today);
  assert.equal(rows[0].due, today);
  assert.equal(rows[0].dueTime, '18:00');
  assert.deepEqual(rows[0].reviewChanges.map(change => change.id), ['change']);
  assert.equal(rows[1].reviewChange, true);
  assert.equal(original.reviewChanges, undefined);
});

test('pending task rows escape content and expose ordinary completion and editing plus ignore', () => {
  const row = projectReviewTasks([], [item('unsafe', { title: '<img onerror=alert(1)>', course_or_project: '<script>' })], today)[0];
  const html = renderTaskRow(row, { today, dateDraggable: true });
  assert.match(html, /待核对/);
  assert.match(html, /data-task-complete="review:unsafe"/);
  assert.match(html, /data-task-open="review:unsafe"/);
  assert.match(html, /data-import-ignore="unsafe"/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /data-calendar-drag-task|<script>|<img/);
});

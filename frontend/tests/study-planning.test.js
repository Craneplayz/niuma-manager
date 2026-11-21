import test from 'node:test';
import assert from 'node:assert/strict';
import { studySessions, taskStepsMarkup } from '../src/study-planning.js';
import { renderCalendar } from '../src/calendar.js';

test('calendar shows the saved study interval on its Beijing date even when the endpoint uses UTC', () => {
  const sessions = studySessions([{ id: 'course', title: '课程', plans: [
    { title: '做实验', startAt: '2026-12-05T16:30:00Z', endAt: '2026-12-05T17:00:00Z' },
    null, { title: '旧安排', startAt: '2026-12-05T16:30:00Z', endAt: null },
    { title: '坏数据', startAt: '', endAt: '' }] }]);
  assert.equal(sessions.length, 1);
  assert.equal(sessions[0].date, '2026-12-06');
  const markup = renderCalendar({ sessions, today: '2026-12-06' });
  assert.match(markup, /1 段学习安排/);
  assert.match(markup, /00:30–01:00/);
});
test('notices and unsaved candidates cannot be split; legacy tasks work without subtasks', () => {
  assert.equal(taskStepsMarkup({ id: 'one', content_type: 'notice' }, String), '');
  assert.equal(taskStepsMarkup({ id: 'two', reviewCandidateId: 'pending' }, String), '');
  assert.match(taskStepsMarkup({ id: 'three' }, String), /AI 拆分/);
});

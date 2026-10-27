import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { classEvents, classMarkup } from '../src/timetable.js';
import { renderCalendar } from '../src/calendar.js';
import { renderDayPanel } from '../src/task-views.js';

const fixture = JSON.parse(fs.readFileSync(new URL('../../contracts/timetable/examples.json', import.meta.url)));
const esc = value => String(value).replaceAll('<', '&lt;');
test('shared fixture expands exact inclusive term and alternating weeks', () => {
  const events = classEvents(fixture.value, '2026-09-01', '2026-09-30');
  assert.equal(events.length, 4);
  assert.deepEqual(events.filter(e => e.parity === 'even').map(e => e.date), ['2026-09-15']);
  assert.equal(classEvents(fixture.value, '2026-09-27', '2026-10-30').length, 0);
  assert.equal(classEvents(fixture.value, '2026-09-14', '2026-09-15').length, 2);
});
test('odd weeks are based on first teaching Monday, not calendar week', () => {
  const tables = structuredClone(fixture.value); tables[0].meetings[1].parity = 'odd';
  assert.deepEqual(classEvents(tables, '2026-09-01', '2026-10-01').filter(e => e.parity === 'odd').map(e => e.date), ['2026-09-08', '2026-09-22']);
});
test('calendar classes display actual span and room without task or DDL controls', () => {
  const classes = classEvents(fixture.value, '2026-09-01', '2026-10-01');
  const html = renderCalendar({ classes, tasks: [], anchorDate: '2026-09-07', mode: 'week', today: '2026-09-07' });
  assert.match(html, /08:00–09:50/); assert.match(html, /示例楼101/);
  assert.doesNotMatch(html, /data-task-open|data-calendar-drag-task/);
  const panel = renderDayPanel({ classesMarkup: classMarkup(classes[0], esc), tasks: [], selectedDate: '2026-09-07', today: '2026-09-07' });
  assert.match(panel, /示例原理/);
});
test('HTML course fields are escaped and never used as actionable markup', () => {
  assert.doesNotMatch(classMarkup({ title: '<script>', location: '<img>', startTime: '08:00', endTime: '09:00' }, esc), /<script>|<img>/);
});
test('explicit exam dates survive teaching term end, with no guessed time or classroom', () => {
  const tables = structuredClone(fixture.value);
  const original = tables[0].meetings[0];
  original.exam = { date: '2027-01-04', period: '下午' };
  original.examNote = '考试时间：20270104下午；';
  tables[0].meetings.push({ ...original, weekday: 3 });
  const events = classEvents(tables, '2026-12-28', '2027-01-10');
  assert.equal(events.length, 1);
  assert.equal(events[0].date, '2027-01-04');
  assert.equal(events[0].startTime, ''); assert.equal(events[0].location, '');
  const html = classMarkup(events[0], esc, true);
  assert.match(html, /考试 · 下午/); assert.doesNotMatch(html, /08:00|示例楼101/);
  assert.match(html, /20270104下午/);
});

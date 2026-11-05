"""Synthetic XLS and actual authenticated HTTP/SQLite timetable regression."""
import base64
import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import jsonschema
import xlwt
import app
import timetable
from auth_test_support import registration_payload


def sample_rows():
    rows = [['节数'] + ['星期' + n for n in '一二三四五六日']]
    rows += [[f'第{n}节'] + [''] * 7 for n in timetable.PERIODS]
    rows[1][1] = rows[2][1] = '示例原理(Ⅰ)(示例楼101)(备注：) 每周考试时间：20261228上午；'
    rows[10][2] = rows[11][2] = '示例实验(暂无上课教室数据)(备注：地点另行通知) 双周考试方式：论文'
    return rows


def xls(rows=None):
    book = xlwt.Workbook()
    sheet = book.add_sheet('课表')
    for r, values in enumerate(rows or sample_rows()):
        for c, value in enumerate(values):
            sheet.write(r, c, value)
    stream = io.BytesIO()
    book.save(stream)
    return base64.b64encode(stream.getvalue()).decode()


class TimetableParserTests(unittest.TestCase):
    def test_real_binary_xls_merges_and_preserves_title_parentheses(self):
        meetings = timetable.parse(xls(), app.ApiError)
        self.assertEqual(len(meetings), 2)
        first, second = meetings
        self.assertEqual((first['title'], first['location'], first['startPeriod'], first['endPeriod']),
                         ('示例原理(Ⅰ)', '示例楼101', 1, 2))
        self.assertEqual((second['location'], second['notes'], second['parity']), ('', '地点另行通知', 'even'))
        self.assertEqual(first['exam'], dict(date='2026-12-28', period='上午'))
        self.assertNotIn('due', first)
        self.assertEqual(timetable.parse(xls(), app.ApiError), meetings)

    def test_different_days_nonadjacent_periods_do_not_merge(self):
        rows = sample_rows()
        rows[4][1] = rows[1][1]
        rows[1][3] = rows[1][1]
        self.assertEqual(len(timetable.parse(xls(rows), app.ApiError)), 4)

    def test_unknown_format_empty_file_and_cell_are_explicit_errors(self):
        for encoded in ('garbage!', '', base64.b64encode(b'<html>not XLS</html>').decode(),
                        base64.b64encode(bytes.fromhex('d0cf11e0a1b11ae1') + b'broken').decode()):
            with self.subTest(encoded=encoded[:20]), self.assertRaises(app.ApiError):
                timetable.parse(encoded, app.ApiError)
        rows = sample_rows()
        rows[1][1] = '无周次说明'
        with self.assertRaises(app.ApiError): timetable.parse(xls(rows), app.ApiError)
        rows = [[value if c == 0 or r == 0 else '' for c, value in enumerate(row)] for r, row in enumerate(sample_rows())]
        with self.assertRaises(app.ApiError): timetable.parse(xls(rows), app.ApiError)

    def test_oversize_and_wrong_headers(self):
        with self.assertRaises(app.ApiError): timetable.parse('a' * 800000, app.ApiError)
        rows = sample_rows(); rows[0][1] = 'Monday'
        with self.assertRaises(app.ApiError): timetable.parse(xls(rows), app.ApiError)

    def test_exams_inside_remarks_deduplicate_without_using_lecture_room(self):
        rows = sample_rows()
        rows[1][1] = rows[2][1] = '示例原理(Ⅰ)(示例楼101)(备注：考试时间：20270104下午；) 每周考试方式：闭卷'
        rows[1][3] = rows[2][3] = rows[1][1]
        meetings = timetable.parse(xls(rows), app.ApiError)
        table = dict(id='test', firstMonday='2026-09-07', lastDate='2026-12-25', meetings=meetings)
        exams = [e for e in timetable.occurrences([table, table]) if e['kind'] == 'exam']
        self.assertEqual(len(exams), 1)
        self.assertEqual((exams[0]['date'], exams[0]['period'], exams[0]['location']), ('2027-01-04', '下午', ''))
        self.assertFalse(any(e['date'] > '2026-12-25' for e in timetable.occurrences([table]) if e['kind'] == 'class'))

    def test_invalid_exam_date_and_multiple_courses_are_not_silently_imported(self):
        rows = sample_rows()
        rows[1][1] = rows[1][1].replace('20261228', '20260230')
        with self.assertRaises(app.ApiError): timetable.parse(xls(rows), app.ApiError)
        rows = sample_rows(); rows[1][1] += '\n' + rows[10][2]
        with self.assertRaises(app.ApiError): timetable.parse(xls(rows), app.ApiError)

    def test_current_term_defaults_match_sixteenth_week_and_two_exam_weeks(self):
        first = date.fromisoformat(timetable.TERM['firstMonday'])
        self.assertEqual(first + timedelta(weeks=15), date(2026, 12, 21))
        self.assertEqual(timetable.TERM['lastDate'], '2026-12-25')
        self.assertEqual((date.fromisoformat(timetable.TERM['examEnd']) - date.fromisoformat(timetable.TERM['examStart'])).days, 13)

    def test_shared_example_and_odd_even_term_boundaries(self):
        root = Path(__file__).resolve().parents[1] / 'contracts' / 'timetable'
        fixture = json.loads((root / 'examples.json').read_text(encoding='utf-8'))
        jsonschema.validate(fixture, json.loads((root / 'schema.json').read_text(encoding='utf-8')), format_checker=jsonschema.FormatChecker())
        events = timetable.occurrences(fixture['value'])
        self.assertEqual([e['date'] for e in events if e['parity'] == 'even'], ['2026-09-15'])
        self.assertEqual([e['date'] for e in events if e['parity'] == 'all'], ['2026-09-07', '2026-09-14', '2026-09-21'])
        fixture['value'][0]['meetings'][1]['parity'] = 'odd'
        self.assertEqual([e['date'] for e in timetable.occurrences(fixture['value']) if e['parity'] == 'odd'], ['2026-09-08', '2026-09-22'])


class TimetableHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = app.DB
        cls.folder = tempfile.TemporaryDirectory()
        app.DB = Path(cls.folder.name) / 'timetable.sqlite3'
        app.initialize(recover=False)
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}/api'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        app.DB = cls.previous; cls.folder.cleanup()

    def call(self, path, body=None, method=None, client=None):
        client = client or self.client
        try:
            response = client.open(Request(self.base + path, None if body is None else json.dumps(body).encode(),
                                           {'Content-Type': 'application/json'}, method=method), timeout=15)
        except HTTPError as e: response = e
        with response: return response.status, json.load(response)

    def setUp(self):
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        account = registration_payload(dict(email=hashlib.sha256(self._testMethodName.encode()).hexdigest()[:24] + '@timetable.test', name='课表测试', password='Synthetic-2026!'),
                                       lambda body: self.call('/auth/email-code', body))
        status, registered = self.call('/auth/register', account)
        self.assertEqual(status, 200)
        self.uid = registered['user']['id']
        self.file = xls()
        self.meetings = timetable.parse(self.file, app.ApiError)
        monday = date.today() + timedelta(days=14)
        monday -= timedelta(days=monday.weekday())
        self.monday = monday.isoformat()
        self.body = dict(file=self.file, firstMonday=self.monday, lastDate=(monday + timedelta(days=20)).isoformat(),
                         clocks={m['id']: [m['startTime'], m['endTime']] for m in self.meetings}, version=0)

    def save(self):
        status, result = self.call('/timetable', self.body)
        self.assertEqual(status, 200, result)
        schema = json.loads((Path(__file__).resolve().parents[1] / 'contracts/timetable/schema.json').read_text(encoding='utf-8'))
        jsonschema.validate(result, schema, format_checker=jsonschema.FormatChecker())
        return result

    def task(self):
        task = dict(id='report', title='合成报告', description='', status='not_started', plans=[], subtasks=[],
                    due=dict(date=self.body['lastDate'], precision='date'))
        self.assertEqual(self.call('/data/tasks', dict(value=[task], version=0), 'PUT')[0], 200)
        self.assertEqual(self.call('/data/availability', dict(value=dict(startTime='08:00', endTime='22:00', weekdays=list(range(7))), version=0), 'PUT')[0], 200)
        return task

    def model(self, payload, _Error=None):
        self.model_payload = copy.deepcopy(payload)
        return dict(estimates={'report': 60}, blocks=[dict(taskId='report', title='写报告', startAt=self.monday + 'T10:00:00+08:00',
                                                         endAt=self.monday + 'T11:00:00+08:00')], warnings=[])

    def test_preview_no_save_and_unauthorized(self):
        for path, body in (('/timetable', None), ('/timetable/preview', dict(file=self.file)), ('/timetable', self.body)):
            self.assertEqual(self.call(path, body, client=build_opener())[0], 401)
        self.assertEqual(self.call('/timetable/preview', dict(file=self.file))[1]['meetings'], self.meetings)
        self.assertEqual(self.call('/timetable')[1], dict(value=[], version=0))

    def test_save_duplicate_refresh_account_isolation_delete(self):
        result = self.save()
        self.assertEqual(result['version'], 1)
        replay = self.save()
        self.assertTrue(replay['duplicate']); self.assertEqual(replay['version'], 1)
        self.assertEqual(self.call('/workspace')[1]['timetable'], result)
        self.assertNotIn('file', json.dumps(result))
        with app.connection() as db:
            self.assertEqual(timetable.read(db, 'other'), dict(value=[], version=0))
        self.assertEqual(self.call('/timetable', dict(id=result['value'][0]['id'], version=0), 'DELETE')[0], 409)
        self.assertEqual(self.call('/timetable', dict(id=result['value'][0]['id'], version=1), 'DELETE')[1], dict(value=[], version=2))

    def test_bad_dates_clocks_and_stale_save_are_atomic(self):
        for update in (dict(firstMonday='invalid'), dict(firstMonday=self.body['lastDate']), dict(lastDate='2039-01-01'),
                       dict(clocks={}), dict(clocks={m['id']: ['10:00', '09:00'] for m in self.meetings}), dict(version=1)):
            with self.subTest(update=update):
                self.assertIn(self.call('/timetable', {**self.body, **update})[0], (400, 409))
                self.assertEqual(self.call('/timetable')[1]['value'], [])

    def test_planning_uses_classes_as_fixed_busy_and_apply_succeeds(self):
        self.task(); self.save()
        with patch.object(app.planning, 'plan', side_effect=self.model):
            status, proposal = self.call('/planning/proposals', {})
        self.assertEqual(status, 200, proposal)
        busy = self.model_payload['existingPlans']
        self.assertEqual(len(busy), 5)
        self.assertTrue(all(p['taskId'].startswith('timetable:') for p in busy))
        self.assertTrue(all(p['isLocked'] for p in busy))
        exam = next(p for p in busy if 'exam:' in p['id'])
        self.assertEqual(exam['startAt'], '2026-12-28T00:00:00+08:00')
        self.assertEqual(exam['endAt'], '2026-12-29T00:00:00+08:00')
        self.assertEqual(self.call('/planning/apply', dict(requestId=proposal['requestId']))[0], 200)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value'][0]['plans']), 1)

    def test_changed_timetable_invalidates_old_proposal_without_mutation(self):
        self.task()
        with patch.object(app.planning, 'plan', side_effect=self.model):
            status, proposal = self.call('/planning/proposals', {})
        self.assertEqual(status, 200, proposal)
        self.save()
        self.assertEqual(self.call('/planning/apply', dict(requestId=proposal['requestId']))[0], 409)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['plans'], [])

    def test_model_cannot_overlap_classes_and_import_cannot_overlap_saved_plans(self):
        task = self.task(); saved = self.save()
        def overlap(payload, _Error=None):
            result = self.model(payload)
            result['blocks'][0].update(startAt=self.monday + 'T08:00:00+08:00', endAt=self.monday + 'T09:00:00+08:00')
            return result
        with patch.object(app.planning, 'plan', side_effect=overlap):
            self.assertEqual(self.call('/planning/proposals', {})[0], 502)
        self.call('/timetable', dict(id=saved['value'][0]['id'], version=1), 'DELETE')
        task['plans'] = [dict(id='fixed', title='固定任务', startAt=self.monday + 'T08:00:00+08:00',
                              endAt=self.monday + 'T09:00:00+08:00', origin='manual')]
        self.assertEqual(self.call('/data/tasks', dict(value=[task], version=1), 'PUT')[0], 200)
        self.assertEqual(self.call('/timetable', {**self.body, 'version': 2})[0], 409)
        self.assertEqual(self.call('/timetable')[1], dict(value=[], version=2))

    def test_exam_day_constraint_uses_timezone_and_does_not_invent_display_hours(self):
        self.save()
        with app.connection() as db:
            with self.assertRaises(app.ApiError):
                timetable.assert_no_conflicts([dict(startAt='2026-12-28T05:00:00+00:00', endAt='2026-12-28T06:00:00+00:00')], db, self.uid, app.ApiError)
            timetable.assert_no_conflicts([dict(startAt='2026-12-29T00:00:00+08:00', endAt='2026-12-29T01:00:00+08:00')], db, self.uid, app.ApiError)
            exam = next(e for e in timetable.occurrences(timetable.read(db, self.uid)['value']) if e['kind'] == 'exam')
            self.assertEqual((exam['startTime'], exam['endTime'], exam['period']), ('', '', '上午'))

    def test_historical_or_completed_task_plans_do_not_block_course_import(self):
        task = self.task()
        past = date.today() - timedelta(weeks=4)
        past -= timedelta(days=past.weekday())
        task['plans'] = [dict(id='past', title='历史安排', startAt=past.isoformat() + 'T08:00:00+08:00',
                              endAt=past.isoformat() + 'T09:00:00+08:00', origin='manual')]
        self.assertEqual(self.call('/data/tasks', dict(value=[task], version=1), 'PUT')[0], 200)
        old = {**self.body, 'firstMonday': past.isoformat(), 'lastDate': (past + timedelta(days=20)).isoformat()}
        self.assertEqual(self.call('/timetable', old)[0], 200)
        task['status'] = 'completed'
        task['plans'][0].update(startAt=self.monday + 'T08:00:00+08:00', endAt=self.monday + 'T09:00:00+08:00')
        self.assertEqual(self.call('/data/tasks', dict(value=[task], version=2), 'PUT')[0], 200)
        self.assertEqual(self.call('/timetable', {**self.body, 'version': 1})[0], 200)


if __name__ == '__main__': unittest.main()

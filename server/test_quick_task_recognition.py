import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import quick_task_recognition as quick


# Exercise the public v0.2 result through the same contract checks as live data.
FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'contracts/text-to-app/text-contract-v0.2.json').read_text())
SAMPLE = FIXTURE['expected']['tasks'][0]


class QuickTaskRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(text='请在2026年9月30日前提交需求报告。', createdAt='2026-09-20T01:02:03Z',
                           timezone='America/Los_Angeles', referenceDay='2026-09-19')
        self.baseline = dict(title=self.source['text'], course=None, due=None)

    def run_model(self, *candidates):
        def recognize(request, api_error):
            self.record = request['records'][0]
            rid = self.record['input_id']
            tasks = [copy.deepcopy(candidate) for candidate in candidates]
            for index, task in enumerate(tasks):
                task.update(input_id=rid, candidate_id=f'{rid}:{index}')
                for item in task['sources']:
                    item.update(input_id=rid, platform='manual', label='一句话添加')
                for item in task['evidence']:
                    item.update(input_id=rid)
            outcome = 'success' if tasks else 'no_tasks'
            return dict(contract_version='0.2', request_id=request['request_id'], tasks=tasks, errors=[], status=outcome,
                        record_results=[dict(input_id=rid, status=outcome, task_ids=[t['candidate_id'] for t in tasks], warnings=[])])
        with patch.object(quick.recognition, 'recognize', side_effect=recognize):
            return quick.recognize_quick_task(self.source, self.baseline)

    def test_projects_date_precision_and_captures_original_local_day_and_time(self):
        self.assertEqual(self.run_model(SAMPLE), dict(title='提交需求报告', due=dict(date='2026-09-30', precision='date')))
        self.assertEqual(self.record['published_at'], dict(value='2026-09-19T18:02:03-07:00', precision='datetime', basis='source'))
        self.assertEqual(self.record['timezone'], 'America/Los_Angeles')

    def test_preview_day_is_used_even_when_queued_after_midnight(self):
        self.source['createdAt'] = '2026-09-20T07:00:01Z'
        self.run_model(SAMPLE)
        self.assertEqual(self.record['published_at'], dict(value='2026-09-19', precision='date', basis='source'))

    def test_genuine_clock_with_zone_and_subminute_precision(self):
        task = copy.deepcopy(SAMPLE)
        task['deadline'].update(value='2026-09-30T02:20:00+08:00', precision='datetime')
        self.assertEqual(self.run_model(task)['due'], dict(date='2026-09-29', precision='datetime', time='11:20'))
        task['deadline']['value'] = '2026-09-30T02:20:40+08:00'
        self.assertNotIn('due', self.run_model(task))

    def test_existing_precise_time_survives_imprecise_result(self):
        self.baseline['due'] = dict(date='2026-09-30', precision='datetime', time='15:45')
        self.assertNotIn('due', self.run_model(SAMPLE))

    def test_no_patch_for_multiple_actions_empty_result_or_update(self):
        self.assertEqual(self.run_model(SAMPLE, SAMPLE), {})
        self.assertEqual(self.run_model(), {})
        task = copy.deepcopy(SAMPLE)
        task['notice_type'] = 'postpone'
        task['evidence'][0]['supports'].append('notice_type')
        self.assertEqual(self.run_model(task), {})

    def test_course_must_be_in_a_cited_passage_and_other_review_reasons_block(self):
        task = copy.deepcopy(SAMPLE)
        self.source['text'] = '软工：' + self.source['text']
        task.update(course_or_project='软工', course_basis='inferred', needs_review=True,
                    review_reasons=['课程归属来自正文或上下文推断，请核实。'])
        task['evidence'].append(dict(field='text', quote='软工', supports=['course_or_project']))
        self.assertEqual(self.run_model(task)['course'], '软工')
        task['course_or_project'] = '其他课'
        self.assertEqual(self.run_model(task), dict(title='提交需求报告'))
        task['course_or_project'] = '软工'
        task['review_reasons'].append('两个日期相互矛盾')
        self.assertEqual(self.run_model(task), dict(title='提交需求报告', course='软工'))

    def test_reference_time_does_not_block_a_grounded_title_or_keep_a_false_deadline(self):
        candidate = copy.deepcopy(SAMPLE)
        self.source['text'] = '明天10点在礼堂观看演出，入场请带学生证。'
        self.baseline.update(title=self.source['text'], due=dict(date='2026-09-20', precision='date'))
        candidate.update(title='观看演出', description='入场请带学生证', needs_review=True,
                         review_reasons=['活动时间仅作参考。'])
        candidate['deadline'].update(kind='reference', status='inferred', raw='明天10点')
        candidate['evidence'][0].update(quote=self.source['text'], supports=['title','description','deadline'])
        self.assertEqual(self.run_model(candidate), dict(title='观看演出', description='入场请带学生证', due=None))

    def test_invalid_evidence_and_service_failures_reach_worker(self):
        task = copy.deepcopy(SAMPLE)
        task['evidence'][0]['quote'] = '与输入无关'
        with self.assertRaises(ValueError):
            self.run_model(task)
        with patch.object(quick.recognition, 'recognize', side_effect=RuntimeError('服务不可用')), self.assertRaises(RuntimeError):
            quick.recognize_quick_task(self.source, self.baseline)

"""Exercise the real-mode two-stage protocol with a scripted model boundary.

These prove scope, validation and recovery invariants, not model accuracy.
The opt-in course smoke separately tests independently expected live outputs.
"""
from copy import deepcopy
import json
import unittest
from contracts.course_context import revision

from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.teaching_draft import TeachingDraftAdapter, evidence_catalog
from ddl_text.course_refinement import primary_record
from ddl_text.tests.test_course_context_boundaries import sample


def fixture():
    request, task, _, _ = sample()
    record = request['records'][0]['record']
    record['text'] = '请尽快通过 Lab 0 了解框架并安装、运行和调试，无需提交文件。'
    task.update(title='Lab 0：安装和调试框架', recommendation='task', reason='', time_role='none', submission_relation='other')
    task['relevance'] = dict(state='current', reason='当前明确的准备要求。', evidence=[])
    task['assessment']['action']['evidence'] = [dict(part_id='body', quote=record['text'])]
    return record, task


def indexed(value, catalog):
    if isinstance(value, dict):
        if set(value) == {'part_id', 'quote'}:
            return dict(span_id=next(k for k, v in catalog.items() if v == value))
        return {k: indexed(v, catalog) for k, v in value.items()}
    if isinstance(value, list): return [indexed(v, catalog) for v in value]
    return value


class Client:
    def __init__(self, replies): self.replies = iter(replies); self.calls = []

    def complete_json(self, instructions, payload, settings):
        data = json.loads(payload); self.calls.append((data, settings.timeout_seconds))
        reply = next(self.replies)
        if isinstance(reply, Exception): raise reply
        value = reply(data) if callable(reply) else reply
        return json.dumps(indexed(value, data['evidence_catalog']), ensure_ascii=False)


def keep(data):
    return dict(updates=[dict(slot=d['slot'], deadline=d['assessment']['deadline'],
        calendar_rule=d.get('calendar_rule'), time_role=d.get('time_role', 'none'),
        relevance=d['relevance'],
        recommendation=d['recommendation'], reason=d['reason'], evidence=d['assessment']['action']['evidence'])
        for d in data['current_actions']])


class CourseRefinementTests(unittest.TestCase):
    def run_adapter(self, record, replies):
        client = Client(replies)
        result = TeachingDraftAdapter(client, True).extract(record, Settings(mode='real', timeout_seconds=30), 0)[0]
        return result, client

    def test_non_action_and_correction_never_see_adjacent_assignments(self):
        for text in ('几何重建：本节介绍曲面重建。', 'Part III 渲染。光照、辐射度量、可见性和材质。'*40):
            record, _ = fixture(); record['text'] = text
            quote = next(v for v in evidence_catalog(primary_record(record)).values() if v['part_id'] == 'body')
            empty = dict(tasks=[], empty_decision=dict(status='no_tasks', reason='仅介绍知识内容。', evidence=[quote]))
            result, client = self.run_adapter(record, [dict(tasks=[]), empty])
            self.assertEqual(result['empty_decision']['status'], 'no_tasks')
            self.assertEqual(len(client.calls), 2)
            for data, _ in client.calls:
                self.assertNotIn('course_context', data['record_metadata'])
                self.assertTrue(data['supporting_context_pending'])
                self.assertNotIn('context:other', [q['part_id'] for q in data['evidence_catalog'].values()])
            self.assertIn('previous_response', client.calls[1][0])

    def test_preparation_stays_current_and_actions_are_frozen(self):
        record, task = fixture()
        result, client = self.run_adapter(record, [dict(tasks=[task]), keep])
        decision = result['decisions'][0]
        self.assertEqual(decision['title'], task['title'])
        self.assertEqual(decision['recommendation'], 'task')
        self.assertEqual(decision['relevance']['state'], 'current')
        self.assertEqual(decision['pair']['assessment']['deadline']['value'], None)
        self.assertEqual(len(client.calls), 2)
        self.assertLess(client.calls[1][1], client.calls[0][1])

    def test_context_can_add_an_evidenced_deadline_without_creating_an_action(self):
        record, task = fixture()
        ref = record['references'][0]; ref['text'] = 'Lab 0 安装调试需于2026年12月20日完成。'
        document = record['course_context']['documents'][0]
        document['length'] = len(ref['text'])
        record['course_context']['revision'] = revision([dict(document, text=ref['text'])])
        def refine(data):
            result = keep(data); update = result['updates'][0]
            update.update(time_role='deadline', evidence=[dict(part_id=ref['ref_id'], quote=ref['text'])])
            update['deadline'].update(value='2026-12-20', precision='date', derivation='absolute',
                                      evidence=update['evidence'])
            return result
        result, _ = self.run_adapter(record, [dict(tasks=[task]), refine])
        self.assertEqual(len(result['decisions']), 1)
        decision = result['decisions'][0]
        self.assertEqual(decision['pair']['assessment']['deadline']['value'], '2026-12-20')
        self.assertEqual(decision['pair']['assessment']['action']['evidence'], task['assessment']['action']['evidence'])

    def test_context_can_resolve_prior_scope_uncertainty_but_cannot_mark_history(self):
        record, task = fixture()
        task.update(recommendation='candidate', reason='大纲适用性尚待确定。')
        task['relevance']['state'] = 'uncertain'
        def resolve(data):
            result = keep(data); update = result['updates'][0]
            update.update(recommendation='task', reason='')
            update['relevance'].update(state='current', reason='本条明确的当前准备要求。',
                evidence=update['evidence'])
            return result
        result, _ = self.run_adapter(record, [dict(tasks=[task]), resolve])
        self.assertEqual(result['decisions'][0]['recommendation'], 'task')
        self.assertEqual(result['decisions'][0]['review_reasons'], [])
        def no_proof(data):
            result = resolve(data); result['updates'][0]['relevance']['evidence'] = []
            return result
        result, _ = self.run_adapter(record, [dict(tasks=[task]), no_proof, no_proof])
        self.assertEqual(result['decisions'][0]['recommendation'], 'candidate')
        def history(data):
            result = keep(data); result['updates'][0]['relevance']['state'] = 'historical'
            return result
        result, _ = self.run_adapter(record, [dict(tasks=[task]), history, history])
        self.assertEqual(result['decisions'][0]['recommendation'], 'candidate')
        self.assertEqual(result['decisions'][0]['relevance']['state'], 'uncertain')

    def test_context_cannot_add_tasks_filter_or_reclassify_primary(self):
        for fault in ('tasks', 'historical', 'duplicate_slot', 'new_slot'):
            with self.subTest(fault=fault):
                record, task = fixture()
                def invalid(data):
                    if fault == 'tasks': return dict(tasks=[task])
                    result = keep(data)
                    if fault == 'historical': result['updates'][0]['recommendation'] = 'filtered'
                    if fault == 'duplicate_slot': result['updates'] *= 2
                    if fault == 'new_slot': result['updates'][0]['slot'] = 'adjacent:lab2'
                    return result
                result, client = self.run_adapter(record, [dict(tasks=[task]), invalid, invalid])
                self.assertEqual(len(client.calls), 3)
                self.assertEqual(len(result['decisions']), 1)
                decision = result['decisions'][0]
                self.assertEqual(decision['title'], task['title'])
                self.assertEqual(decision['relevance']['state'], 'current')
                self.assertEqual(decision['recommendation'], 'candidate')
                self.assertIn('课程参考资料未能完成核对', decision['review_reasons'][0])

    def test_optional_context_timeout_preserves_primary_and_primary_failure_still_fails(self):
        record, task = fixture()
        error = AgentError('agent_timeout', True, 'model_timeout')
        result, client = self.run_adapter(record, [dict(tasks=[task]), error])
        self.assertEqual(result['decisions'][0]['recommendation'], 'candidate')
        self.assertEqual(len(client.calls), 2)
        with self.assertRaises(AgentError): self.run_adapter(record, [error])

    def test_unknown_primary_action_cannot_be_promoted_by_context(self):
        record, task = fixture()
        task['assessment']['action']['state'] = 'uncertain'
        task.update(recommendation='candidate', reason='当前来源的行动要求不明确。')
        def promote(data):
            result = keep(data); result['updates'][0].update(recommendation='task', reason='')
            return result
        result, _ = self.run_adapter(record, [dict(tasks=[task]), promote])
        self.assertEqual(result['decisions'][0]['recommendation'], 'candidate')


if __name__ == '__main__': unittest.main()

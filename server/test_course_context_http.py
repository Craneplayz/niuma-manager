"""Authenticated HTTP and SQLite wiring with a synthetic model boundary.

Exercises real retrieval, reader orchestration, compiler, result validation,
evidence API, auto-save, source revisions and manual decision protection.
Does not measure live-model semantic accuracy.
"""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import app
import teaching
import test_teaching as harness
from server.test_course_context import request,draft
from ddl_text.teaching_draft import compile_teaching_response,TeachingPreview
from ddl_text import teaching_pipeline


class CourseContextHTTPTests(unittest.TestCase):
    setUp=harness.TeachingIntegrationTests.setUp
    tearDown=harness.TeachingIntegrationTests.tearDown
    call=harness.TeachingIntegrationTests.call

    def sync(self,requests):
        with app.connection() as db:
            return teaching.register(db,self.uid,'synthetic-course-user',requests)

    def run_record(self,rid,expected=200):
        def classify(record,**kwargs):
            task=draft(record);task.update(time_role='deadline',submission_relation='assignment_submission')
            refs=[r for r in record['references'] if r['kind']=='course_context' and '截止' in r['text']]
            if refs:
                ref=refs[0]
                day='2026-12-21' if '21日' in ref['text'] else '2026-12-20'
                task['assessment']['deadline'].update(value=day,precision='date',derivation='absolute',anchor=None,
                    evidence=[dict(part_id=ref['ref_id'],quote=ref['text'])])
            result=compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)
            return TeachingPreview(tuple(result['decisions']),False,())
        options=dict(provider='qiniu',key='synthetic-key',model='synthetic',endpoint='https://example.test')
        with patch.object(teaching,'live_options',return_value=options),patch.object(teaching_pipeline,'process_teaching_draft',side_effect=classify):
            status,batches=self.call('/recognition/batches',dict(contract_version='teaching-run/0.1',request_id=rid))
        self.assertEqual(status,expected,batches)
        if expected!=200:return batches
        return next(b for b in batches if b['request']['request_id']==rid)

    def test_context_on_first_import_dates_evidence_api_and_no_duplicate_resync(self):
        primary=request('homework','请完成第四章书面作业，截止规则见教学大纲。')
        material=request('syllabus','教学大纲：第四章书面作业于2026年12月20日截止。')
        views=self.sync([primary,material])['requests']
        rid=next(v['request_id'] for v in views if v['records'][0]['input_id']=='homework')
        batch=self.run_record(rid);item=batch['items'][0]
        self.assertEqual(item['candidate']['deadline']['value'],'2026-12-20')
        self.assertEqual(item['candidate']['deadline']['status'],'inferred')
        self.assertIn('syllabus',[q['input_id'] for q in item['candidate']['evidence']])
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        status,context=self.call('/recognition/course-context',dict(request_id=rid))
        self.assertEqual(status,200)
        self.assertTrue(context['records'][0]['passages'])
        self.assertEqual(context['records'][0]['passages'][0]['input_id'],'syllabus')
        self.assertNotIn(rid,[v['request_id'] for v in self.sync([material,primary])['requests']])
        saved=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['due'],dict(date='2026-12-20',precision='date'))
        # A correction becomes a reviewable update; it cannot silently duplicate
        # or rewrite a task that the user has already saved.
        changed=deepcopy(material);changed['records'][0]['record']['text']=changed['records'][0]['record']['text'].replace('20日','21日')
        next_views=self.sync([changed])['requests']
        next_rid=next(v['request_id'] for v in next_views if v['records'][0]['input_id']=='homework')
        revised=self.run_record(next_rid)['items'][0]
        self.assertTrue(revised['candidate']['needs_review'])
        self.assertEqual(revised['candidate']['deadline']['value'],'2026-12-21')
        self.assertEqual(self.call('/recognition/candidates/'+revised['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],400)
        saved=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['due'],dict(date='2026-12-20',precision='date'))
        # A different authenticated user cannot query the first user's context.
        self.assertEqual(self.call('/auth/register',dict(name='Other',email='other-context@example.test',password='Synthetic-password!'))[0],200)
        self.assertEqual(self.call('/recognition/course-context',dict(request_id=rid))[0],404)

    def test_context_change_during_model_call_cannot_save_a_stale_result(self):
        primary=request('homework','请完成第四章作业，规则见教学大纲。')
        material=request('syllabus','教学大纲：第四章作业于12月20日截止。')
        rid=self.sync([primary,material])['requests'][0]['request_id']
        original=teaching.validate_current_result
        def change_before_save(*args):
            changed=deepcopy(material);changed['records'][0]['record']['text']+='更正为12月21日。'
            self.sync([changed]);return original(*args)
        with patch.object(teaching,'validate_current_result',side_effect=change_before_save):
            self.run_record(rid,expected=409)
        with app.connection() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?',(self.uid,rid)).fetchone())


if __name__=='__main__':unittest.main()

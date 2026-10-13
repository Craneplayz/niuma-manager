"""Saved native teaching snapshots -> explicit reprocessing -> actual task list."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import app
import recognition
import reprocessing
import teaching
import test_teaching


class TeachingReprocessingTests(unittest.TestCase):
    setUp=test_teaching.TeachingIntegrationTests.setUp
    call=test_teaching.TeachingIntegrationTests.call

    def tearDown(self):
        self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        test_teaching.TeachingIntegrationTests.tearDown(self)

    def tasks(self):
        return self.call('/workspace')[1].get('tasks',{}).get('value',[])

    def prepare(self, name='partial-clear-deadline'):
        cases=json.loads((Path(__file__).parents[1]/'contracts/examples/teaching-model-routing-v04-cases.json').read_text(encoding='utf8'))['cases']
        self.case=next(c for c in cases if c['name']==name)
        self.capture=deepcopy(self.case['request'])
        with app.connection() as db:
            sync=teaching.register(db,self.uid,'synthetic-course-user',[self.capture])
            self.request=teaching.resolve_snapshot(db,self.uid,sync['requests'][0]['request_id'],app.ApiError)
        self.input_id=self.request['records'][0]['record']['input_id']
        accounts=self.call('/recognition/reprocessing/sources')[1]['accounts']
        self.start=dict(contract_version='reprocessing/0.1',operation_id='test-operation',
            source_account_id=next(a for a in accounts if a['label']=='当前教学网账号')['source_account_id'],input_ids=[self.input_id])

    def model(self, request, *_):
        result=deepcopy(self.case['expected']);result['request_id']=request['request_id']
        entry=request['records'][0];row=result['record_results'][0]
        row.update(input_id=entry['record']['input_id'],snapshot_id=entry['record']['snapshot_id'])
        for decision in row['decisions']: decision['pair']['record']=deepcopy(entry['record'])
        return result,dict(version=result['policy_version'])

    def store_old(self, state='pending'):
        result,_=self.model(self.request)
        decision=result['record_results'][0]['decisions'][0]
        decision.update(recommendation='candidate',review_codes=['model_review'],review_reasons=['旧规则要求核对正文'])
        card=teaching.result_view(self.request,result)['tasks'][0]
        with app.connection() as db:
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',(self.uid,self.request['request_id'],'old',json.dumps(self.request),json.dumps(result)))
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',('old-card',self.uid,self.request['request_id'],json.dumps(card),state))

    def launch(self):
        status,run=self.call('/recognition/reprocessing/runs',self.start)
        self.assertEqual(status,202,run)
        return run

    def saved(self, run):
        self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        return self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]

    def test_old_pending_becomes_one_task_without_browser_confirmation(self):
        self.prepare();self.store_old()
        with patch.object(teaching,'recognize',side_effect=self.model) as model:
            run=self.launch();saved=self.saved(run)
            self.assertEqual(saved['state'],'succeeded',saved)
            self.assertEqual(saved['records'][0]['decision_summary'],dict(preserved=0,replaced=1,new_pending=0,unresolved=0))
            self.assertEqual(self.call('/recognition/reprocessing/runs',self.start)[0],200)
            self.assertEqual(model.call_count,1)
        tasks=self.tasks()
        self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0]['due'],dict(date='2026-11-17',precision='datetime',time='23:30'))
        self.assertTrue(tasks[0]['course'])
        batches=self.call('/recognition/batches')[1]
        self.assertEqual([i['state'] for b in batches for i in b['items']],['applied'])
        with app.connection() as db:
            self.assertEqual(teaching.register(db,self.uid,'synthetic-course-user',[self.capture])['summary']['skipped'],1)
        self.assertTrue(self.call('/recognition/task-evidence',dict(task_id=tasks[0]['id']))[1]['evidence'])
        record=self.call('/recognition/reprocessing/sources')[1]['accounts'][0]['records'][0]
        self.assertEqual(record['result_status'],'success');self.assertTrue(record['policy_version'].startswith('teaching-policy-v1:'))

    def test_ignored_decision_is_not_recreated_or_hidden_on_read(self):
        self.prepare();self.store_old('ignored')
        with patch.object(teaching,'recognize',side_effect=self.model): saved=self.saved(self.launch())
        self.assertEqual(saved['state'],'succeeded',saved)
        self.assertEqual(saved['records'][0]['decision_summary']['preserved'],1)
        self.assertEqual(saved['records'][0]['decision_summary']['unresolved'],1)
        items=[i for b in self.call('/recognition/batches')[1] for i in b['items']]
        item=next(i for i in items if i['state']=='pending')
        self.assertTrue(item['candidate']['needs_review'])
        self.assertEqual(item['candidate']['notice_type'],'uncertain')
        self.assertTrue(any('旧任务' in reason for reason in item['candidate']['review_reasons']))
        self.assertIn(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],(400,409))
        self.assertFalse(self.tasks())

    def test_no_action_filter_and_candidate_are_not_auto_created(self):
        for index,name in enumerate(('non-action-partial','expired-partial','required-date-unread')):
            self.prepare(name);self.start['operation_id']=f'case:{index}'
            with patch.object(teaching,'recognize',side_effect=self.model): saved=self.saved(self.launch())
            self.assertEqual(saved['state'],'succeeded',saved)
            self.assertEqual(saved['records'][0]['outcome'],'tasks' if index==2 else 'no_tasks')
        self.assertFalse(self.tasks())
        items=[i for b in self.call('/recognition/batches')[1] for i in b['items']]
        self.assertEqual(len(items),1);self.assertTrue(items[0]['candidate']['needs_review'])

    def test_model_failure_preserves_pending_and_original_request(self):
        self.prepare();self.store_old()
        with patch.object(teaching,'recognize',side_effect=ValueError('failed')): saved=self.saved(self.launch())
        self.assertEqual(saved['state'],'failed');self.assertTrue(saved['records'][0]['old_result_retained'])
        with app.connection() as db:
            self.assertEqual(teaching.resolve_snapshot(db,self.uid,self.request['request_id'],app.ApiError),self.request)
            self.assertEqual(db.execute('SELECT state FROM recognition_candidates WHERE id=?',('old-card',)).fetchone()[0],'pending')

    def test_invalid_evidence_or_legacy_output_cannot_be_saved(self):
        self.prepare();self.store_old()
        def bad(request,*_):
            result,policy=self.model(request);result.pop('classification_mode');return result,policy
        with patch.object(teaching,'recognize',side_effect=bad): self.assertEqual(self.saved(self.launch())['state'],'failed')
        self.assertFalse(self.tasks())

    def test_stop_source_edit_and_account_switch_reject_late_results(self):
        self.prepare();self.store_old()
        for mutation in ('stop','source','account'):
            with self.subTest(mutation=mutation):
                started=threading.Event();release=threading.Event()
                def slow(request,*_):
                    started.set();release.wait(5);return self.model(request)
                self.start['operation_id']=mutation
                with patch.object(teaching,'recognize',side_effect=slow):
                    run=self.launch();self.assertTrue(started.wait(5))
                    if mutation=='stop': self.call('/recognition/reprocessing/runs/'+run['run_id']+'/stop',dict(contract_version='reprocessing/0.1'))
                    elif mutation=='source':
                        with app.connection() as db: db.execute("UPDATE teaching_sources SET digest='changed' WHERE user_id=?",(self.uid,))
                    else: self.call('/sources/credentials',dict(username='different-account',password='synthetic-pass'))
                    release.set();saved=self.saved(run)
                self.assertEqual(saved['records'][0]['state'],'interrupted' if mutation=='stop' else 'superseded')
                self.assertFalse(self.tasks())

    def test_removed_campus_credentials_hide_native_directory_and_reject_saved_selection(self):
        self.prepare()
        self.call('/sources/credentials',dict(username='different-account',password='synthetic-pass'))
        self.assertFalse(self.call('/recognition/reprocessing/sources')[1]['accounts'])
        self.assertEqual(self.call('/recognition/reprocessing/runs',self.start)[0],404)

    def test_queued_source_change_does_not_call_model(self):
        self.prepare()
        with patch.object(reprocessing,'launch'): run=self.launch()
        with app.connection() as db: db.execute("UPDATE teaching_sources SET digest='changed' WHERE user_id=?",(self.uid,))
        with patch.object(teaching,'recognize') as model:
            reprocessing.process(app.DB,self.uid,run['run_id'],app.ApiError);model.assert_not_called()
        self.assertEqual(self.saved(run)['records'][0]['state'],'superseded')

    def test_same_input_id_in_legacy_source_does_not_protect_native_task(self):
        self.prepare()
        card=teaching.result_view(self.request,self.model(self.request)[0])['tasks'][0]
        with app.connection() as db:
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',('foreign',self.uid,'other-account-request',json.dumps(card),'ignored'))
        with patch.object(teaching,'recognize',side_effect=self.model): saved=self.saved(self.launch())
        self.assertEqual(saved['records'][0]['decision_summary']['preserved'],0)
        self.assertEqual(len(self.tasks()),1)

    def test_newer_run_wins_and_does_not_create_duplicate_tasks(self):
        self.prepare();started=threading.Event();release=threading.Event()
        def slow_first(request,*_):
            if not started.is_set(): started.set();release.wait(5)
            return self.model(request)
        with patch.object(teaching,'recognize',side_effect=slow_first):
            first=self.launch();self.assertTrue(started.wait(5))
            self.start['operation_id']='second';second=self.launch()
            release.set();self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        self.assertEqual(self.saved(first)['records'][0]['state'],'superseded')
        self.assertEqual(self.saved(second)['state'],'succeeded')
        self.assertEqual(len(self.tasks()),1)

    def test_submitted_scope_cannot_be_bypassed_by_reprocessing(self):
        self.prepare()
        with app.connection() as db:
            teaching.submission.observe(db,self.uid,teaching.account('synthetic-course-user'),
                self.request['records'][0]['record'],self.request['request_id'],dict(state='submitted'))
        with patch.object(teaching,'recognize',side_effect=self.model): saved=self.saved(self.launch())
        self.assertEqual(saved['state'],'succeeded',saved)
        self.assertFalse(self.tasks())
        item=self.call('/recognition/batches')[1][0]['items'][0]
        self.assertTrue(item['candidate']['needs_review'])
        self.assertIn(teaching.submission.SCOPE_REVIEW,item['candidate']['review_reasons'])

    def test_queue_cancellation_does_not_start_a_late_model_call(self):
        from server.model_queue import ModelQueue
        self.prepare();pool=ModelQueue(limit=1,wait=10);self.assertTrue(pool.acquire())
        with patch.object(recognition,'_ACTIVE',pool),patch.object(teaching,'recognize') as model:
            run=self.launch()
            self.call('/recognition/reprocessing/runs/'+run['run_id']+'/stop',dict(contract_version='reprocessing/0.1'))
            saved=self.saved(run);model.assert_not_called()
            self.assertEqual(saved['state'],'stopped');pool.release()

    def test_failure_after_candidate_insert_rolls_back_every_write(self):
        self.prepare();self.store_old()
        with patch.object(teaching,'recognize',side_effect=self.model),patch.object(recognition,'resolve_candidate',side_effect=RuntimeError('disk write failed')):
            saved=self.saved(self.launch())
        self.assertEqual(saved['state'],'interrupted')
        self.assertFalse(self.tasks())
        with app.connection() as db:
            self.assertEqual(teaching.resolve_snapshot(db,self.uid,self.request['request_id'],app.ApiError),self.request)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_batches WHERE user_id=?',(self.uid,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT state FROM recognition_candidates WHERE id=?',('old-card',)).fetchone()[0],'pending')

    def test_superseded_failure_leaves_active_inbox_but_retains_audit_result(self):
        self.prepare();result,_=self.model(self.request)
        result['status']='failed'
        result['record_results'][0].update(status='failed',decisions=[],error=dict(code='agent_timeout',message='Agent 调用超时。',retryable=True))
        with app.connection() as db:
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',(self.uid,self.request['request_id'],'failed',json.dumps(self.request),json.dumps(result)))
        self.assertTrue(self.call('/recognition/batches')[1][0]['result']['errors'])
        with patch.object(teaching,'recognize',side_effect=self.model): self.assertEqual(self.saved(self.launch())['state'],'succeeded')
        batches=self.call('/recognition/batches')[1]
        self.assertEqual(len(batches),1);self.assertFalse(batches[0]['result']['errors'])
        with app.connection() as db:
            old=db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',(self.uid,self.request['request_id'])).fetchone()
            self.assertEqual(json.loads(old[0]),result)


if __name__=='__main__': unittest.main()

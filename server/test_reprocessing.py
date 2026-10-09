import copy
import hashlib
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import recognition
import reprocessing
import test_api as api_tests


class ReprocessingTests(unittest.TestCase):
    client=api_tests.ApiTests.client
    call=api_tests.ApiTests.call
    register=api_tests.ApiTests.register

    @classmethod
    def setUpClass(cls):
        api_tests.ApiTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        reprocessing.wait_for_jobs(app.DB)
        api_tests.ApiTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.browser=self.client();self.register(self.browser,self._testMethodName+'@test.local')
        self.uid=self.call(self.browser,'/auth/me')[1]['user']['id']
        fixture=json.loads((Path(__file__).resolve().parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))
        self.record=fixture['request']['records'][0]
        self.request=dict(fixture['request'],request_id='saved:'+self.uid,records=[self.record])
        self.task=fixture['expected']['tasks'][0]
        self.policy=dict(version='text-policy-v1:'+'a'*64)
        self.account=hashlib.sha256(b'synthetic-account').hexdigest()
        with app.connection() as db:
            db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)',(self.uid,self.account,self.record['input_id'],'synthetic-digest',json.dumps(self.request)))
        self.directory=self.call(self.browser,'/recognition/reprocessing/sources')[1]
        self.start=dict(contract_version='reprocessing/0.1',operation_id='op:'+self.uid,source_account_id=self.directory['accounts'][0]['source_account_id'],input_ids=[self.record['input_id']])

    def tearDown(self):
        self.assertTrue(reprocessing.wait_for_jobs(app.DB))

    def result(self,request,*_,tasks=False):
        recognition._EXECUTION.policy=self.policy
        return dict(contract_version='0.2',request_id=request['request_id'],status='success' if tasks else 'no_tasks',record_results=[dict(input_id=self.record['input_id'],status='success' if tasks else 'no_tasks',task_ids=[self.task['candidate_id']] if tasks else [],warnings=[])],tasks=[copy.deepcopy(self.task)] if tasks else [],errors=[])

    def launch(self):
        status,run=self.call(self.browser,'/recognition/reprocessing/runs','POST',self.start)
        self.assertEqual(status,202)
        return run

    def saved(self,run):
        return self.call(self.browser,'/recognition/reprocessing/runs/'+run['run_id'])[1]

    def test_success_versions_persistence_idempotence_and_owner(self):
        with patch.object(recognition,'recognize',side_effect=self.result) as model:
            run=self.launch();self.assertTrue(reprocessing.wait_for_jobs(app.DB))
            saved=self.saved(run)
            self.assertEqual(saved['state'],'succeeded');self.assertEqual(saved['records'][0]['policy_version'],self.policy['version'])
            self.assertEqual(self.call(self.browser,'/recognition/reprocessing/runs','POST',self.start)[0],200)
            self.assertEqual(model.call_count,1)
        self.assertEqual(self.call(self.browser,'/recognition/reprocessing/sources')[1]['accounts'][0]['records'][0]['result_status'],'no_tasks')
        other=self.client();self.register(other,'foreign-'+self.uid+'@test.local')
        self.assertEqual(self.call(other,'/recognition/reprocessing/runs/'+run['run_id'])[0],404)
        self.assertEqual(self.call(other,'/recognition/reprocessing/runs','POST',self.start)[0],404)
        changed=dict(self.start,input_ids=['another'])
        self.assertEqual(self.call(self.browser,'/recognition/reprocessing/runs','POST',changed)[0],409)

    def test_stop_blocks_late_result_and_is_idempotent(self):
        started=threading.Event();release=threading.Event()
        def model(request,*_):
            started.set();release.wait(5);return self.result(request,tasks=True)
        with patch.object(recognition,'recognize',side_effect=model):
            run=self.launch();self.assertTrue(started.wait(5))
            path='/recognition/reprocessing/runs/'+run['run_id']+'/stop'
            self.assertEqual(self.call(self.browser,path,'POST',dict(contract_version='reprocessing/0.1'))[1]['state'],'stopping')
            release.set();self.assertTrue(reprocessing.wait_for_jobs(app.DB))
            self.assertEqual(self.saved(run)['state'],'stopped')
            self.assertEqual(self.call(self.browser,path,'POST',dict(contract_version='reprocessing/0.1'))[1]['state'],'stopped')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_candidates WHERE user_id=?',(self.uid,)).fetchone()[0],0)

    def test_newer_run_does_not_accept_older_response(self):
        started=threading.Event();release=threading.Event()
        def model(request,*_):
            if not started.is_set():started.set();release.wait(5)
            return self.result(request)
        with patch.object(recognition,'recognize',side_effect=model):
            first=self.launch();self.assertTrue(started.wait(5))
            self.start['operation_id']+=':new';second=self.launch()
            release.set();self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        self.assertEqual(self.saved(first)['records'][0]['state'],'superseded')
        self.assertEqual(self.saved(second)['state'],'succeeded')

    def test_protected_decisions_hold_ambiguous_new_tasks(self):
        with app.connection() as db:
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',('protected:'+self.uid,self.uid,self.request['request_id'],json.dumps(self.task),'ignored'))
        with patch.object(recognition,'recognize',side_effect=lambda request,*_:self.result(request,tasks=True)):
            run=self.launch();reprocessing.wait_for_jobs(app.DB)
        self.assertEqual(self.saved(run)['records'][0]['decision_summary']['unresolved'],1)
        with app.connection() as db:
            candidates=db.execute('SELECT state,candidate FROM recognition_candidates WHERE user_id=?',(self.uid,)).fetchall()
            self.assertEqual([r['state'] for r in candidates],['ignored','pending'])
            difference=json.loads(candidates[1]['candidate'])
            self.assertEqual(difference['notice_type'],'uncertain');self.assertTrue(difference['needs_review'])
            self.assertFalse(recognition.can_auto_apply(difference,self.request))
            self.assertEqual(db.execute('SELECT request FROM source_versions WHERE user_id=?',(self.uid,)).fetchone()[0],json.dumps(self.request))

    def test_failure_preserves_previous_success(self):
        with app.connection() as db:
            result=self.result(self.request)
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',(self.uid,self.request['request_id'],'original',json.dumps(self.request),json.dumps(result)))
        with patch.object(recognition,'recognize',side_effect=ValueError('synthetic failure')):
            run=self.launch();reprocessing.wait_for_jobs(app.DB)
        self.assertEqual(self.saved(run)['state'],'failed');self.assertTrue(self.saved(run)['records'][0]['old_result_retained'])
        with app.connection() as db:
            self.assertEqual(json.loads(db.execute('SELECT result FROM recognition_batches WHERE user_id=?',(self.uid,)).fetchone()[0]),result)

    def test_restart_cancels_unstarted_without_model_replay(self):
        with patch.object(reprocessing,'launch'):
            run=self.launch()
        with app.connection() as db:
            reprocessing.initialize(db,recover=True)
        saved=self.saved(run);self.assertEqual(saved['state'],'interrupted');self.assertFalse(saved['records'][0]['attempted'])

    def test_stop_queued_records_never_calls_model(self):
        with patch.object(reprocessing,'launch'):
            run=self.launch()
        stopped=self.call(self.browser,'/recognition/reprocessing/runs/'+run['run_id']+'/stop','POST',dict(contract_version='reprocessing/0.1'))[1]
        self.assertEqual(stopped['state'],'stopped');self.assertFalse(stopped['records'][0]['attempted'])
        with patch.object(recognition,'recognize') as model:
            reprocessing.process(app.DB,self.uid,run['run_id'],app.ApiError);model.assert_not_called()

    def test_source_change_during_model_does_not_publish_old_result(self):
        started=threading.Event();release=threading.Event()
        def model(request,*_):started.set();release.wait(5);return self.result(request,tasks=True)
        with patch.object(recognition,'recognize',side_effect=model):
            run=self.launch();self.assertTrue(started.wait(5))
            with app.connection() as db:
                db.execute('UPDATE source_versions SET digest=? WHERE user_id=?',('new-source-version',self.uid))
            release.set();reprocessing.wait_for_jobs(app.DB)
        self.assertEqual(self.saved(run)['records'][0]['state'],'superseded')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recognition_candidates WHERE user_id=?',(self.uid,)).fetchone()[0],0)

    def test_directory_paginates_and_rejects_empty_or_forged_selection(self):
        with app.connection() as db:
            for index in range(101):
                record=dict(self.record,input_id='old/中文 '+str(index));request=dict(self.request,records=[record])
                db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)',(self.uid,self.account,record['input_id'],'digest',json.dumps(request)))
        page=self.call(self.browser,'/recognition/reprocessing/sources')[1]
        self.assertEqual(len(page['accounts'][0]['records']),100)
        next_page=self.call(self.browser,'/recognition/reprocessing/sources?cursor='+page['next_cursor'])[1]
        self.assertEqual(len(next_page['accounts'][0]['records']),2)
        for change in (dict(self.start,input_ids=[]),dict(self.start,text='forged'),dict(self.start,input_ids=['foreign'])):
            self.assertIn(self.call(self.browser,'/recognition/reprocessing/runs','POST',change)[0],(400,404))


if __name__=='__main__':unittest.main()

"""HTTP integration: captured v0.4 snapshot, recognition, cards, auto-apply and retries."""
from copy import deepcopy
import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from unittest.mock import patch

import app
import teaching
from jsonschema import Draft202012Validator
from contracts.teaching_io import snapshot_digest
from ddl_text.config import Settings
from ddl_text.teaching_pipeline import process_teaching_request
from ddl_text.tests.test_teaching_pipeline import example, RecordClient


class TeachingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old_db=app.DB
        # Crawler execution is mocked below; its availability check must not
        # depend on a developer's node_modules (the server CI does not install it).
        crawler=Path(self.tmp.name)/'crawler'
        (crawler/'node_modules'/'tsx').mkdir(parents=True)
        self.crawler_patch=patch.object(app.source_sync,'CRAWLER',crawler)
        self.crawler_patch.start();self.addCleanup(self.crawler_patch.stop)
        app.DB=Path(self.tmp.name)/'test.sqlite3'; app.initialize()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}/api'
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.call('/auth/register',dict(name='Synthetic',email='teaching@example.test',password='Synthetic-password!'))[0],200)
        self.assertEqual(self.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))[0],200)
        with app.connection() as db:self.uid=db.execute('SELECT id FROM users').fetchone()['id']
        self.drafts={}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close();self.thread.join();app.DB=self.old_db;self.tmp.cleanup()

    def call(self,path,body=None):
        req=Request(self.base+path, None if body is None else json.dumps(body).encode(), {'Content-Type':'application/json'})
        try:
            with self.client.open(req,timeout=120) as response:return response.status,json.load(response)
        except HTTPError as response:return response.code,json.load(response)

    def capture(self,names):
        request,drafts=example(names);self.drafts.update(drafts)
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(request))):
            status,result=self.call('/sources/sync',dict(sources=['teaching_web']))
        self.assertEqual(status,200,result)
        return request,result

    def model(self,request,ApiError):
        output=process_teaching_request(request,client=RecordClient(self.drafts),settings=Settings(timeout_seconds=5),
            verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
        output['policy_version']='teaching-policy-v1:'+'0'*64
        return output,dict(version=output['policy_version'])

    def native_capture(self,state,when='2026-10-10T10:00:00+08:00'):
        request,drafts=example(); self.drafts.update(drafts)
        record=request['records'][0]['record'];record['collected_at']=when
        basis={'submitted':'review_submission_history_title','active':'active_assignment_page','unknown':'unknown'}[state]
        return dict(contract_version='teaching-capture/0.1',request=request,assignment=dict(
            state=state,basis=basis,course_id=record['course_id'],content_id=record['source_item_id']))

    def sync_native(self,capture):
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(capture))):
            status,result=self.call('/sources/sync',dict(sources=['teaching_web']))
        self.assertEqual(status,200,result)
        return result

    def test_submitted_first_capture_skips_without_model_and_retains_snapshot(self):
        capture=self.native_capture('submitted')
        with patch.object(teaching,'recognize') as model:
            result=self.sync_native(capture)
            self.assertEqual(result,dict(requests=[],summary=dict(total=1,skipped=1,pending=0)))
            with app.connection() as db:
                rid=db.execute('SELECT request_id FROM teaching_sources').fetchone()['request_id']
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=rid))[0],409)
            self.assertEqual(self.sync_native(capture),result)
        model.assert_not_called()
        self.assertEqual(self.call('/recognition/batches')[1],[])

    def test_submitted_withdraws_only_bound_pending_candidates_and_retains_raw_results(self):
        sync=self.sync_native(self.native_capture('active'))
        batch=self.run_request(sync['requests'][0]);item=batch['items'][0]
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        stored=self.call('/recognition/batches')[1][0]
        self.assertEqual(stored['items'][0]['state'],'ignored')
        self.assertEqual(stored['result']['status'],'no_tasks')
        self.assertEqual(stored['items'][0]['version'],item['version']+1)
        with app.connection() as db:
            row=db.execute('SELECT resolution FROM recognition_candidates WHERE id=?',(item['id'],)).fetchone()
            self.assertEqual(json.loads(row['resolution'])['action'],'source_submitted')
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0],1)
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='reopen',version=2))[0],409)
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],409)

    def test_submitted_preserves_applied_tasks_and_user_ignored_decisions(self):
        sync=self.sync_native(self.native_capture('active'));view=sync['requests'][0]
        iid=view['records'][0]['input_id'];draft=json.loads(self.drafts[iid]);draft['tasks']*=2;self.drafts[iid]=json.dumps(draft)
        first,second=self.run_request(view)['items']
        self.assertEqual(self.call('/recognition/candidates/'+first['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        self.assertEqual(self.call('/recognition/candidates/'+second['id']+'/resolve',dict(action='ignore',version=1))[0],200)
        before=self.call('/workspace')[1]['tasks']
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        self.assertEqual(self.call('/workspace')[1]['tasks'],before)
        states={i['id']:(i['state'],i['version']) for i in self.call('/recognition/batches')[1][0]['items']}
        self.assertEqual(states,{first['id']:('applied',2),second['id']:('ignored',2)})
        self.assertEqual(self.call('/recognition/candidates/'+first['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)

    def test_late_model_and_late_capture_cannot_restore_submitted_candidate(self):
        sync=self.sync_native(self.native_capture('active'));view=sync['requests'][0]
        submitted=self.native_capture('submitted','2026-10-10T11:00:00+08:00')
        def complete_during_model(request,ApiError):
            output=self.model(request,ApiError)
            with app.connection() as db:teaching.register(db,self.uid,'synthetic-course-user',[submitted])
            return output
        with patch.object(teaching,'recognize',side_effect=complete_during_model):
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.assertEqual(self.call('/recognition/batches')[1],[])
        self.assertEqual(self.sync_native(self.native_capture('active'))['requests'],[])
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT state FROM teaching_submission_sources').fetchone()[0],'submitted')

    def test_submitted_then_active_requires_review_even_after_restart(self):
        self.sync_native(self.native_capture('submitted'))
        with app.connection() as db:teaching.initialize(db)
        view=self.sync_native(self.native_capture('active','2026-10-10T11:00:00+08:00'))['requests'][0]
        item=self.run_request(view)['items'][0]
        self.assertTrue(item['candidate']['needs_review'])
        self.assertIn('曾显示已提交',''.join(item['candidate']['review_reasons']))
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],400)

    def test_missing_native_state_after_submitted_does_not_falsely_keep_completion(self):
        self.sync_native(self.native_capture('submitted'))
        capture=self.native_capture('unknown','2026-10-10T11:00:00+08:00');capture['assignment']=None
        view=self.sync_native(capture)['requests'][0]
        item=self.run_request(view)['items'][0]
        self.assertTrue(item['candidate']['needs_review'])
        with app.connection() as db:
            row=db.execute('SELECT state,review_pending FROM teaching_submission_sources').fetchone()
            self.assertEqual(tuple(row),('unknown',1))

    def test_completed_source_hides_obsolete_failure_but_keeps_original_error(self):
        view=self.sync_native(self.native_capture('active'))['requests'][0]
        self.drafts[view['records'][0]['input_id']]='not json'
        self.assertEqual(self.run_request(view)['result']['status'],'failed')
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        result=self.call('/recognition/batches')[1][0]['result']
        self.assertEqual(result['status'],'no_tasks');self.assertEqual(result['errors'],[])
        self.assertIn('source_submitted',result['record_results'][0]['warnings'][0])
        with app.connection() as db:
            original=json.loads(db.execute('SELECT result FROM recognition_batches').fetchone()[0])
            self.assertEqual(original['status'],'failed')

    def test_assignment_identity_and_browser_supplied_state_are_rejected(self):
        capture=self.native_capture('submitted');capture['assignment']['content_id']='other'
        with patch.object(app.source_sync.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(capture))):
            self.assertEqual(self.call('/sources/sync',dict(sources=['teaching_web']))[0],502)
        self.assertEqual(self.call('/recognition/batches',self.native_capture('submitted'))[0],400)

    def test_submitted_account_cannot_withdraw_other_accounts_candidates(self):
        view=self.sync_native(self.native_capture('active'))['requests'][0]
        first=self.run_request(view)['items'][0]
        self.call('/sources/credentials',dict(username='another-campus-account',password='synthetic-password'))
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        self.assertEqual(self.call('/recognition/batches')[1][0]['items'][0]['state'],'pending')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT version FROM recognition_candidates WHERE id=?',(first['id'],)).fetchone()[0],1)

    def run_request(self,view):
        schema=json.loads((teaching.ROOT/'contracts/schemas/teaching-workspace-v0.1.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema).validate(view)
        with patch.object(teaching,'recognize',side_effect=self.model):
            status,result=self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))
        self.assertEqual(status,200,result)
        batch=next(b for b in result if b['request']['request_id']==view['request_id'])
        Draft202012Validator(schema).validate(batch['result'])
        return batch

    def test_sync_recognize_and_apply_preserves_course_date_and_repeat_decisions(self):
        request,sync=self.capture(('unread-supplement','yearless-current-assignment-term','yearless-unknown-age'))
        for index,view in enumerate(sync['requests']):
            batch=self.run_request(view);item=batch['items'][0];card=item['candidate']
            self.assertEqual(card['course_or_project'],'测试课程')
            self.assertEqual(card['needs_review'],index==2)
            if index<2:
                self.assertEqual(card['deadline']['value'],'2026-10-23')
                status,result=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
                self.assertEqual(status,200,result)
            else:
                self.assertEqual(card['deadline']['status'],'inferred')
                self.assertEqual(card['deadline']['value'],'2026-10-23')
                self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],400)
            with app.connection() as db:
                row=db.execute('SELECT request,result FROM recognition_batches WHERE request_id=?',(view['request_id'],)).fetchone()
                self.assertEqual(json.loads(row['request'])['contract_version'],'0.4')
                self.assertEqual(json.loads(row['result'])['policy_version'],'teaching-policy-v1:'+'0'*64)
        tasks=self.call('/workspace')[1]['tasks']['value']
        self.assertEqual(len(tasks),2);self.assertTrue(all(t['course']=='测试课程' for t in tasks))
        self.assertTrue(all(t['due']==dict(date='2026-10-23',precision='date') for t in tasks))
        _,again=self.capture(('unread-supplement','yearless-current-assignment-term','yearless-unknown-age'))
        self.assertEqual(again['summary'],dict(total=3,skipped=3,pending=0))

    def test_stored_unknown_age_card_refreshes_date_without_changing_user_state(self):
        _,sync=self.capture(('yearless-unknown-age',))
        batch=self.run_request(sync['requests'][0]); item=batch['items'][0]
        with app.connection() as db:
            legacy=deepcopy(item['candidate']);legacy['deadline'].update(status='unknown',value=None,precision='unknown')
            db.execute('UPDATE recognition_candidates SET candidate=? WHERE id=?',(json.dumps(legacy),item['id']))
        current=self.call('/recognition/batches')[1][0]['items'][0]
        self.assertEqual(current['id'],item['id'])
        self.assertEqual((current['state'],current['version']),('pending',1))
        self.assertEqual(current['candidate']['deadline']['value'],'2026-10-23')
        self.assertEqual(current['candidate']['deadline']['status'],'inferred')
        self.assertTrue(current['candidate']['needs_review'])

    def test_failed_record_retry_preserves_other_success_and_no_duplicate_cards(self):
        _,sync=self.capture(('explicit-complete','explicit-complete'))
        bad=sync['requests'][0];iid=bad['records'][0]['input_id'];valid=self.drafts[iid];self.drafts[iid]='invalid JSON'
        self.assertEqual(self.run_request(bad)['result']['status'],'failed')
        good=self.run_request(sync['requests'][1]);original=good['items'][0]['id']
        self.drafts[iid]=valid
        self.assertEqual(self.run_request(bad)['result']['status'],'success')
        self.assertEqual(self.run_request(sync['requests'][1])['items'][0]['id'],original)

    def test_browser_cannot_grant_trust_and_stale_or_removed_sources_cannot_apply(self):
        request,sync=self.capture(('explicit-complete',));view=sync['requests'][0]
        self.assertEqual(self.call('/recognition/batches',request)[0],400)
        self.assertEqual(self.call('/recognition/batches',dict(contract_version=teaching.RUN,request_id=view['request_id'],records=[]))[0],400)
        batch=self.run_request(view);item=batch['items'][0]
        newer=deepcopy(request);newer['records'][0]['record']['text']+=' 新增备注。'
        with app.connection() as db:teaching.register(db,self.uid,'synthetic-course-user',[newer])
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],409)
        self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.call('/sources/credentials/remove',{})
        self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)

    def test_no_tasks_and_multiple_tasks_are_distinct(self):
        _,sync=self.capture(('explicit-complete','explicit-complete'))
        first,second=sync['requests'];self.drafts[first['records'][0]['input_id']]='{"tasks":[]}'
        iid=second['records'][0]['input_id'];draft=json.loads(self.drafts[iid]);draft['tasks']*=2;self.drafts[iid]=json.dumps(draft)
        self.assertEqual(self.run_request(first)['result']['status'],'no_tasks')
        batch=self.run_request(second);self.assertEqual(len(batch['items']),2)
        self.assertNotEqual(batch['items'][0]['candidate']['candidate_id'],batch['items'][1]['candidate']['candidate_id'])

    def test_another_user_cannot_run_a_captured_snapshot(self):
        _,sync=self.capture(('explicit-complete',));view=sync['requests'][0]
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call('/auth/register',dict(name='Other',email='other@example.test',password='Synthetic-password!'))
        self.call('/sources/credentials',dict(username='synthetic-course-user',password='synthetic-password'))
        with patch.object(teaching,'recognize') as model:
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        model.assert_not_called()

    def test_account_change_during_model_execution_discards_late_result(self):
        _,sync=self.capture(('explicit-complete',));view=sync['requests'][0]
        def change_account(request,ApiError):
            result=self.model(request,ApiError)
            with app.connection() as db:
                db.execute('UPDATE source_credentials SET username=? WHERE user_id=?',('different-course-account',self.uid))
            return result
        with patch.object(teaching,'recognize',side_effect=change_account):
            self.assertEqual(self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=view['request_id']))[0],409)
        self.assertEqual(self.call('/recognition/batches')[1],[])

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

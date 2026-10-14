"""Authenticated sync -> private attachment -> recognition -> task/evidence HTTP tests."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
import recognition
import reprocessing
import teaching
import teaching_assets
import test_teaching as harness
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.docx_reader import DOCX_MIME
from ddl_text.teaching_pipeline import process_teaching_request
from ddl_text.tests.docx_cases import package, paragraph
from ddl_text.tests.media_cases import image_bytes
from ddl_text.tests.teaching_cases import policy_case, model_task
from ddl_text.tests.test_teaching_pipeline import example, RecordClient
from contracts.teaching_io import snapshot_digest

TEXT='请于2026年11月17日23:30前提交合成实验报告。'


class TeachingAssetsIntegrationTests(unittest.TestCase):
    setUp=harness.TeachingIntegrationTests.setUp
    tearDown=harness.TeachingIntegrationTests.tearDown
    call=harness.TeachingIntegrationTests.call

    def sync(self, data=None, mime=DOCX_MIME, malformed=False):
        if not hasattr(self,'default_docx'): self.default_docx=package(paragraph(TEXT))
        data=data or self.default_docx  # Reuse identical ZIP bytes, including entry timestamps.
        request,_=example(); r=request['records'][0]['record']
        r.update(text='请完成附件中的作业，提交时间见附件。',references=[dict(
            ref_id='file:1',kind='attachment',label='合成作业.docx',read_state='unread',text=None)])
        digest=hashlib.sha256(data).hexdigest()
        def crawl(*args,**kwargs):
            folder=Path(kwargs['env']['PKU_ATTACHMENT_DIR'])
            (folder/(digest+'.bin')).write_bytes(data)
            (folder/'manifest.json').write_text(json.dumps([dict(input_id=r['input_id'],ref_id='file:1',
                sha256='../../secret' if malformed else digest,mime_type=mime,size_bytes=len(data))]),encoding='utf-8')
            return SimpleNamespace(returncode=0,stdout=json.dumps(request))
        with patch.object(app.source_sync.subprocess,'run',side_effect=crawl):
            status,out=self.call('/sources/sync',dict(sources=['teaching_web']))
        if malformed:
            self.assertEqual(status,502,out);return
        self.assertEqual(status,200,out)
        return out

    def final_model(self,request,**kwargs):
        record=request['records'][0]['record']
        self.assertIn(TEXT,record['references'][0]['text'])
        pair=policy_case('explicit-complete')['pair'];pair['record']=deepcopy(record)
        pair['assessment']['input_id']=record['input_id']
        quote=dict(part_id='file:1',quote=TEXT)
        pair['assessment']['action']=dict(state='supported',evidence=[quote])
        pair['assessment']['deadline']=dict(value='2026-11-17T23:30:00+08:00',precision='datetime',
            derivation='absolute',anchor=None,evidence=[quote])
        pair['assessment'].update(unread_relations=[],other_review_reasons=[])
        task=model_task(pair);task.update(title='提交合成实验报告',description=TEXT)
        out=process_teaching_request(request,client=RecordClient({record['input_id']:json.dumps(dict(tasks=[task]))}),
            settings=Settings(),verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
        out['policy_version']=teaching.teaching_policy_manifest(kwargs['settings'])['version']
        return out

    def run_record(self,request_id,vision=None):
        options=dict(provider='qiniu',key='synthetic-key',model='synthetic-test',endpoint='https://api.qnaigc.com/v1')
        with patch.object(teaching,'live_options',return_value=options), \
             patch.object(teaching,'process_teaching_request',side_effect=self.final_model), \
             patch.object(teaching.ChatClient,'complete_json',side_effect=vision) as call:
            status,out=self.call('/recognition/retry',dict(contract_version=teaching.RUN,request_id=request_id))
        self.assertEqual(status,200,out)
        return next(b for b in out if b['request']['request_id']==request_id),call

    def test_docx_only_deadline_reaches_task_and_evidence_without_extra_model(self):
        synced=self.sync();rid=synced['requests'][0]['request_id']
        batch,vision=self.run_record(rid)
        vision.assert_not_called()
        card=batch['items'][0];self.assertFalse(card['candidate']['needs_review'])
        self.assertEqual(card['candidate']['deadline']['value'],'2026-11-17T23:30:00+08:00')
        self.assertEqual(card['candidate']['course_or_project'],'测试课程')
        self.assertEqual(card['candidate']['evidence'][0]['field'],'file:1')
        status,out=self.call('/recognition/candidates/'+card['id']+'/resolve',dict(action='create',auto_apply=True,version=1))
        self.assertEqual(status,200,out)
        task=self.call('/workspace')[1]['tasks']['value'][0]
        self.assertEqual(task['due'],dict(date='2026-11-17',time='23:30',precision='datetime'))
        self.assertEqual(self.sync()['summary']['skipped'],1)
        with app.connection() as db:
            captured=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
            self.assertIsNone(captured['records'][0]['record']['references'][0]['text'])
            cache=db.execute('SELECT result FROM teaching_asset_files').fetchone()[0]
            self.assertIn(TEXT,json.loads(cache)['text'])
            self.assertEqual(teaching_assets.load(db,'another-user',teaching.account('synthetic-course-user'),rid),[])
        self.call('/sources/credentials/remove',{})
        with app.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0],0)

    def test_image_timeout_can_retry_and_reprocessing_reuses_read_cache(self):
        rid=self.sync(image_bytes(),'image/png')['requests'][0]['request_id']
        failed,_=self.run_record(rid,AgentError('agent_timeout',True))
        self.assertEqual(failed['result']['status'],'failed');self.assertFalse(failed['items'])
        good=json.dumps(dict(pages=[dict(index=1,text=TEXT,uncertain=False,note='')]))
        batch,vision=self.run_record(rid,lambda *args:good)
        vision.assert_called_once();self.assertTrue(batch['items'])
        # Same account-bound execution context is used by the reprocessing worker.
        import reprocessing
        with app.connection() as db:
            request=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
            context=teaching.attachment_context(db,self.uid,rid)
        request['request_id']='synthetic:reprocessing'
        with patch.object(teaching,'live_options',return_value=dict(provider='qiniu',key='x',model='synthetic-test',endpoint='https://api.qnaigc.com/v1')), \
             patch.object(teaching,'process_teaching_request',side_effect=self.final_model), \
             patch.object(teaching.ChatClient,'complete_json',side_effect=AssertionError('must use cached transcript')):
            result,_=reprocessing.recognize_saved(request,True,app.ApiError,attachments=context)
        self.assertEqual(result['status'],'success')

    def test_changed_file_changes_snapshot_and_forged_path_rejected(self):
        rid=self.sync()['requests'][0]['request_id']
        newer=self.sync(package(paragraph(TEXT+'要求有所更改。')))['requests'][0]['request_id']
        self.assertNotEqual(rid,newer)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0],1)
            self.assertEqual(teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid),[])
        self.sync(malformed=True)

    def reprocess(self, operation, auto_apply=False):
        account=next(a for a in self.call('/recognition/reprocessing/sources')[1]['accounts']
            if a['label']=='当前教学网账号')
        options=dict(provider='qiniu',key='synthetic-key',model='synthetic-test',endpoint='https://api.qnaigc.com/v1')
        good=json.dumps(dict(pages=[dict(index=1,text=TEXT,uncertain=False,note='')]))
        with patch.object(teaching,'live_options',return_value=options), \
             patch.object(teaching,'process_teaching_request',side_effect=self.final_model), \
             patch.object(teaching.ChatClient,'complete_json',return_value=good) as vision, \
             patch.object(recognition,'can_auto_apply',return_value=auto_apply):
            status,run=self.call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',
                operation_id=operation,source_account_id=account['source_account_id'],
                input_ids=[account['records'][0]['input_id']]))
            self.assertEqual(status,202,run)
            self.assertTrue(reprocessing.wait_for_jobs(app.DB))
            saved=self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]
        return saved,vision

    def test_repeated_reprocessing_prune_restart_preserves_raw_snapshot_and_image(self):
        data=image_bytes();rid=self.sync(data,'image/png')['requests'][0]['request_id']
        account=teaching.account('synthetic-course-user')
        with app.connection() as db:
            original=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
        # Keep cards pending to exercise two successive source-pointer advances.
        for index in range(2):
            saved,vision=self.reprocess('image-lineage:'+str(index))
            self.assertEqual(saved['state'],'succeeded',saved)
            self.assertEqual(vision.call_count,1 if index==0 else 0)
            with app.connection() as db:
                current=db.execute('SELECT request_id FROM teaching_sources').fetchone()[0]
                self.assertNotEqual(current,rid)
                expected=deepcopy(original);expected['request_id']=current
                self.assertEqual(teaching.resolve_snapshot(db,self.uid,current,app.ApiError),expected)
                self.assertEqual(teaching_assets.load(db,self.uid,account,current)[0]['data'],data)
                enriched=json.loads(db.execute('SELECT request FROM recognition_batches WHERE request_id=?',(current,)).fetchone()[0])
                self.assertEqual(enriched['records'][0]['record']['references'][0]['text'],'【模型转录：图片】\n'+TEXT)
                self.assertEqual(teaching_assets.load(db,'other-user',account,current),[])
                self.assertEqual(teaching_assets.load(db,self.uid,'other-account',current),[])
                # This is the prune that used to delete the sole original file.
                teaching_assets.prune(db,self.uid)
                self.assertEqual(teaching_assets.load(db,self.uid,account,current)[0]['data'],data)
            self.assertEqual(self.sync(data,'image/png')['requests'][0]['request_id'],current)
            rid=current
        # Reinitialize storage through a new connection, with no in-memory cache.
        app.initialize()
        saved,vision=self.reprocess('after-restart',auto_apply=True)
        self.assertEqual(saved['state'],'succeeded',saved);vision.assert_not_called()
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)
        self.assertEqual(self.call('/sources/credentials',dict(username='changed-course-user',password='synthetic-password'))[0],200)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_refs').fetchone()[0],0)
        self.sync(data,'image/png')
        self.assertEqual(self.call('/sources/credentials/remove',{})[0],200)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_refs').fetchone()[0],0)

    def test_failed_final_commit_rolls_back_attachment_rebind_and_source(self):
        data=image_bytes();rid=self.sync(data,'image/png')['requests'][0]['request_id']
        account=teaching.account('synthetic-course-user')
        with app.connection() as db:
            original=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
        with patch.object(recognition,'resolve_candidate',side_effect=RuntimeError('synthetic commit failure')):
            saved,_=self.reprocess('rollback-image-lineage',auto_apply=True)
        self.assertEqual(saved['state'],'interrupted',saved)
        with app.connection() as db:
            self.assertEqual(teaching.resolve_snapshot(db,self.uid,rid,app.ApiError),original)
            teaching_assets.prune(db,self.uid)
            self.assertEqual(teaching_assets.load(db,self.uid,account,rid)[0]['data'],data)
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_candidates').fetchone()[0],0)

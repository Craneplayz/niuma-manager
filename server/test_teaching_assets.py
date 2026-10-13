"""Authenticated sync -> private attachment -> recognition -> task/evidence HTTP tests."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
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

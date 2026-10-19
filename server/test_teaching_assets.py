"""Authenticated sync -> private attachment -> recognition -> task/evidence HTTP tests."""
import hashlib
import json
import sqlite3
from copy import deepcopy
from contextlib import closing
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


class AssetIndexUpgradeTests(unittest.TestCase):
    def test_existing_database_upgrade_preserves_shared_files_cache_and_account_isolation(self):
        with closing(sqlite3.connect(':memory:')) as db:
            teaching.initialize(db)
            # Simulate an existing release with populated tables and no new indexes.
            db.execute('DROP INDEX teaching_sources_request')
            db.execute('DROP INDEX teaching_asset_refs_file')
            for uid, account in [('user', 'course'), ('user', 'other-course'), ('other-user', 'course')]:
                db.execute('INSERT INTO teaching_sources VALUES(?,?,?,?,?,?)', (uid, account, 'input', 'digest', 'live', '{}'))
                for digest in ('shared', 'orphan'):
                    db.execute('INSERT INTO teaching_asset_files VALUES(?,?,?,?,?,?,?)',
                        (uid, account, digest, 'application/pdf', b'synthetic', 'cached-version', 'cached-text'))
                for request in ('live', 'obsolete'):
                    db.execute('INSERT INTO teaching_asset_refs VALUES(?,?,?,?,?,?)',
                        (uid, account, request, 'file:0', 'shared', 'application/pdf'))
            before = db.execute('SELECT * FROM teaching_asset_files ORDER BY user_id,account,sha256').fetchall()
            teaching.initialize(db)
            teaching.initialize(db)
            self.assertEqual(before, db.execute('SELECT * FROM teaching_asset_files ORDER BY user_id,account,sha256').fetchall())
            teaching_assets.prune(db, 'user')
            for account in ('course', 'other-course'):
                self.assertEqual(db.execute('SELECT sha256,data,cache_version,result FROM teaching_asset_files WHERE user_id=? AND account=?',
                    ('user', account)).fetchall(), [('shared', b'synthetic', 'cached-version', 'cached-text')])
                self.assertEqual(db.execute('SELECT request_id FROM teaching_asset_refs WHERE user_id=? AND account=?',
                    ('user', account)).fetchall(), [('live',)])
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_files WHERE user_id=?', ('other-user',)).fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_refs WHERE user_id=?', ('other-user',)).fetchone()[0], 2)


class TeachingAssetsIntegrationTests(unittest.TestCase):
    def test_upgrade_reuses_legacy_request_and_stable_file_even_if_read_status_varies(self):
        from contracts.teaching_io import canonical
        rid=self.sync()['requests'][0]['request_id']
        with app.connection() as db:
            request=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
            files=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid)
            entry=deepcopy(request['records'][0])
            for key in ('collected_at','snapshot_id'):entry['record'].pop(key)
            old=hashlib.sha256(canonical(dict(source=entry,assets=teaching_assets.identity(files)))).hexdigest()
            db.execute('UPDATE teaching_sources SET digest=? WHERE user_id=?',(old,self.uid))
            record=request['records'][0]['record'];record['references'][0].update(read_state='failed',text=None)
            again=teaching.register(db,self.uid,'synthetic-course-user',[request],assets={record['input_id']:files})
            self.assertEqual(again['requests'][0]['request_id'],rid)

    def test_source_change_preserves_unchanged_attachment_cache(self):
        rid=self.sync()['requests'][0]['request_id']
        with app.connection() as db:
            original=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
            files=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid)
            db.execute('UPDATE teaching_asset_files SET cache_version=?,result=? WHERE user_id=?',
                       ('synthetic-version',json.dumps(dict(read_state='complete',text=TEXT)),self.uid))
        original['records'][0]['record']['text']+=' Extra requirement'
        input_id=original['records'][0]['record']['input_id']
        with patch.object(teaching_assets,'MAX_ACCOUNT',len(files[0]['data'])),app.connection() as db:
            changed=teaching.register(db,self.uid,'synthetic-course-user',[original],assets={input_id:files})
            newer=changed['requests'][0]['request_id']
            self.assertNotEqual(newer,rid)
            saved=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),newer)
            self.assertEqual(saved[0]['cache_version'],'synthetic-version')
            self.assertEqual(json.loads(saved[0]['result'])['text'],TEXT)
            self.assertEqual(teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid),[])

    def test_replacing_attachment_near_quota_reclaims_retired_bytes_through_http(self):
        old=package(paragraph(TEXT));new=package(paragraph(TEXT+'新的提交要求。'))
        with patch.object(teaching_assets,'MAX_ACCOUNT',max(len(old),len(new))):
            rid=self.sync(old)['requests'][0]['request_id']
            newer=self.sync(new)['requests'][0]['request_id']
        self.assertNotEqual(newer,rid)
        with app.connection() as db:
            self.assertEqual(teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid),[])
            files=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),newer)
            self.assertEqual([item['data'] for item in files],[new])
            self.assertEqual(db.execute('SELECT sum(length(data)) FROM teaching_asset_files').fetchone()[0],len(new))

    def test_mixed_attachment_revision_reclaims_quota_and_keeps_unchanged_cache(self):
        request,_=example();record=request['records'][0]['record']
        record['references']=[dict(ref_id=ref,kind='attachment',label=ref,read_state='unread',text=None)
                              for ref in ('keep','replace')]
        def asset(ref,data):
            return dict(ref_id=ref,sha256=hashlib.sha256(data).hexdigest(),mime_type='application/pdf',data=data)
        keep=asset('keep',b'keep');old=asset('replace',b'old!');new=asset('replace',b'new!')
        account=teaching.account('synthetic-course-user');input_id=record['input_id']
        with patch.object(teaching_assets,'MAX_ACCOUNT',8),app.connection() as db:
            first=teaching.register(db,self.uid,'synthetic-course-user',[request],assets={input_id:[keep,old]})['requests'][0]['request_id']
            db.execute('UPDATE teaching_asset_files SET cache_version=?,result=? WHERE sha256=?',
                ('synthetic-version',json.dumps(dict(read_state='complete',text=TEXT)),keep['sha256']))
            second=teaching.register(db,self.uid,'synthetic-course-user',[request],assets={input_id:[new,keep]})['requests'][0]['request_id']
            self.assertNotEqual(first,second)
            files={item['ref_id']:item for item in teaching_assets.load(db,self.uid,account,second)}
            self.assertEqual(files['replace']['data'],b'new!')
            self.assertEqual(files['keep']['cache_version'],'synthetic-version')
            self.assertEqual(json.loads(files['keep']['result'])['text'],TEXT)
            self.assertEqual(db.execute('SELECT sum(length(data)) FROM teaching_asset_files').fetchone()[0],8)

    def test_attachment_over_quota_rolls_back_pruning_and_source_revision(self):
        old=package(paragraph(TEXT));new=package(paragraph(TEXT+'新的提交要求。'))
        rid=self.sync(old)['requests'][0]['request_id']
        with app.connection() as db:
            original=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
            db.execute('UPDATE teaching_asset_files SET cache_version=?,result=? WHERE user_id=?',
                ('synthetic-version',json.dumps(dict(read_state='complete',text=TEXT)),self.uid))
        with patch.object(teaching_assets,'MAX_ACCOUNT',len(new)-1):
            self.assertIn('存储限制',self.sync(new,expected_status=413)['error'])
        with app.connection() as db:
            self.assertEqual(teaching.resolve_snapshot(db,self.uid,rid,app.ApiError),original)
            files=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid)
            self.assertEqual([item['data'] for item in files],[old])
            self.assertEqual(files[0]['cache_version'],'synthetic-version')
            self.assertEqual(json.loads(files[0]['result'])['text'],TEXT)
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0],1)

    def setUp(self):
        harness.TeachingIntegrationTests.setUp(self)
        transport=patch('ddl_text.teaching_attachment_reader.transcribe',
            side_effect=lambda client,instructions,parts,settings:client.complete_json(instructions,parts,settings))
        transport.start();self.addCleanup(transport.stop)
    tearDown=harness.TeachingIntegrationTests.tearDown
    call=harness.TeachingIntegrationTests.call

    def sync(self, data=None, mime=DOCX_MIME, malformed=False, pdf_text=None, expected_status=200):
        if not hasattr(self,'default_docx'): self.default_docx=package(paragraph(TEXT))
        data=data or self.default_docx  # Reuse identical ZIP bytes, including entry timestamps.
        request,_=example(); r=request['records'][0]['record']
        r.update(text='请完成附件中的作业，提交时间见附件。',references=[dict(
            ref_id='file:1',kind='attachment',label='合成作业.docx',read_state='unread',text=None)])
        if pdf_text is not None:
            r['references'][0].update(read_state='complete',text=pdf_text)
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
        self.assertEqual(status,expected_status,out)
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
        self.assert_cached_reprocess(rid)

    def test_complete_pdf_capture_skips_visual_transcription_through_http_and_reprocessing(self):
        import io
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        out=io.BytesIO(); doc=canvas.Canvas(out,pagesize=(800,300))
        for n in range(17):
            doc.setFont('STSong-Light',12)
            doc.drawString(20,250,TEXT if n==16 else f'Page {n+1}'); doc.showPage()
        doc.save()
        original='\n'.join(f'Page {i}' for i in range(1,17))+'\n'+TEXT
        rid=self.sync(out.getvalue(),'application/pdf',pdf_text=original)['requests'][0]['request_id']
        batch,vision=self.run_record(rid,AssertionError('complete PDF text must not be transcribed again'))
        vision.assert_not_called()
        self.assertEqual(batch['result']['status'],'success')
        with app.connection() as db:
            saved=json.loads(db.execute('SELECT request FROM recognition_batches WHERE request_id=?',(rid,)).fetchone()[0])
        self.assertIn(TEXT,saved['records'][0]['record']['references'][0]['text'])
        self.assertEqual(batch['items'][0]['candidate']['deadline']['value'],'2026-11-17T23:30:00+08:00')
        self.assert_cached_reprocess(rid)

    def test_complete_capture_cannot_bypass_pdf_visibility_check_or_restore_hidden_text(self):
        from ddl_text.tests.pdf_cases import layered_pdf_bytes
        captured='Visible information\nHidden assignment due Friday\nOrdinary second page'
        rid=self.sync(layered_pdf_bytes(),'application/pdf',pdf_text=captured)['requests'][0]['request_id']
        good=json.dumps(dict(pages=[dict(index=1,text=TEXT,uncertain=False,note='')]))
        batch,vision=self.run_record(rid,lambda *args:good)
        self.assertEqual(batch['result']['status'],'success')
        vision.assert_called_once()
        self.assertEqual(len(vision.call_args.args[1]),2)
        with app.connection() as db:
            saved=json.loads(db.execute('SELECT request FROM recognition_batches WHERE request_id=?',(rid,)).fetchone()[0])
        text=saved['records'][0]['record']['references'][0]['text']
        self.assertNotIn('Hidden assignment',text)
        self.assertIn('原件文字：第 2 页',text)
        self.assert_cached_reprocess(rid)

    def test_attachment_format_failure_keeps_its_code_and_retry_recovers(self):
        rid=self.sync(image_bytes(),'image/png')['requests'][0]['request_id']
        failed,vision=self.run_record(rid,lambda *args:'{"pages":[]}')
        self.assertEqual(vision.call_count,2)
        self.assertEqual(failed['result']['errors'][0]['code'],'invalid_agent_output')
        self.assertIn('页码',failed['result']['errors'][0]['message'])
        self.assertFalse(failed['items'])
        with app.connection() as db:
            self.assertIsNone(db.execute('SELECT result FROM teaching_asset_files WHERE user_id=?',(self.uid,)).fetchone()[0])
        good=json.dumps(dict(pages=[dict(index=1,text=TEXT,uncertain=False)]))
        batch,vision=self.run_record(rid,lambda *args:good)
        self.assertEqual(batch['result']['status'],'success')
        vision.assert_called_once()

    def test_long_visual_pdf_finishes_one_background_run_without_shared_75_second_deadline(self):
        import source_jobs as jobs
        import ddl_text.teaching_attachment_reader as reader
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        synced=self.sync(pdf_bytes(('Synthetic page',)*43),'application/pdf')
        rid=synced['requests'][0]['request_id'];clock=[100.0];seen=[];budgets=[]
        fake_time=SimpleNamespace(monotonic=lambda:clock[0])
        def render(data,asset,limits):
            clock[0]+=1
            return dict(pages=[b'png']*min(limits.max_pages,43-limits.start_page),total_pages=43,error=None)
        def vision(instructions,parts,settings):
            if settings.timeout_seconds<20: raise AgentError('agent_timeout',True)
            seen.extend(p['text'] for p in parts if p['type']=='text')
            clock[0]+=20
            with app.connection() as db:
                self.assertIsNone(db.execute('SELECT result FROM recognition_batches WHERE request_id=?',(rid,)).fetchone())
            return json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False) for i in range(len(parts)//2)]))
        def final(request,**kwargs):
            budgets.append(kwargs['budget_seconds'])
            self.assertIn('第 43 页',request['records'][0]['record']['references'][0]['text'])
            return self.final_model(request,**kwargs)
        options=dict(provider='qiniu',key='synthetic',model='synthetic-test',endpoint='https://api.qnaigc.com/v1')
        with patch.object(jobs.source_sync,'collect',return_value=synced), \
             patch.object(teaching,'live_options',return_value=options), \
             patch.object(teaching,'process_teaching_request',side_effect=final), \
             patch.object(teaching.ChatClient,'complete_json',side_effect=vision), \
             patch.object(reader,'read_pdf',side_effect=render), \
             patch.object(reader,'time',fake_time), patch.object(teaching_assets,'time',fake_time):
            status,run=self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',
                sources=['teaching_web'],consent=True,operation_id='long-pdf'))
            self.assertEqual(status,202,run)
            jobs.wait_for_jobs(app.DB)
        final=self.call('/sources/sync-runs/'+run['run_id'])[1]
        self.assertEqual(final['state'],'succeeded',final)
        self.assertEqual(final['counts']['failed'],0)
        self.assertEqual(len(seen),43);self.assertEqual(len(set(seen)),43)
        self.assertGreater(clock[0]-100,75);self.assertEqual(budgets,[90])
        with app.connection() as db:
            cached=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(cached['read_state'],'complete')
            self.assertIn('第 43 页',cached['text'])

    def test_stop_background_pdf_keeps_checkpoint_without_starting_next_batch(self):
        import source_jobs as jobs
        import ddl_text.teaching_attachment_reader as reader
        import threading
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        synced=self.sync(pdf_bytes(('Synthetic page',)*13),'application/pdf')
        entered=threading.Event();release=threading.Event();seen=[]
        def render(data,asset,limits):
            return dict(pages=[b'png']*min(limits.max_pages,13-limits.start_page),total_pages=13,error=None)
        def vision(instructions,parts,settings):
            seen.append(parts[0]['text']);entered.set();release.wait(5)
            return json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False) for i in range(len(parts)//2)]))
        options=dict(provider='qiniu',key='synthetic',model='synthetic-test',endpoint='https://api.qnaigc.com/v1')
        with patch.object(jobs.source_sync,'collect',return_value=synced), \
             patch.object(teaching,'live_options',return_value=options), \
             patch.object(teaching,'process_teaching_request',side_effect=AssertionError('stopped')) as final_model, \
             patch.object(teaching.ChatClient,'complete_json',side_effect=vision), \
             patch.object(reader,'read_pdf',side_effect=render):
            status,run=self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',
                sources=['teaching_web'],consent=True,operation_id='stop-pdf'))
            try:
                self.assertEqual(status,202,run);self.assertTrue(entered.wait(5))
                status,stopped=self.call('/sources/sync-runs/'+run['run_id']+'/stop',dict(contract_version='source-sync/0.1'))
                self.assertEqual(status,200);self.assertEqual(stopped['state'],'stopped')
            finally:
                release.set();jobs.wait_for_jobs(app.DB)
        final_model.assert_not_called();self.assertEqual(len(seen),1)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recognition_batches').fetchone()[0],0)
            checkpoint=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(checkpoint['pdf_checkpoint'],1);self.assertEqual(len(checkpoint['pages']),6)

    def test_pdf_timeout_restart_resumes_persisted_pages_without_repeating_completed_calls(self):
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        rid=self.sync(pdf_bytes(('Synthetic page',)*13),'application/pdf')['requests'][0]['request_id']
        seen=[]
        def vision(instructions,parts,settings):
            labels=[p['text'] for p in parts if p['type']=='text'];seen.append(labels)
            if 2<=len(seen)<=4:raise AgentError('agent_timeout',True)
            return json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False,note='') for i in range(len(labels))]))
        failed,_=self.run_record(rid,vision)
        self.assertEqual(failed['result']['status'],'failed')
        with app.connection() as db:
            cached=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(cached['total_pages'],13);self.assertEqual(len(cached['pages']),6)
            self.assertEqual(teaching_assets.load(db,'other-user',teaching.account('synthetic-course-user'),rid),[])
        app.initialize()
        good,_=self.run_record(rid,vision)
        self.assertEqual(good['result']['status'],'success')
        self.assertIn('第 7 页',seen[4][0]);self.assertEqual(len(seen),6)
        with app.connection() as db:
            cached=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(cached['read_state'],'complete');self.assertIn('第 13 页',cached['text'])
        self.assert_cached_reprocess(rid)

    def test_pdf_later_render_failure_keeps_evidence_through_http_and_cache(self):
        import io
        from pypdf import PdfWriter
        writer=PdfWriter()
        for _ in range(2): writer.add_blank_page(width=300,height=300)
        writer.add_blank_page(width=10000,height=10000)
        output=io.BytesIO();writer.write(output)
        rid=self.sync(output.getvalue(),'application/pdf')['requests'][0]['request_id']
        vision=lambda *args:json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False) for i in range(2)]))
        batch,calls=self.run_record(rid,vision)
        calls.assert_called_once()
        self.assertTrue(batch['items'])
        self.assertEqual(batch['items'][0]['candidate']['deadline']['value'],'2026-11-17T23:30:00+08:00')
        with app.connection() as db:
            cached=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(cached['read_state'],'partial')
            self.assertIn(TEXT,cached['text']);self.assertIn('第 3 页',cached['text'])
            saved=json.loads(db.execute('SELECT request FROM recognition_batches WHERE request_id=?',(rid,)).fetchone()[0])
            self.assertEqual(saved['records'][0]['record']['references'][0]['read_state'],'partial')
        self.assert_cached_reprocess(rid)

    def test_late_pdf_checkpoint_cannot_replace_completed_shared_cache(self):
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        rid=self.sync(pdf_bytes(('Synthetic page',)*13),'application/pdf')['requests'][0]['request_id']
        with app.connection() as db:
            request=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
            slow=teaching.attachment_context(db,self.uid,rid)
            fast=teaching.attachment_context(db,self.uid,rid)
        settings=Settings(model='synthetic-test',endpoint='https://api.qnaigc.com/v1')
        seen=[]
        def good(instructions,parts,settings):
            labels=[p['text'] for p in parts if p['type']=='text'];seen.extend(labels)
            return json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False,note='') for i in range(len(labels))]))
        def delayed(instructions,parts,settings):
            if not parts[0]['text'].startswith('第 1 页（'): raise AgentError('agent_timeout',True)
            # Interleave two already loaded contexts deterministically: the faster
            # reader commits all thirteen pages before this reader saves its first six.
            teaching_assets.enrich(deepcopy(request),fast['files'],SimpleNamespace(complete_json=good),settings,fast['save_cache'])
            return good(instructions,parts,settings)
        with self.assertRaises(AgentError):
            teaching_assets.enrich(deepcopy(request),slow['files'],SimpleNamespace(complete_json=delayed),settings,slow['save_cache'])
        with app.connection() as db:
            cached=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(cached.get('read_state'),'complete')
            self.assertIn('第 13 页',cached['text'])
        self.assert_cached_reprocess(rid)

    def test_cache_progress_writes_are_monotonic_and_version_scoped(self):
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        rid=self.sync(pdf_bytes(('Synthetic page',)*5),'application/pdf')['requests'][0]['request_id']
        with app.connection() as db:
            old=teaching.attachment_context(db,self.uid,rid)
            new=teaching.attachment_context(db,self.uid,rid)
        def checkpoint(n):
            return dict(pdf_checkpoint=1,total_pages=5,pages=[dict(text=f'Page {i}',uncertain=False,note='') for i in range(n)])
        save=old['save_cache'];asset=old['files'][0]
        save(asset,'version-a',checkpoint(4))
        save(asset,'version-a',checkpoint(2))
        with app.connection() as db:
            cached=json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])
            self.assertEqual(len(cached['pages']),4)
            fresh=teaching.attachment_context(db,self.uid,rid)
        save(asset,'version-a',dict(read_state='partial',text='Only two pages',pdf_pages_read=2))
        with app.connection() as db:
            self.assertEqual(len(json.loads(db.execute('SELECT result FROM teaching_asset_files').fetchone()[0])['pages']),4)
        fresh['save_cache'](fresh['files'][0],'version-b',checkpoint(2))
        # A still running old-version reader cannot overwrite a newer cache.
        save(asset,'version-a',dict(read_state='complete',text='Old complete'))
        new['save_cache'](new['files'][0],'version-a',checkpoint(5))
        with app.connection() as db:
            row=db.execute('SELECT cache_version,result FROM teaching_asset_files').fetchone()
            self.assertEqual(row['cache_version'],'version-b')
            self.assertEqual(len(json.loads(row['result'])['pages']),2)

    def assert_cached_reprocess(self,rid):
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


class TeachingAssetsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.settings=Settings(model='synthetic',endpoint='https://model.example.invalid/v1')

    def pdf_input(self):
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        request,_=example();data=pdf_bytes(('Synthetic',)*13)
        request['records'][0]['record']['references']=[dict(ref_id='file:1',kind='attachment',
            label='synthetic.pdf',read_state='partial',text='Captured text with a visual gap.')]
        asset=dict(ref_id='file:1',data=data,mime_type='application/pdf',sha256=hashlib.sha256(data).hexdigest(),
            result=None,cache_version=None)
        return request,asset

    def test_truncated_pdf_output_shrinks_batch_without_repeating_completed_pages(self):
        from unittest.mock import Mock
        import ddl_text.teaching_attachment_reader as reader
        request,asset=self.pdf_input();calls=[]
        def render(data,asset,limits):
            calls.append((limits.start_page,limits.max_pages))
            return dict(pages=[b'png']*min(limits.max_pages,13-limits.start_page),total_pages=13,error=None)
        def transcribe(client,instructions,parts,settings):
            if len(calls)>1 and len(parts)>2:raise AgentError('invalid_agent_output',True,'output_truncated')
            return json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False) for i in range(len(parts)//2)]))
        cache=Mock()
        with patch.object(reader,'read_pdf',side_effect=render),patch.object(reader,'transcribe',side_effect=transcribe):
            teaching_assets.enrich(request,[asset],Mock(),self.settings,cache)
        self.assertEqual(calls[:4],[(0,6),(6,6),(6,3),(6,1)])
        self.assertEqual([start for start,size in calls[3:]],list(range(6,13)))
        ref=request['records'][0]['record']['references'][0]
        self.assertEqual(ref['read_state'],'complete');self.assertIn('第 13 页',ref['text'])

    def test_single_page_truncation_and_provider_timeout_are_not_retried_forever(self):
        from unittest.mock import Mock
        import ddl_text.teaching_attachment_reader as reader
        for failure,expected in [(AgentError('invalid_agent_output',True,'output_truncated'),3),
                                 (AgentError('agent_timeout',True),3)]:
            with self.subTest(code=failure.code):
                request,asset=self.pdf_input()
                def render(data,asset,limits):
                    return dict(pages=[b'png']*limits.max_pages,total_pages=13,error=None)
                with patch.object(reader,'read_pdf',side_effect=render), \
                     patch.object(reader,'transcribe',side_effect=failure) as model:
                    with self.assertRaises(AgentError):teaching_assets.enrich(request,[asset],Mock(),self.settings,Mock())
                self.assertEqual(model.call_count,expected)

    def test_nonadvancing_checkpoint_stops_instead_of_looping(self):
        from unittest.mock import Mock
        request,asset=self.pdf_input()
        with patch.object(teaching_assets,'read_attachment',return_value=dict(pdf_checkpoint=1,total_pages=13,pages=[])) as reader:
            teaching_assets.enrich(request,[asset],Mock(),self.settings,Mock())
        reader.assert_called_once()
        self.assertNotEqual(request['records'][0]['record']['references'][0]['read_state'],'complete')

    def test_complete_pdf_still_requires_matching_captured_bytes(self):
        from unittest.mock import Mock
        request,asset=self.pdf_input()
        request['records'][0]['record']['references'][0]['read_state']='complete'
        asset['data']+=b'changed'
        with self.assertRaises(AgentError):
            teaching_assets.enrich(request,[asset],Mock(),self.settings,Mock())

    def test_multiple_attachments_each_get_a_fresh_read_budget(self):
        from unittest.mock import Mock
        request,asset=self.pdf_input();assets=[asset,dict(asset,ref_id='file:2',data=asset['data']+b'\n')]
        assets[1]['sha256']=hashlib.sha256(assets[1]['data']).hexdigest()
        ref=deepcopy(request['records'][0]['record']['references'][0]);ref['ref_id']='file:2'
        request['records'][0]['record']['references'].append(ref)
        clock=[100.0]
        def read(*args,expires,**kwargs):
            self.assertEqual(expires-clock[0],75)
            clock[0]+=70
            return dict(read_state='complete',text=TEXT)
        with patch.object(teaching_assets,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
             patch.object(teaching_assets,'read_attachment',side_effect=read) as reader:
            teaching_assets.enrich(request,assets,Mock(),self.settings,Mock())
        self.assertEqual(reader.call_count,2)
        self.assertTrue(all(r['read_state']=='complete' for r in request['records'][0]['record']['references']))

    def test_pdf_verified_pages_replace_unchecked_capture_and_cache_is_reused(self):
        from unittest.mock import Mock
        from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes
        data=pdf_bytes(('Page with graphics',)*6+('Deadline 2026-11-17 23:30',))
        request,_=example();record=request['records'][0]['record']
        original='\n'.join(['PDF text page '+str(n) for n in range(1,7)]+['Deadline 2026-11-17 23:30'])
        record['references']=[dict(ref_id='file:1',kind='attachment',label='seven-pages.pdf',read_state='partial',text=original)]
        asset=dict(ref_id='file:1',data=data,mime_type='application/pdf',sha256=hashlib.sha256(data).hexdigest(),result=None,cache_version=None)
        seen=[]
        def transcribe(client,instructions,parts,settings):
            count=len(parts)//2;start=len(seen)
            seen.extend(range(start+1,start+count+1))
            return json.dumps(dict(pages=[dict(index=n+1,text='Visible page '+str(start+n+1),uncertain=False,note='') for n in range(count)]))
        cache=Mock()
        with patch('ddl_text.teaching_attachment_reader.transcribe',side_effect=transcribe):
            teaching_assets.enrich(request,[asset],Mock(),self.settings,cache)
        ref=record['references'][0]
        self.assertEqual(ref['read_state'],'complete');self.assertNotIn('PDF text page',ref['text'])
        self.assertIn('Visible page 7',ref['text']);self.assertEqual(seen,list(range(1,8)))
        teaching_assets.check_request(request)
        # Repeat with the persisted transcript cache and the original capture.
        asset.update(cache_version=cache.call_args.args[1],result=json.dumps(cache.call_args.args[2]))
        ref.update(text=original,read_state='partial')
        with patch.object(teaching_assets,'read_attachment',side_effect=AssertionError('cached file')):
            teaching_assets.enrich(request,[asset],Mock(),self.settings,cache)
        self.assertNotIn('PDF text page',ref['text']);self.assertIn('Visible page 6',ref['text'])

    def test_pdf_verified_partial_does_not_restore_unchecked_text(self):
        from unittest.mock import Mock
        request,_=example();record=request['records'][0]['record']
        record['text']='Source body '+ 'X'*20000
        original='A'*49000+' Deadline 2026-11-17 23:30'
        record['references']=[dict(ref_id='file:1',kind='attachment',label='large.pdf',read_state='partial',text=original)]
        data=b'synthetic';asset=dict(ref_id='file:1',data=data,mime_type='application/pdf',sha256=hashlib.sha256(data).hexdigest(),result=None,cache_version=None)
        teaching_assets.check_request(request)
        with patch.object(teaching_assets,'read_attachment',return_value=dict(read_state='partial',text='B'*40000)):
            teaching_assets.enrich(request,[asset],Mock(),self.settings,Mock())
        self.assertEqual(record['references'][0]['text'],'B'*40000)
        self.assertEqual(record['references'][0]['read_state'],'partial')
        teaching_assets.check_request(request)

    def test_shared_capture_file_counted_and_read_once_with_each_binding_checked(self):
        import tempfile
        from unittest.mock import Mock
        data=image_bytes()+b'\0'*(5*1024*1024-len(image_bytes()))
        digest=hashlib.sha256(data).hexdigest()
        request,_=example();base=request['records'][0]
        entries=[];batches=[]
        for i in range(21):
            row=deepcopy(base);row['record']['input_id']='input:'+str(i)
            row['record']['references']=[dict(ref_id='file:1',kind='attachment',label='shared.png',read_state='unread',text=None)]
            batches.append(dict(request,records=[row]))
            entries.append(dict(input_id=row['record']['input_id'],ref_id='file:1',sha256=digest,mime_type='image/png',size_bytes=len(data)))
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp).resolve();path=folder/(digest+'.bin');path.write_bytes(data)
            manifest=folder/'manifest.json';manifest.write_text(json.dumps(entries),encoding='utf-8')
            open_file=Path.open;reads=[]
            def opened(p,*args,**kwargs):
                if p==path:reads.append(p)
                return open_file(p,*args,**kwargs)
            with patch.object(Path,'open',opened):assets=teaching_assets.read_capture(folder,batches)
            self.assertEqual(len(assets),21);self.assertEqual(len(reads),1)
            self.assertTrue(all(a[0]['data'] is assets['input:0'][0]['data'] for a in assets.values()))
            entries[-1]['size_bytes']-=1
            manifest.write_text(json.dumps(entries),encoding='utf-8')
            with self.assertRaises(ValueError):teaching_assets.read_capture(folder,batches)

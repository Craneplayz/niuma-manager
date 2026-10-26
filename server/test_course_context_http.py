"""Authenticated HTTP and SQLite wiring with a synthetic model boundary.

Exercises real retrieval, reader orchestration, compiler, result validation,
evidence API, auto-save, source revisions and manual decision protection.
Does not measure live-model semantic accuracy.
"""
import hashlib
import json
import unittest
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch

import app
import reprocessing
import teaching
import test_teaching as harness
from server.test_course_context import request,draft
from ddl_text.teaching_draft import compile_teaching_response,TeachingPreview
from ddl_text import teaching_pipeline
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.tests.teaching_cases import empty_reply
from server import course_context,teaching_assets


class CourseContextHTTPTests(unittest.TestCase):
    setUp=harness.TeachingIntegrationTests.setUp
    call=harness.TeachingIntegrationTests.call

    def tearDown(self):
        try:self.assertTrue(reprocessing.wait_for_jobs(app.DB))
        finally:harness.TeachingIntegrationTests.tearDown(self)

    def sync(self,requests,assets=None):
        with app.connection() as db:
            return teaching.register(db,self.uid,'synthetic-course-user',requests,assets=assets)

    def run_record(self,rid,expected=200,classifier=None):
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
        with patch.object(teaching,'live_options',return_value=options),patch.object(teaching_pipeline,'process_teaching_draft',side_effect=classifier or classify):
            status,batches=self.call('/recognition/batches',dict(contract_version='teaching-run/0.1',request_id=rid))
        self.assertEqual(status,expected,batches)
        if expected!=200:return batches
        return next(b for b in batches if b['request']['request_id']==rid)

    def file_source(self,name,*,course='synthetic-course'):
        value=request(name,'练习内容见附件。',course=course)
        value['records'][0]['record']['references']=[dict(ref_id='file:1',kind='attachment',label='01.pdf',read_state='unread',text=None)]
        data=('synthetic-pdf-'+name).encode()
        return value,dict(ref_id='file:1',data=data,sha256=hashlib.sha256(data).hexdigest(),mime_type='application/pdf')

    @contextmanager
    def reader(self,**kwargs):
        # The entry point and course helper currently import both module paths.
        with patch.object(teaching_assets,'read_attachment',**kwargs),patch.object(teaching.teaching_assets,'read_attachment',**kwargs):
            yield

    def file_classifier(self,record,*,missing='incomplete',**kwargs):
        ref=next((r for r in record['references'] if r['kind']=='attachment' and r['read_state']=='complete'),None)
        if ref:
            task=draft(record)
            task.update(title='提交附件练习',time_role='deadline',submission_relation='assignment_submission')
            task['assessment']['action']['evidence']=[dict(part_id=ref['ref_id'],quote=ref['text'])]
            compiled=compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)
        else:
            compiled=compile_teaching_response(empty_reply(record,missing),record,source_verified=True)
        empty=compiled['empty_decision']
        return TeachingPreview(tuple(compiled['decisions']),bool(empty and empty['status']=='no_tasks'),(empty['reason'],) if empty else (),empty)

    def test_cross_source_completion_recovers_original_and_does_not_repeat(self):
        for missing in ('incomplete','no_tasks'):
            with self.subTest(missing=missing):
                primary,asset=self.file_source('a-'+missing,course=missing)
                primary['records'][0]['record']['body_status']='partial' if missing=='incomplete' else 'complete'
                other=request('b-'+missing,'请完成课堂练习。',course=missing)
                raw=[primary,other];assets={primary['records'][0]['record']['input_id']:[asset]}
                views=self.sync(raw,assets)['requests'];rid=views[0]['request_id'];other_rid=views[1]['request_id']
                classify=lambda record,**kwargs:self.file_classifier(record,missing=missing,**kwargs)
                with self.reader(side_effect=AgentError('file_read_failed',False)):
                    first=self.run_record(rid,classifier=classify)
                self.assertEqual(first['result']['status'],'failed' if missing=='incomplete' else 'no_tasks',first)
                if missing=='incomplete':self.assertEqual(first['result']['errors'][0]['code'],'incomplete_source')
                self.assertFalse(first['items'])
                complete=dict(read_state='complete',text='请完成并提交第四章书面练习。')
                with self.reader(return_value=complete):
                    self.run_record(other_rid)
                with app.connection() as db:
                    self.assertEqual(json.loads(db.execute('SELECT result FROM teaching_asset_files WHERE sha256=?',(asset['sha256'],)).fetchone()[0])['read_state'],'complete')
                next_views=self.sync(raw,assets)['requests']
                matching=[v['request_id'] for v in next_views if v['records'][0]['input_id']==primary['records'][0]['record']['input_id']]
                self.assertEqual(len(matching),1,next_views)
                again=matching[0]
                self.assertNotEqual(again,rid)
                with self.reader(side_effect=AssertionError('complete cache must be reused')):
                    recovered=self.run_record(again,classifier=classify)
                self.assertEqual(len(recovered['items']),1,recovered)
                item=recovered['items'][0]
                status,workspace=self.call('/workspace')
                self.assertEqual(status,200,workspace)
                version=workspace.get('tasks',{}).get('version',0)
                status,saved=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',reviewed=True,version=item['version'],tasks_version=version,
                    draft=dict(title=item['candidate']['title'],description=item['candidate']['description'],due=None)))
                self.assertEqual(status,200,saved)
                for _ in range(2):
                    self.assertNotIn(primary['records'][0]['record']['input_id'],[v['records'][0]['input_id'] for v in self.sync(raw,assets)['requests']])
                self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1 if missing=='incomplete' else 2)

    def test_cold_cache_reordering_saves_first_result_without_invalidating_again(self):
        primary=request('homework','请完成第四章书面作业。')
        a,a_file=self.file_source('a-material');z,z_file=self.file_source('z-material')
        rid=self.sync([primary,a,z],{'a-material':[a_file],'z-material':[z_file]})['requests'][0]['request_id']
        acct=teaching.account('synthetic-course-user')
        def selection():
            with app.connection() as db:
                current=teaching.resolve_snapshot(db,self.uid,rid,app.ApiError)
                return course_context.select(db,self.uid,acct,current['records'][0])
        before=selection()
        self.assertEqual([s['request']['records'][0]['record']['input_id'] for s in before],['a-material','z-material'])
        def read(data,*args,**kwargs):
            return dict(read_state='complete',text='教学大纲：第四章书面作业于2026年12月20日截止。' if data==z_file['data'] else '课堂参考资料。')
        with self.reader(side_effect=read):
            batch=self.run_record(rid)
        after=selection()
        self.assertEqual([s['request']['records'][0]['record']['input_id'] for s in after],['z-material','a-material'])
        self.assertEqual(course_context.fingerprint(before),course_context.fingerprint(after))
        self.assertEqual(len(batch['items']),1)
        item=batch['items'][0]
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        self.assertNotIn('homework',[v['records'][0]['input_id'] for v in self.sync([z,a,primary],{'a-material':[a_file],'z-material':[z_file]})['requests']])

    def test_concurrent_cache_completion_is_not_acknowledged_by_failed_primary_read(self):
        primary,asset=self.file_source('only');primary['records'][0]['record']['body_status']='partial'
        rid=self.sync([primary],{'only':[asset]})['requests'][0]['request_id']
        def classify(record,**kwargs):
            # A different reader commits after our primary read failed, before
            # the model/save boundary. Its text was never part of this request.
            with app.connection() as db:
                stored=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid)[0]
                teaching_assets.save_read_cache(db,self.uid,stored,teaching_assets.cache_version(Settings(model='synthetic',endpoint='https://example.test')),
                    dict(read_state='complete',text='请完成并提交第四章练习。'))
            return self.file_classifier(record,**kwargs)
        with self.reader(side_effect=AgentError('file_read_failed',False)):
            batch=self.run_record(rid,classifier=classify)
        self.assertEqual(batch['result']['status'],'failed')
        self.assertEqual(batch['result']['errors'][0]['code'],'incomplete_source')
        next_views=self.sync([primary],{'only':[asset]})['requests']
        self.assertEqual(len(next_views),1)
        self.assertNotEqual(next_views[0]['request_id'],rid)

    def test_top_k_cache_membership_change_saves_and_refreshes_once(self):
        for replay in (False,True):
            with self.subTest(reprocessing=replay):
                course='rank-replay' if replay else 'rank-normal'
                iid=course+'-task';primary=request(iid,'请完成书面作业。',course=course)
                pairs=[self.file_source(course+'-'+name,course=course) for name in 'abcdefz']
                raw=[primary]+[p[0] for p in pairs]
                assets={p['records'][0]['record']['input_id']:[a] for p,a in pairs}
                rid=self.sync(raw,assets)['requests'][0]['request_id']
                acct=teaching.account('synthetic-course-user')
                with app.connection() as db:
                    before=course_context.select(db,self.uid,acct,primary['records'][0])
                    token=[tuple(row) for row in db.execute('SELECT input_id,digest FROM teaching_sources WHERE user_id=? AND account=? ORDER BY input_id',(self.uid,acct))]
                    zrid=db.execute('SELECT request_id FROM teaching_sources WHERE user_id=? AND input_id=?',
                                   (self.uid,course+'-z')).fetchone()[0]
                self.assertEqual(len(before),6)
                self.assertNotIn(course+'-z',[s['request']['records'][0]['record']['input_id'] for s in before])
                def classify(record,**kwargs):
                    with app.connection() as db:
                        asset=teaching_assets.load(db,self.uid,acct,zrid)[0]
                        teaching_assets.save_read_cache(db,self.uid,asset,teaching_assets.cache_version(Settings(model='synthetic',endpoint='https://example.test')),
                            dict(read_state='complete',text='教学大纲：所有书面作业无需额外登记。'))
                        self.assertEqual(token,[tuple(row) for row in db.execute('SELECT input_id,digest FROM teaching_sources WHERE user_id=? AND account=? ORDER BY input_id',(self.uid,acct))])
                        self.assertNotEqual(course_context.fingerprint(before),course_context.fingerprint(course_context.select(db,self.uid,acct,primary['records'][0])))
                    compiled=compile_teaching_response(empty_reply(record,'no_tasks'),record,source_verified=True)
                    return TeachingPreview((),True,(),compiled['empty_decision'])
                with self.reader(return_value=dict(read_state='complete',text='课堂资料内容。')):
                    if replay:
                        source=self.call('/recognition/reprocessing/sources')[1]['accounts'][0]
                        options=dict(provider='qiniu',key='synthetic-key',model='synthetic',endpoint='https://example.test')
                        with patch.object(teaching,'live_options',return_value=options),patch.object(teaching_pipeline,'process_teaching_draft',side_effect=classify):
                            status,run=self.call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',operation_id=course,
                                source_account_id=source['source_account_id'],input_ids=[iid]))
                            self.assertEqual(status,202,run)
                            self.assertTrue(reprocessing.wait_for_jobs(app.DB))
                        saved=self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]
                        self.assertEqual(saved['state'],'succeeded',saved)
                    else:
                        self.assertEqual(self.run_record(rid,classifier=classify)['result']['status'],'no_tasks')
                    with app.connection() as db:
                        saved_rid=db.execute('SELECT request_id FROM teaching_sources WHERE user_id=? AND input_id=?',(self.uid,iid)).fetchone()[0]
                    views=self.sync(raw,assets)['requests']
                    next_rid=next(v['request_id'] for v in views if v['records'][0]['input_id']==iid)
                    self.assertNotEqual(next_rid,saved_rid)
                    self.run_record(next_rid,classifier=lambda record,**kw:TeachingPreview((),True,(),
                        compile_teaching_response(empty_reply(record,'no_tasks'),record,source_verified=True)['empty_decision']))
                    for _ in range(2):
                        self.assertNotIn(iid,[v['records'][0]['input_id'] for v in self.sync(raw,assets)['requests']])

    def test_optional_title_failure_survives_http_validation_and_autosave(self):
        primary=request('group-notice','先添加老师微信，老师加你进课程微信群。')
        rid=self.sync([primary])['requests'][0]['request_id']
        def classify(record,**kwargs):
            task=draft(record)
            task.update(title='加入课程微信群',time_role='none',submission_relation='other',
                title_facts=dict(kind='other',channel='',sequence='',subject='加课程微信群',
                    evidence=[dict(part_id='body',quote=record['text'])]))
            compiled=compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)
            return TeachingPreview(tuple(compiled['decisions']),False,())
        batch=self.run_record(rid,classifier=classify)
        self.assertEqual(len(batch['items']),1)
        item=batch['items'][0]
        self.assertEqual(item['candidate']['title'],'加入课程微信群')
        self.assertEqual(self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        self.assertEqual(len(self.call('/workspace')[1]['tasks']['value']),1)

    def test_existing_unrepresentable_cache_is_skipped_without_endless_retries(self):
        primary,asset=self.file_source('oversized-cache')
        rid=self.sync([primary],{'oversized-cache':[asset]})['requests'][0]['request_id']
        with app.connection() as db:
            stored=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid)[0]
            teaching_assets.save_read_cache(db,self.uid,stored,teaching_assets.cache_version(Settings(model='synthetic',endpoint='https://example.test')),
                dict(read_state='complete',text='x'*100001))
        with self.reader(side_effect=AssertionError('known cache must not be reread')):
            batch=self.run_record(rid,classifier=lambda record,**kwargs:self.file_classifier(record,missing='no_tasks',**kwargs))
        self.assertEqual(batch['result']['status'],'no_tasks',batch)
        for _ in range(2):self.assertEqual(self.sync([primary],{'oversized-cache':[asset]})['requests'],[])

    def test_reprocessing_preserves_late_cache_completion_for_next_sync(self):
        for related in (False,True):
            with self.subTest(related=related):
                iid='replay-'+str(related)
                primary,asset=self.file_source(iid,course=iid)
                raw=[primary]+([request('policy-'+iid,'教学大纲：作业要求见附件。',course=iid)] if related else [])
                rid=self.sync(raw,{iid:[asset]})['requests'][0]['request_id']
                def classify(record,**kwargs):
                    with app.connection() as db:
                        stored=teaching_assets.load(db,self.uid,teaching.account('synthetic-course-user'),rid)[0]
                        teaching_assets.save_read_cache(db,self.uid,stored,teaching_assets.cache_version(Settings(model='synthetic',endpoint='https://example.test')),
                            dict(read_state='complete',text='请完成并提交第四章练习。'))
                    return self.file_classifier(record,missing='no_tasks',**kwargs)
                source=self.call('/recognition/reprocessing/sources')[1]['accounts'][0]
                options=dict(provider='qiniu',key='synthetic-key',model='synthetic',endpoint='https://example.test')
                with self.reader(side_effect=AgentError('file_read_failed',False)),patch.object(teaching,'live_options',return_value=options),patch.object(teaching_pipeline,'process_teaching_draft',side_effect=classify):
                    status,run=self.call('/recognition/reprocessing/runs',dict(contract_version='reprocessing/0.1',operation_id=iid,
                        source_account_id=source['source_account_id'],input_ids=[iid]))
                    self.assertEqual(status,202,run)
                    self.assertTrue(reprocessing.wait_for_jobs(app.DB))
                saved=self.call('/recognition/reprocessing/runs/'+run['run_id'])[1]
                self.assertEqual(saved['state'],'succeeded',saved)
                with app.connection() as db:
                    replay_rid=db.execute('SELECT request_id FROM teaching_sources WHERE user_id=? AND input_id=?',(self.uid,iid)).fetchone()[0]
                self.assertNotEqual(replay_rid,rid)
                next_views=self.sync(raw,{iid:[asset]})['requests']
                matching=[v['request_id'] for v in next_views if v['records'][0]['input_id']==iid]
                self.assertEqual(len(matching),1,next_views)
                self.assertNotEqual(matching[0],replay_rid)
                with self.reader(side_effect=AssertionError('complete cache must be reused')):
                    recovered=self.run_record(matching[0],classifier=self.file_classifier)
                self.assertEqual(len(recovered['items']),1,recovered)
                # A saved auto-applicable candidate may be returned for writeback;
                # that must reuse its batch, not schedule another recognition.
                repeat=self.sync(raw,{iid:[asset]})['requests']
                self.assertTrue(all(v['request_id']==matching[0] for v in repeat if v['records'][0]['input_id']==iid))
                with app.connection() as db:
                    self.assertEqual(db.execute('SELECT request_id FROM teaching_sources WHERE user_id=? AND input_id=?',(self.uid,iid)).fetchone()[0],matching[0])
                again=self.run_record(matching[0],classifier=lambda *a,**k:self.fail('unchanged batch must reuse saved result'))
                self.assertEqual([item['id'] for item in again['items']],[item['id'] for item in recovered['items']])

    def test_actual_context_addition_and_removal_still_reject_stale_results(self):
        for change in ('add','remove'):
            with self.subTest(change=change):
                primary=request('task-'+change,'请完成第四章作业。',course=change)
                material=request('syllabus-'+change,'教学大纲：第四章作业于12月20日截止。',course=change)
                rid=self.sync([primary,material])['requests'][0]['request_id']
                original=teaching.validate_current_result
                def change_before_save(*args):
                    if change=='add':
                        self.sync([request('new-policy','教学大纲补充：第四章要求。',course=change)])
                    else:
                        with app.connection() as db:
                            db.execute('DELETE FROM teaching_sources WHERE user_id=? AND input_id=?',(self.uid,'syllabus-'+change))
                    return original(*args)
                with patch.object(teaching,'validate_current_result',side_effect=change_before_save):
                    self.run_record(rid,expected=409)
                with app.connection() as db:
                    self.assertIsNone(db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?',(self.uid,rid)).fetchone())

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

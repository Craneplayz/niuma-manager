"""Real SQLite + source contracts; no browser, private data or model requests."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.teaching_io import check_request
from contracts.course_context import validate_context
from ddl_text.config import Settings
from ddl_text.tests.teaching_cases import policy_case,model_task
from ddl_text.teaching_draft import compile_teaching_response
from server import course_context as context, teaching_assets


def request(name,text,*,course='synthetic-course',term='2026-autumn'):
    record=policy_case('explicit-complete')['pair']['record']
    record.update(input_id=name,source_item_id=name,snapshot_id='snapshot:'+name,
        course_id=course,text=text,references=[],source_metadata_text='学期：'+term,
        course_term=dict(term_id=term,starts_on='2026-09-01',ends_on='2027-02-28',applies_to='current_assignment',
                         evidence=[dict(part_id='source_metadata',quote='学期：'+term)]))
    return dict(contract_version='0.4',request_id='request:'+name,
                records=[dict(record=record,source=dict(label=name,url=None,course_or_project='合成课程'))])


def draft(record):
    task=model_task(policy_case('explicit-complete')['pair'])
    task['assessment'].update(action=dict(state='supported',evidence=[dict(part_id='body',quote=record['text'])]),
        deadline=dict(value=None,precision='unknown',derivation='not_provided',anchor=None,evidence=[]),unread_relations=[])
    return task


class CourseContextTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE teaching_sources(user_id TEXT,account TEXT,input_id TEXT,
            digest TEXT,request_id TEXT,request TEXT,PRIMARY KEY(user_id,account,input_id));
            CREATE TABLE teaching_submission_requests(user_id TEXT,account TEXT,request_id TEXT,input_id TEXT,
            PRIMARY KEY(user_id,account,request_id));''')
        teaching_assets.initialize(self.db);context.initialize(self.db)

    def save(self,value,uid='u',account='a'):
        entry=value['records'][0]; r=entry['record']
        digest=hashlib.sha256(json.dumps(entry,sort_keys=True).encode()).hexdigest()
        self.db.execute('INSERT OR REPLACE INTO teaching_sources VALUES(?,?,?,?,?,?)',
            (uid,account,r['input_id'],digest,value['request_id'],json.dumps(value)))
        context.index(self.db,uid,account,r)

    def test_shared_course_examples_cover_contract_boundaries(self):
        examples=json.loads((Path(__file__).resolve().parents[1]/'contracts/examples/course-context-v01.json').read_text(encoding='utf-8'))
        self.assertEqual(examples['contract_version'],'course-context/0.1')
        for case in examples['cases']:
            with self.subTest(case=case['name']):check_request(case['request'])
        expected={c['name']:c['expected'] for c in examples['cases']}
        self.assertEqual(expected['published-not-due']['dates'],[None])
        self.assertEqual(expected['three-channels']['count'],3)
        self.assertEqual(expected['two-files-exception']['dates'],['2026-11-13'])

    def test_all_scope_dimensions_and_semester_window_are_required(self):
        target=request('homework','第四章书面作业，期限按教学大纲。')
        good=request('syllabus','教学大纲：书面作业每周提交。');self.save(good)
        self.save(request('other-owner','教学大纲：错误日期。'),uid='v')
        self.save(request('other-account','教学大纲：错误日期。'),account='b')
        self.save(request('other-course','教学大纲：错误日期。',course='other'))
        self.save(request('other-semester','教学大纲：错误日期。',term='2025-autumn'))
        inconsistent=request('bad-window','教学大纲：错误日期。')
        inconsistent['records'][0]['record']['course_term']['starts_on']='2026-10-01';self.save(inconsistent)
        self.assertEqual([r['request']['request_id'] for r in context.select(self.db,'u','a',target['records'][0])],['request:syllabus'])
        target['records'][0]['record']['course_term']=None
        self.assertEqual(context.select(self.db,'u','a',target['records'][0]),[])

    def test_context_quotes_have_original_offsets_and_forgery_is_rejected(self):
        target=request('assignment','请完成第四章书面作业。')
        material=request('calendar','无关段落。'*1500+'教学大纲：第四章书面作业于2026年12月20日提交。')
        context.attach(target,[material]);check_request(target)
        record=target['records'][0]['record'];doc=record['course_context']['documents'][0]
        ref=next(r for r in record['references'] if r['ref_id']==doc['ref_id'])
        original=material['records'][0]['record']['text']
        self.assertEqual(ref['text'],original[doc['offset']:doc['offset']+doc['length']])
        self.assertEqual(ref['read_state'],'partial');self.assertLessEqual(len(ref['text']),4000)
        for field,value in [('course_id','other'),('term_id','other'),('length',1),('source_url','file:///secret')]:
            bad=deepcopy(record);bad['course_context']['documents'][0][field]=value
            with self.subTest(field=field),self.assertRaises(Exception):validate_context(bad)
        bad=deepcopy(record);bad.pop('course_context')
        with self.assertRaises(ValueError):validate_context(bad)
        bad=deepcopy(record);bad['references'][-1]['text']+='改'
        with self.assertRaises(ValueError):validate_context(bad)

    def test_budget_is_shared_with_primary_source_and_never_discards_it(self):
        target=request('assignment','请完成作业。'+'x'*99000)
        before=target['records'][0]['record']['text']
        materials=[request('syllabus'+str(i),'教学大纲：'+str(i)+'x'*9000) for i in range(20)]
        context.attach(target,materials);check_request(target)
        r=target['records'][0]['record']
        self.assertEqual(r['text'],before)
        self.assertGreater(r['course_context']['omitted_documents'],0)
        self.assertLessEqual(len(r['references']),12)

    def test_first_import_reads_related_original_and_bad_attachment_does_not_fail_primary(self):
        target=request('assignment','第四章作业，截止时间见教学大纲。')
        material=request('syllabus','教学大纲见附件。')
        material['records'][0]['record']['references']=[dict(ref_id='file:1',kind='attachment',label='rules.pdf',read_state='complete',text='不应信任的原始PDF文本')]
        selected=[dict(request=material,digest='x')]
        seen=[]
        def enrich(current,files,client,settings,save_cache,**kwargs):
            seen.append((files,deepcopy(current)))
            self.assertIsNone(current['records'][0]['record']['references'][0]['text'])
            current['records'][0]['record']['references'][0].update(read_state='complete',text='书面作业于2026年12月20日提交。')
        with patch.object(teaching_assets,'enrich',side_effect=enrich):
            context.prepare(target,selected,lambda _:['synthetic-original'],None,Settings(),lambda *a:None,lambda:None)
        self.assertEqual(seen[0][0],['synthetic-original'])
        self.assertTrue(any('12月20日' in r['text'] for r in target['records'][0]['record']['references']))
        check_request(target)
        # Expired context budget never accepts a raw PDF transcript.
        with patch.object(context,'READ_BUDGET_SECONDS',0):
            context.prepare(target,selected,lambda _:self.fail('must not load bytes'),None,Settings(model='synthetic',endpoint='https://example.test'),lambda *a:None,lambda:None)
        self.assertFalse(any('不应信任' in r['text'] for r in target['records'][0]['record']['references']))
        self.assertGreater(target['records'][0]['record']['course_context']['omitted_documents'],0)

    def test_new_material_changes_only_dependent_revision_and_does_not_loop(self):
        target=request('assignment','第四章书面作业。');self.save(target)
        context.remember(self.db,'u','a',target['records'][0],[])
        self.save(request('unrelated','欢迎大家。'))
        self.assertEqual(context.refresh_dependents(self.db,'u','a'),[])
        material=request('syllabus','教学大纲：书面作业统一12月20日提交。');self.save(material)
        changed=context.refresh_dependents(self.db,'u','a')
        self.assertEqual(len(changed),1)
        self.assertEqual(changed[0][0],target['request_id'])
        self.assertNotEqual(changed[0][1]['request_id'],target['request_id'])
        self.assertEqual(context.refresh_dependents(self.db,'u','a'),[])
        chosen=context.select(self.db,'u','a',target['records'][0])
        self.assertTrue(context.current(self.db,'u','a',chosen))
        material['records'][0]['record']['text']+='更正：改为12月21日。';self.save(material)
        self.assertFalse(context.current(self.db,'u','a',chosen))
        self.assertEqual(len(context.refresh_dependents(self.db,'u','a')),1)

    def test_completed_attachment_cache_survives_exhausted_context_budget(self):
        target=request('assignment','第四章作业，截止时间见教学大纲。')
        material=request('syllabus','教学大纲见附件。')
        material['records'][0]['record']['references']=[dict(ref_id='file:1',kind='attachment',label='rules.pdf',read_state='unread',text=None)]
        self.save(material)
        settings=Settings(model='synthetic',endpoint='https://example.test')
        version=teaching_assets.cache_version(settings)
        data=b'synthetic-original'; sha=hashlib.sha256(data).hexdigest()
        teaching_assets.store(self.db,'u','a',material['request_id'],[dict(ref_id='file:1',sha256=sha,mime_type='application/pdf',data=data)])
        result=dict(read_state='complete',text='第四章书面作业于2026年12月20日截止。')
        self.db.execute('UPDATE teaching_asset_files SET cache_version=?,result=?',(version,json.dumps(result)))
        selected=context.select(self.db,'u','a',target['records'][0])
        with patch.object(context,'READ_BUDGET_SECONDS',0):
            context.prepare(target,selected,lambda _:self.fail('cache must not load blobs'),None,settings,lambda *a:None,lambda:None,
                load_cached=lambda item,v:teaching_assets.load_cached(self.db,'u','a',item['request']['request_id'],v))
        self.assertIn(result['text'],[r['text'] for r in target['records'][0]['record']['references']])
        check_request(target)

    def test_unchanged_sync_reuses_course_catalog_and_cache_queries(self):
        sources=[request('policy-'+str(i),f'教学大纲：第四章书面作业要求{i}。') for i in range(30)]
        for value in sources:self.save(value)
        for value in sources:context.remember(self.db,'u','a',value['records'][0],context.select(self.db,'u','a',value['records'][0]))
        statements=[];self.db.set_trace_callback(statements.append)
        self.assertEqual(context.refresh_dependents(self.db,'u','a'),[])
        self.db.set_trace_callback(None)
        queries=[s for s in statements if s.lstrip().upper().startswith('SELECT')]
        self.assertLessEqual(len(queries),len(sources)+3)

    def test_attachment_text_is_searchable_even_with_an_ordinary_filename(self):
        target=request('assignment','请完成第四章书面作业，提交时间按课程规定。')
        material=request('ordinary','资料见附件。')
        material['records'][0]['record']['references']=[dict(ref_id='file:1',kind='attachment',label='01.pdf',read_state='unread',text=None)]
        self.save(material)
        for i in range(7):self.save(request('policy-'+str(i),'教学大纲：请认真学习。'))
        self.assertNotIn('request:ordinary',[s['request']['request_id'] for s in context.select(self.db,'u','a',target['records'][0])])
        data=b'synthetic';sha=hashlib.sha256(data).hexdigest()
        teaching_assets.store(self.db,'u','a',material['request_id'],[dict(ref_id='file:1',sha256=sha,mime_type='application/pdf',data=data)])
        result=dict(read_state='complete',text='课程规定：所有第四章书面作业统一于2026年12月20日提交。')
        self.db.execute('UPDATE teaching_asset_files SET cache_version=?,result=?',('synthetic-cache',json.dumps(result)))
        chosen=context.select(self.db,'u','a',target['records'][0])
        self.assertEqual(chosen[0]['request']['request_id'],'request:ordinary')

    def test_long_material_keeps_separated_rule_and_exception_spans(self):
        target=request('assignment','第11讲书面作业按课程大纲及更正安排提交。')
        first='课程大纲：所有书面作业统一在2026年11月6日提交。'
        last='课程安排更正：第11讲书面作业改为2026年11月13日提交。'
        text=first+'无关背景。'*2500+last
        material=request('syllabus',text)
        context.attach(target,[material]);check_request(target)
        record=target['records'][0]['record'];refs=record['references']
        self.assertTrue(any(first in r['text'] for r in refs))
        self.assertTrue(any(last in r['text'] for r in refs))
        for doc,ref in zip(record['course_context']['documents'],refs):
            self.assertEqual(ref['text'],text[doc['offset']:doc['offset']+doc['length']])

    def test_context_cannot_generate_duplicate_unrelated_items(self):
        target=request('assignment','请完成第四章作业。');context.attach(target,[request('syllabus','期末考试2027年1月12日进行。')])
        r=target['records'][0]['record'];task=draft(r)
        q=dict(part_id=r['references'][0]['ref_id'],quote=r['references'][0]['text'])
        task['assessment']['action']['evidence']=[q]
        with self.assertRaises(Exception):compile_teaching_response(json.dumps(dict(tasks=[task])),r,source_verified=True)
        task['assessment']['action']['evidence']=[dict(part_id='body',quote=r['text'])]
        # A different, applicable policy provides this assignment's future date.
        context.attach(target,[request('syllabus','第四章作业于2027年1月12日截止。')])
        q=dict(part_id=r['references'][0]['ref_id'],quote=r['references'][0]['text'])
        task['assessment']['deadline'].update(value='2027-01-12',precision='date',derivation='absolute',evidence=[q])
        result=compile_teaching_response(json.dumps(dict(tasks=[task])),r,source_verified=True)
        self.assertEqual(result['decisions'][0]['pair']['assessment']['deadline']['value'],'2027-01-12')

    def test_consistent_titles_keep_channels_and_series_distinct(self):
        titles=[]
        for channel in ('书面','POJ','MOOC'):
            for chapter in ('第四章','第五章'):
                r=request('a',f'请完成{channel}{chapter}作业。')['records'][0]['record'];task=draft(r)
                task['title_facts']=dict(kind='assignment',channel=channel,sequence=chapter,subject='',evidence=task['assessment']['action']['evidence'])
                result=compile_teaching_response(json.dumps(dict(tasks=[task])),r,source_verified=True)
                titles.append(result['decisions'][0]['title'])
        self.assertEqual(titles,['书面作业 · 第4章','书面作业 · 第5章','POJ作业 · 第4章','POJ作业 · 第5章','MOOC作业 · 第4章','MOOC作业 · 第5章'])
        task['title_facts']['sequence']='第九十九章'
        with self.assertRaises(Exception):compile_teaching_response(json.dumps(dict(tasks=[task])),r,source_verified=True)

    def test_teaching_week_calculation_and_missing_anchor(self):
        target=request('exam','考试安排在第16周周五。')
        context.attach(target,[request('calendar','教学第一周周一为2026年9月7日。')])
        r=target['records'][0]['record'];task=draft(r)
        q=dict(part_id=r['references'][0]['ref_id'],quote=r['references'][0]['text'])
        task.update(content_type='notice',time_role='event',calendar_rule=dict(week_one_monday='2026-09-07',week=16,weekday=5,offset_days=0,evidence=[q,*task['assessment']['action']['evidence']]))
        decision=compile_teaching_response(json.dumps(dict(tasks=[task])),r,source_verified=True)['decisions'][0]
        self.assertEqual(decision['pair']['assessment']['deadline']['value'],'2026-12-25')
        self.assertEqual(decision['recommendation'],'candidate')
        task['calendar_rule']['week_one_monday']='2026-09-01'
        decision=compile_teaching_response(json.dumps(dict(tasks=[task])),r,source_verified=True)['decisions'][0]
        self.assertIsNone(decision['pair']['assessment']['deadline']['value'])


if __name__=='__main__':unittest.main()

"""Deterministic display tests; these do not measure model extraction accuracy."""
import json
import unittest
from copy import deepcopy

from ddl_text.adapters import AgentError
from ddl_text.course_facts import sequence_display, title
from ddl_text.teaching_draft import compile_teaching_response, evidence_catalog
from ddl_text import diagnostics
from ddl_text.tests.teaching_cases import model_task, policy_case


def sample(sequence='第四章',channel='书面',kind='assignment',subject='条件分支'):
    pair=policy_case('explicit-complete')['pair']
    record=pair['record']
    record.update(text=f'请完成{channel}{sequence}作业，主题为{subject}。',references=[])
    evidence=[dict(part_id='body',quote=record['text'])]
    task=model_task(pair)
    task['assessment'].update(action=dict(state='supported',evidence=evidence),
        deadline=dict(value=None,precision='unknown',derivation='not_provided',anchor=None,evidence=[]),
        unread_relations=[])
    task['title_facts']=dict(kind=kind,channel=channel,sequence=sequence,subject=subject,evidence=deepcopy(evidence))
    return record,task


class CourseTitleStyleTests(unittest.TestCase):
    def test_equivalent_explicit_batches_have_one_display(self):
        cases={
            '第4章':('第四章','第4章','第 ４ 章','Chapter4','CHAPTER 04','Chapter #4'),
            '第4讲':('第四讲','第4讲','Lecture 4'),
            '第4周':('第四周','第4周','week 4'),
            '第4次':('第四次','第4次','Assignment 4','Lab 4'),
            '第0次':('第零次','第〇次','Lab 0'),
            '第2次':('第两次','第2次'),
            '第10章':('第十章','第一十章','Chapter 10'),
            '第21周':('第二十一周','Week 21'),
            '第100章':('第一百章','Chapter 100'),
            '第103章':('第一百零三章','Chapter 103'),
            '第120章':('第一百二十章','Chapter 120'),
            '第231章':('第二百三十一章','第两百三十一章','Chapter 231'),
        }
        for expected,values in cases.items():
            for value in values:
                with self.subTest(value=value):self.assertEqual(sequence_display(value),expected)

    def test_uncertain_sequences_dates_and_problem_numbers_are_unchanged(self):
        for value in ('','4','2026','2026-10-23','2026年','第2026周','第二〇二六周',
                      '第4.2章','Chapter 4.2','Problem 4','4.2','HW4','Lab -1','Assignment 4a',
                      '第4-5讲','第4、5章','第4章第2题','Chapter 4 homework','Lab 2 [2026.09.29]',
                      '第一百二章','第十十章','第一二章','第一千章','Chapter 1000','第百章',
                      '第四课','  不确定批次  '):
            with self.subTest(value=value):self.assertEqual(sequence_display(value),value)

    def test_compile_preserves_raw_facts_evidence_and_source(self):
        record,task=sample('Chapter 4')
        original_record,original_task=deepcopy(record),deepcopy(task)
        decision=compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)['decisions'][0]
        self.assertEqual(decision['title'],'书面作业 · 第4章')
        self.assertEqual(decision['title_facts'],original_task['title_facts'])
        self.assertEqual(decision['pair']['record'],original_record)
        self.assertEqual(record,original_record)
        self.assertEqual(task,original_task)
        self.assertIn('条件分支',decision['description'])

    def test_channel_and_subject_are_not_number_normalized(self):
        outputs=[]
        for channel in ('书面','POJ','MOOC'):
            _,task=sample('第四章',channel=channel,kind='reading',subject='Problem 4.2 / 2026年')
            outputs.append(title(task))
        self.assertEqual(outputs,['书面阅读 · 第4章 · Problem 4.2 / 2026年',
                                  'POJ阅读 · 第4章 · Problem 4.2 / 2026年',
                                  'MOOC阅读 · 第4章 · Problem 4.2 / 2026年'])

    def test_same_display_does_not_merge_separate_decisions(self):
        record,task=sample('第四次',channel='',kind='lab',subject='实验报告')
        other=deepcopy(task)
        other['title_facts']['sequence']='Lab 4'
        record['text']+=' 另有 Lab 4 实验报告。'
        other['title_facts']['evidence']=[dict(part_id='body',quote=record['text'])]
        decisions=compile_teaching_response(json.dumps(dict(tasks=[task,other])),record,source_verified=True)['decisions']
        self.assertEqual([d['title'] for d in decisions],['实验 · 第4次','实验 · 第4次'])
        self.assertNotEqual(decisions[0]['pair']['assessment']['candidate_id'],decisions[1]['pair']['assessment']['candidate_id'])
        self.assertEqual([d['title_facts']['sequence'] for d in decisions],['第四次','Lab 4'])

    def test_normalization_cannot_replace_literal_source_evidence(self):
        record,task=sample('第四章')
        task['title_facts']['sequence']='Chapter 4'
        decision=compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)['decisions'][0]
        self.assertEqual(decision['title'],task['title'])
        self.assertIsNone(decision['title_facts'])

    def test_optional_title_faults_preserve_valid_tasks_and_report_fallback(self):
        for text,title_text in [('先添加老师微信，老师加你进课程微信群。','加入课程微信群'),
                                ('请完成第三讲作业题。','第三讲作业')]:
            for broken in ('paraphrase','quote','shape','kind','span'):
                with self.subTest(text=text,broken=broken):
                    record,task=sample()
                    record['text']=text
                    evidence=[dict(part_id='body',quote=text)]
                    task.update(title=title_text,description=title_text)
                    task['assessment']['action']['evidence']=deepcopy(evidence)
                    task['title_facts'].update(channel='',sequence='',subject='不在原文中的转述',evidence=deepcopy(evidence))
                    if broken=='quote':task['title_facts']['evidence'][0]['quote']='伪造引用'
                    if broken=='shape':task['title_facts']=['invalid']
                    if broken=='kind':task['title_facts']['kind']='unsupported'
                    if broken=='span':task['title_facts']['evidence']=[dict(span_id='missing')]
                    events=[]
                    with diagnostics.scope(lambda stage,**fields:events.append(dict(stage=stage,**fields))):
                        result=compile_teaching_response(json.dumps(dict(tasks=[task,dict(task,title_facts=None)])),record,source_verified=True)
                    self.assertEqual(len(result['decisions']),2)
                    decision=result['decisions'][0]
                    self.assertEqual((decision['title'],decision['description']),(title_text,title_text))
                    self.assertIsNone(decision['title_facts'])
                    self.assertTrue(any(e.get('code')=='title_facts' and e.get('status')=='fallback' for e in events),events)

    def test_indexed_optional_facts_degrade_but_core_evidence_stays_strict(self):
        record,task=sample()
        span=next(k for k,v in evidence_catalog(record).items() if v['part_id']=='body')
        task.update(content_type='task',time_role='none',submission_relation='assignment_submission')
        task['assessment']['action']['evidence']=[dict(span_id=span)]
        task['relevance']['evidence']=[]
        task['title_facts']['evidence']=[dict(span_id='missing')]
        result=compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True,indexed=True)
        self.assertIsNone(result['decisions'][0]['title_facts'])
        for field in ('action','deadline'):
            invalid=deepcopy(task)
            invalid['assessment'][field]['evidence']=[dict(span_id='missing')]
            with self.subTest(field=field),self.assertRaises(AgentError):
                compile_teaching_response(json.dumps(dict(tasks=[invalid])),record,source_verified=True,indexed=True)

    def test_historical_draft_without_facts_keeps_its_title(self):
        self.assertEqual(title(dict(title='完成 Chapter 4 的第4.2题')), '完成 Chapter 4 的第4.2题')

    def test_title_fallback_never_relaxes_action_date_calendar_or_source(self):
        for fault in ('action','deadline','calendar','source'):
            record,task=sample()
            task['title_facts']['subject']='未引用的主题'
            if fault in ('action','deadline'):
                task['assessment'][fault]['evidence']=[dict(part_id='body',quote='伪造证据')]
            elif fault=='calendar':
                task['calendar_rule']=dict(week_one_monday='2026-09-07',week=4,weekday=1,offset_days=0,
                    evidence=[dict(part_id='body',quote='伪造基准日期')])
            else:record['course_id']=[]
            with self.subTest(fault=fault),self.assertRaises(AgentError):
                compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)


if __name__=='__main__':unittest.main()

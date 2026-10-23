"""Deterministic display tests; these do not measure model extraction accuracy."""
import json
import unittest
from copy import deepcopy

from ddl_text.adapters import AgentError
from ddl_text.course_facts import sequence_display, title
from ddl_text.teaching_draft import compile_teaching_response
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
        with self.assertRaises(AgentError) as failure:
            compile_teaching_response(json.dumps(dict(tasks=[task])),record,source_verified=True)
        self.assertEqual(failure.exception.code,'invalid_agent_output')

    def test_historical_draft_without_facts_keeps_its_title(self):
        self.assertEqual(title(dict(title='完成 Chapter 4 的第4.2题')), '完成 Chapter 4 的第4.2题')


if __name__=='__main__':unittest.main()

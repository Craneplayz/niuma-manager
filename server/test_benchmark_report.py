import json
import tempfile
import unittest
from pathlib import Path
from server.model_evaluation.report import evaluate,publish
from server.model_evaluation.course_context_suite import build,score


class ReportTests(unittest.TestCase):
    def test_context_keeps_wrong_missing_and_safe_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            cases=build(Path(folder))
        self.assertEqual(len(cases),8)
        self.assertTrue(all(case['request']['records'][0]['record']['course_context']['documents'] for case in cases))
        positive=cases[0]['expected']
        negative=cases[3]['expected']
        deadline={'value':positive['dates'][0],'evidence':[{'part_id':'context:fixture'}]}
        decision={'recommendation':'task','pair':{'assessment':{'deadline':deadline}}}
        result={'status':'success','record_results':[{'decisions':[decision]}]}
        self.assertEqual(score(positive,result)['correct'],1)
        self.assertEqual(score(negative,result)['wrong'],1)
        deadline['value']=None
        self.assertEqual(score(positive,result)['missed'],1)
        result['record_results'][0]['decisions']=[]
        self.assertEqual(score(negative,result)['safe'],1)
        self.assertEqual(score(negative,{'status':'failed'})['failed'],1)

    def real(self,expected):return dict(input_contract='0.4',module='recognition',expected=expected)

    def test_echoed_input_date_is_not_a_correct_extracted_date(self):
        case=self.real(dict(deadline='2026-11-20T23:59:00+08:00'))
        out=dict(record_results=[dict(decisions=[dict(pair=dict(record=dict(text='2026-11-20T23:59:00+08:00'),assessment=dict(deadline=dict(value=None))))])])
        checks,*_=evaluate(case,dict(status='ok',output=out))
        self.assertFalse(checks['deadline'])

    def test_real_date_and_role_must_be_structured(self):
        case=self.real(dict(event='2026-11-02T15:10:00+08:00'))
        decision=dict(time_role='deadline',pair=dict(assessment=dict(deadline=dict(value=case['expected']['event']))))
        out=dict(record_results=[dict(decisions=[decision])])
        self.assertFalse(evaluate(case,dict(status='ok',output=out))[0]['event_time'])
        decision['time_role']='event'
        self.assertTrue(evaluate(case,dict(status='ok',output=out))[0]['event_time'])

    def test_unread_attachment_never_becomes_confirmed_negative(self):
        checks,*_=evaluate(self.real(dict(incomplete=True)),dict(status='ok',output=dict(record_results=[dict(decisions=[])])))
        self.assertIsNone(checks['semantic_truth'])
        self.assertNotIn('negative_correct',checks)

    def test_multi_task_effort_constraints_keep_all_ids(self):
        case=dict(module='estimation',expected=dict(estimates={'a':30,'b':20}),request=dict(tasks=[dict(id='a'),dict(id='b')]))
        checks,*_=evaluate(case,dict(status='ok',output=dict(estimates={'a':30})))
        self.assertFalse(checks['positive_complete']);self.assertFalse(checks['explicit_a'])

    def test_append_only_same_cohort_and_keep_real_case_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            case=dict(self.real({'no_action':True}),id='original',origin='real_original',shareable=False,request={'source':'private-original'})
            profiles=[dict(id=name,family=name,modalities=['text']) for name in ('first','second')]
            manifest=dict(cases=[case],dataset_hash='same',source_version='same',profiles=profiles)
            folders=[]
            for profile in profiles:
                name=profile['id'];folder=root/name;folder.mkdir();folders.append(folder)
                (folder/'benchmark-manifest.json').write_text(json.dumps(dict(manifest,run_id=name,admitted_families=[name])))
                receipt=dict(id='original',run_id=name,model=name,status='ok',output={'record_results':[{'decisions':[],'private':'raw-private-answer'}]})
                (folder/'receipt.json').write_text(json.dumps(receipt))
            report=publish(folders[0],root/'report.json',phase='formal',additional=folders[1:])
            self.assertEqual(report['runIds'],['first','second'])
            self.assertEqual(report['summary']['coverage']['executed'],2)
            self.assertTrue(all(row['admitted'] for row in report['rows']))
            self.assertNotIn('private-original',json.dumps(report));self.assertNotIn('raw-private-answer',json.dumps(report))
            (folders[1]/'benchmark-manifest.json').write_text(json.dumps(dict(manifest,dataset_hash='other',run_id='second',admitted_families=['second'])))
            with self.assertRaisesRegex(ValueError,'dataset_hash'):
                publish(folders[0],root/'report.json',phase='formal',additional=folders[1:])

if __name__=='__main__':unittest.main()

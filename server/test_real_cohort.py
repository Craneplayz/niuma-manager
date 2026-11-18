import unittest
from server.model_evaluation.real_cohort import original_case,modified_case


class CohortTests(unittest.TestCase):
    def original(self):
        return original_case(dict(id='R1',entry=dict(record=dict(course_id='c',source_item_id='s',text='11月20日交报告。',references=[])),expected=dict(deadline='2026-11-20T23:59:00+08:00')))

    def test_original_is_kept_exact_and_private(self):
        case=self.original()
        self.assertEqual(case['request']['records'][0]['record']['text'],'11月20日交报告。')
        self.assertEqual(case['provenance']['modifications'],[])
        self.assertFalse(case['shareable'])

    def test_modified_deadline_keeps_family_and_explicit_oracle(self):
        old=self.original();new=modified_case(old,'R1-date',[('11月20日','11月22日')],dict(deadline='2026-11-22T23:59:00+08:00'),'验证DDL变化是否传递到输出')
        self.assertEqual(old['request']['records'][0]['record']['text'],'11月20日交报告。')
        self.assertEqual(new['family_id'],old['family_id'])
        self.assertEqual(new['label_status'],'requires_review')
        self.assertEqual(new['expected']['deadline'],'2026-11-22T23:59:00+08:00')

    def test_noop_or_unmatched_edits_do_not_inflate_case_count(self):
        for replacements in [[],[('报告','报告')],[('不存在','另一个')]]:
            with self.subTest(replacements=replacements),self.assertRaises(ValueError):
                modified_case(self.original(),'other',replacements,{},'测试')

if __name__=='__main__':unittest.main()

import unittest
from server.model_evaluation.metrics import aggregate,distribution,wilson


class MetricsTests(unittest.TestCase):
    def test_precision_recall_f1_are_object_metrics(self):
        report=aggregate([dict(family_id='a',executed=True,status='ok',tp=3,fp=1,fn=2)])
        self.assertEqual(report['detection']['precision'],.75)
        self.assertEqual(report['detection']['recall'],.6)
        self.assertAlmostEqual(report['detection']['f1'],2/3)

    def test_missing_and_not_applicable_do_not_become_passes(self):
        rows=[dict(family_id='a',executed=True,status='error',checks=dict(contract=False)),
              dict(family_id='b',executed=False,checks=dict(contract=None)),
              dict(family_id='c',applicable=False,checks=dict(contract=None))]
        result=aggregate(rows)
        self.assertEqual(result['coverage']['pending'],1)
        self.assertEqual(result['checks']['contract']['value'],0)
        self.assertEqual(result['checks']['contract']['denominator'],1)

    def test_repeats_do_not_inflate_independent_sample_size(self):
        rows=[dict(family_id='same',executed=True,status='ok',checks=dict(date=i!=2)) for i in range(3)]
        check=aggregate(rows)['checks']['date']
        self.assertEqual(check['denominator'],3)
        self.assertEqual(check['independentFamilies'],1)
        self.assertEqual(check['familyPassRate'],0)

    def test_unknown_cost_is_not_zero_cost(self):
        result=aggregate([dict(family_id='a',executed=True,status='ok')])
        self.assertEqual(result['cost']['unknownCases'],1)
        self.assertIsNone(result['cost']['estimatePerSuccessfulCase'])
        self.assertEqual(result['tokens']['inputMissing'],1)

    def test_latency_keeps_failure_time_and_percentile_definition(self):
        rows=[dict(family_id=str(i),executed=True,status='error' if i==2 else 'ok',seconds=n)for i,n in enumerate([1,2,30])]
        result=aggregate(rows)
        self.assertEqual(result['latencySeconds']['p50'],2)
        self.assertAlmostEqual(result['latencySeconds']['p95'],27.2)
        self.assertEqual(result['successfulLatencySeconds']['maximum'],2)
        self.assertIsNone(distribution([])['p95'])

    def test_wilson_and_empty_denominators(self):
        self.assertIsNone(wilson(0,0))
        low,high=wilson(10,10)
        self.assertGreater(low,.7);self.assertAlmostEqual(high,1)
        self.assertIsNone(aggregate([])['detection']['f1'])

if __name__=='__main__':unittest.main()

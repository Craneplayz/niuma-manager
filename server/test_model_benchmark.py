"""Offline regression for paid-request admission, interrupted calls and suite coverage."""
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from server.model_evaluation.budget import Budget,BudgetExceeded
from server.model_evaluation.benchmark_client import BenchmarkClient
from server.model_evaluation.build_suite import build
from server.model_evaluation.benchmark import admit
from ddl_text.config import Settings


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.budget=Budget(self.root/'budget.sqlite3',1)

    def test_concurrent_reservations_cannot_exceed_limit(self):
        def reserve(i):
            try:self.budget.reserve(str(i),0.2);return True
            except BudgetExceeded:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8)as pool:
            self.assertEqual(sum(pool.map(reserve,range(20))),5)
        self.assertEqual(self.budget.summary()['committedYuan'],1)

    def test_unknown_usage_survives_restart_and_new_output_directory(self):
        self.budget.reserve('pending',0.9)
        restarted=Budget(self.root/'budget.sqlite3',1)
        with self.assertRaises(BudgetExceeded):restarted.reserve('another',0.2)
        self.assertEqual(restarted.summary()['unknownRequests'],1)
        with self.assertRaises(ValueError):Budget(self.root/'budget.sqlite3',100)

    def test_actual_usage_releases_only_unused_reserve(self):
        self.budget.reserve('a',0.9);self.budget.settle('a',0.1)
        self.budget.reserve('b',0.9)
        self.assertEqual(self.budget.summary()['committedYuan'],1)

    def test_overrun_is_recorded_not_hidden(self):
        self.budget.reserve('a',0.1);self.budget.settle('a',1.1)
        self.assertTrue(self.budget.summary()['boundExceeded'])
        self.assertEqual(self.budget.summary()['committedYuan'],1.1)

    def test_invalid_budget_rejected(self):
        for amount in [0,-1,float('nan'),float('inf')]:
            with self.subTest(amount=amount),self.assertRaises(ValueError):Budget(self.root/'invalid.sqlite3',amount)

    def test_dry_admission_does_not_create_ledger(self):
        missing=self.root/'not-created'/'budget.sqlite3'
        self.assertEqual(Budget.read_summary(missing,100)['committedYuan'],0)
        self.assertFalse(missing.parent.exists())

    def test_resume_keeps_frozen_models_after_spending(self):
        profiles=[dict(family='a'),dict(family='b')]
        estimates={'a':10,'b':30}
        self.assertEqual(admit(profiles,estimates,20),profiles[:1])
        self.assertEqual(admit(profiles,estimates,1,existing=['a','b']),profiles)
        self.assertEqual(admit(profiles,estimates,1,existing=['b','a']),profiles[::-1])

    def test_readonly_budget_matches_live_ledger(self):
        self.budget.reserve('a',.4);self.budget.settle('a',.1)
        self.assertEqual(Budget.read_summary(self.budget.path,1),self.budget.summary())

    def client(self):
        return BenchmarkClient('synthetic-key',self.root/'trace',dict(family='Test',id='test/model',endpoint='https://example.test/v1',
            max_output_tokens=64,input_yuan_per_token_max=0.000001,output_yuan_per_token_max=0.000002,thinking='disabled'),self.budget)

    def test_real_transport_records_usage_cost_and_does_not_expose_key(self):
        envelope=dict(id='synthetic-request',usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15),
                      choices=[dict(finish_reason='stop',message=dict(content='{}'))])
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(envelope).encode()
        opener=MagicMock();opener.open.return_value=response
        with patch('server.model_evaluation.benchmark_client.build_opener',return_value=opener):
            result=self.client().complete_json('instructions','{}',Settings(mode='real'))
        self.assertEqual(result,'{}')
        event=json.loads((self.root/'trace.calls.jsonl').read_text())
        self.assertEqual(event['total_tokens'],15)
        self.assertAlmostEqual(event['estimated_yuan'],0.00002)
        self.assertNotIn('synthetic-key',json.dumps(event))
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['max_tokens'],64)

    def test_broken_response_keeps_budget_reservation(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'not-json'
        opener=MagicMock();opener.open.return_value=response
        with patch('server.model_evaluation.benchmark_client.build_opener',return_value=opener):
            with self.assertRaises(Exception):self.client().complete_json('instructions','{}',Settings(mode='real'))
        self.assertEqual(self.budget.summary()['unknownRequests'],1)
        self.assertGreater(self.budget.summary()['committedYuan'],0)

    def test_stratified_suite_is_frozen_and_records_correlated_pairs(self):
        cases=build(self.root/'suite')
        self.assertGreaterEqual(len(cases),100)
        self.assertEqual(len({c['id']for c in cases}),len(cases))
        self.assertGreaterEqual(len({c['scenario']for c in cases}),20)
        pairs=[c for c in cases if c.get('pair_with')]
        self.assertEqual(len(pairs),12)
        byid={c['id']:c for c in cases}
        for case in pairs:
            self.assertEqual(case['family_id'],byid[case['pair_with']]['family_id'])
            self.assertEqual(case['expected'],byid[case['pair_with']]['expected'])
        self.assertTrue(json.loads((self.root/'suite/dataset.json').read_text())['frozen_before_execution'])

if __name__=='__main__':unittest.main()

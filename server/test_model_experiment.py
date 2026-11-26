"""Isolated HTTP and SQLite acceptance for online evaluation."""
import os
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, build_opener
from unittest import TestCase
from unittest.mock import patch

import app
from server import model_experiment as experiment, operations
from test_usage import UsageTests
del UsageTests


class ModelExperimentTests(TestCase):
    def setUp(self):
        from test_usage import UsageTests
        UsageTests.setUp(self)
        self.addCleanup(UsageTests.tearDown, self)
        config=patch.dict(os.environ, NIUMA_EXPERIMENT_ID='test-models', NIUMA_EXPERIMENT_SALT='x'*40,
                          NIUMA_EXPERIMENT_B_MODEL='google/gemini-3.1-flash-lite',
                          NIUMA_EXPERIMENT_B_ENDPOINT='https://api.modelink.ai/v1', NIUMA_EXPERIMENT_ACCOUNTS=self.uid)
        config.start()
        self.addCleanup(config.stop)

    def call(self, *args, **kwargs):
        from test_usage import UsageTests
        return UsageTests.call(self,*args,**kwargs)

    def result(self, module, status='success', observed=True):
        base=dict(provider='qiniu',key='synthetic-not-stored',model='deepseek/deepseek-v4.1-flash',endpoint='https://api.qnaigc.com/v1')
        def evaluate():
            selected=experiment.options(base)
            expected=os.environ['NIUMA_EXPERIMENT_B_MODEL'] if experiment.assignment(self.uid)['arm']=='B' else base['model']
            self.assertEqual(selected['model'],expected)
            self.assertEqual(experiment.assignment(self.uid),experiment.assignment(self.uid))
            if observed:operations.emit('model_call')
            return {'status':status,'private':'not stored'}
        with app.connection() as db:
            return experiment.run(db,self.uid,module,evaluate)

    def test_result_feedback_prompt_and_access(self):
        self.assertTrue(self.call('/usage/visit',{})[1]['modelExperiment'])
        self.result('text')
        self.result('estimation',observed=False)
        self.result('text',status='failed')
        first=self.call('/usage/model-experiment')[1]
        self.assertEqual(len(first['results']),1)
        self.assertFalse(first['promptDue'])
        self.result('decomposition','ready_to_plan')
        second=self.call('/usage/model-experiment')[1]
        self.assertTrue(second['promptDue'])
        self.assertEqual([r['ordinal'] for r in second['results']],[2,1])
        receipt=self.call('/usage/model-experiment/prompt',{})[1]['result']['id']
        self.assertIsNone(self.call('/usage/model-experiment/prompt',{})[1]['result'])
        first_id=first['results'][0]['id']
        self.assertEqual(self.call('/usage/model-experiment',{'id':first_id,'score':5})[0],200)
        self.assertEqual(self.call('/usage/model-experiment',{'id':receipt,'score':2})[0],200)
        self.assertEqual(self.call('/usage/model-experiment',{'id':receipt,'score':6})[0],400)
        self.assertEqual(self.call('/usage/model-experiment',{'id':receipt,'score':True})[0],400)
        self.assertEqual(self.call('/usage/model-experiment',{'id':receipt,'score':3,'message':'禁止'})[0],400)
        self.assertEqual(self.call('/admin/model-experiment')[0],401)
        with patch.dict(os.environ,NIUMA_ADMIN_USER_IDS=self.uid):
            self.assertEqual(self.call('/admin/auth/login',{'email':'usage@example.test','password':'Usage-Test-2026!'})[0],200)
            admin=self.call('/admin/model-experiment')[1]['rows']
        self.assertEqual(sum(row['attempts'] for row in admin),3)
        self.assertEqual(sum(row['feedback'] for row in admin),2)
        self.assertEqual(sum(row['score5'] or 0 for row in admin),1)
        self.assertEqual(sum(row['score2'] or 0 for row in admin),1)
        self.assertNotIn('synthetic-not-stored',str(admin))
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call('/auth/register',{'name':'Other','email':'other@example.test','password':'Usage-Test-2026!'})
        self.assertEqual(self.call('/usage/model-experiment')[1]['results'],[])
        self.assertEqual(self.call('/usage/model-experiment',{'id':receipt,'score':5})[0],404)

    def test_alternative_is_fixed_and_unenrolled_uses_baseline(self):
        from unittest.mock import patch
        with patch.object(experiment,'actor',return_value='1'*64):
            self.result('teaching')
        self.assertIsNone(experiment.assignment('not-enrolled'))
        with patch.dict(os.environ,NIUMA_EXPERIMENT_B_ENDPOINT='https://example.test/v1'):
            self.assertIsNone(experiment.assignment(self.uid))

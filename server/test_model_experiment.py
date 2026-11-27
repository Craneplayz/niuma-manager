"""Isolated HTTP and SQLite acceptance for online evaluation."""
import io
import json
import os
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, build_opener
from unittest import TestCase
from unittest.mock import patch

import app
from server import model_experiment as experiment, operations


class ModelExperimentTests(TestCase):
    def setUp(self):
        from test_usage import UsageTests
        UsageTests.setUp(self)
        self.addCleanup(UsageTests.tearDown, self)
        config=patch.dict(os.environ, NIUMA_EXPERIMENT_ID='test-models', NIUMA_EXPERIMENT_SALT='x'*40,
                          NIUMA_EXPERIMENT_B_KEY='synthetic-alt', NIUMA_EXPERIMENT_B_MODEL='google/gemini-3.1-flash-lite',
                          NIUMA_EXPERIMENT_B_ENDPOINT='https://api.modelink.ai/v1', NIUMA_EXPERIMENT_ACCOUNTS=self.uid)
        config.start()
        self.addCleanup(config.stop)

    def call(self, *args, **kwargs):
        from test_usage import UsageTests
        return UsageTests.call(self,*args,**kwargs)

    def result(self, module, status='success', observed=True, tokens=()):
        base=dict(provider='qiniu',key='synthetic-not-stored',model='deepseek/deepseek-v4.1-flash',endpoint='https://api.qnaigc.com/v1')
        def evaluate():
            selected=experiment.options(base)
            expected=os.environ['NIUMA_EXPERIMENT_B_MODEL'] if experiment.assignment(self.uid)['arm']=='B' else base['model']
            self.assertEqual(selected['model'],expected)
            self.assertEqual(selected['key'],'synthetic-alt' if expected!=base['model'] else base['key'])
            self.assertEqual(experiment.assignment(self.uid),experiment.assignment(self.uid))
            if observed:operations.emit('model_call')
            for count in tokens:
                self.assertTrue(operations.producer(dict(contract_version='producer-observation/0.1',
                    stage='model_call',duration_ms=1,result_code='ok',input_tokens=None,
                    output_tokens=None,total_tokens=count)))
            return {'status':status,'private':'not stored'}
        with app.connection() as db:
            result, receipt = experiment.run(db,self.uid,module,evaluate)
            if status!='rejected':experiment.accept(db,receipt,status)
            db.commit()
            return result

    def test_result_feedback_prompt_and_access(self):
        self.assertTrue(self.call('/usage/visit',{})[1]['modelExperiment'])
        self.result('text',tokens=(10,))
        self.result('estimation',status='rejected')
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
        self.assertEqual(sum(row['attempts'] for row in admin),4)
        self.assertEqual(sum(row['tokens'] or 0 for row in admin),10)
        self.assertEqual(sum(row['missingCalls'] for row in admin),4)
        self.assertEqual(sum(row['feedback'] for row in admin),2)
        self.assertEqual(sum(row['score5'] or 0 for row in admin),1)
        self.assertEqual(sum(row['score2'] or 0 for row in admin),1)
        self.assertNotIn('synthetic-not-stored',str(admin))
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call('/auth/register',{'name':'Other','email':'other@example.test','password':'Usage-Test-2026!'})
        self.assertEqual(self.call('/usage/model-experiment')[1]['results'],[])
        self.assertEqual(self.call('/usage/model-experiment',{'id':receipt,'score':5})[0],404)

    def test_alternative_is_fixed_and_unenrolled_uses_baseline(self):
        with patch.object(experiment,'actor',return_value='1'*64):
            self.result('teaching')
        self.assertIsNone(experiment.assignment('not-enrolled'))
        with patch.dict(os.environ,NIUMA_EXPERIMENT_B_ENDPOINT='https://example.test/v1'):
            self.assertIsNone(experiment.assignment(self.uid))

    def test_legacy_exposures_keep_ratings_and_usage_on_upgrade(self):
        with operations.connection(app.DB) as db:
            experiment.initialize(db)
            db.execute('ALTER TABLE model_exposures DROP COLUMN known_calls')
            db.executemany('''INSERT INTO model_exposures VALUES
                (?, ?, ?, 'A', 'text', 'legacy', strftime('%s','now'), 25,'success',2,?,?,?,0)''',
                [('1'*32,'test-models',experiment.actor(self.uid,'test-models'),13,5,1),
                 ('2'*32,'test-models',experiment.actor(self.uid,'test-models'),None,2,2)])
        with patch.dict(os.environ,NIUMA_ADMIN_USER_IDS=self.uid):
            self.assertEqual(self.call('/admin/auth/login',{'email':'usage@example.test','password':'Usage-Test-2026!'})[0],200)
            row, = self.call('/admin/model-experiment')[1]['rows']
        self.assertEqual((row['tokens'],row['measuredCalls'],row['missingCalls'],row['feedback']),(13,2,2,2))
        self.result('text')
        self.assertEqual([r['score'] for r in self.call('/usage/model-experiment')[1]['results']],[None,2,5])

    def test_planning_commit_and_decomposition_capture_actual_transport_usage(self):
        from ddl_text import chat_client
        from test_task_decomposition_api import EXAMPLES

        task = dict(id='project',title='课程项目',description='',status='not_started',plans=[],subtasks=[],
                    due=dict(date=(app.datetime.now(app.TZ)+app.timedelta(days=2)).date().isoformat(),precision='date'))
        self.assertEqual(self.call('/data/tasks',{'value':[task],'version':0},'PUT')[0],200)
        draft = {k:EXAMPLES['clarification']['response'][k] for k in ('status','assessment','questions','plan')}
        def response(content):
            return io.BytesIO(json.dumps({'usage':{'total_tokens':10},'choices':[
                {'finish_reason':'stop','message':{'content':json.dumps(content)}}]}).encode())

        with patch.dict(os.environ,DDL_PROVIDER='qiniu',QINIU_API_KEY='synthetic-base',HARNESS_URL=''), \
                patch.object(chat_client,'build_opener') as transport:
            transport.return_value.open.side_effect=[
                response({'estimates':{'project':30}}), response({'estimates':{'project':30}}),
                response({'estimates':{'project':30}}), response(draft)]
            with patch.object(app,'validate_proposal',side_effect=ValueError):
                self.assertEqual(self.call('/planning/proposals',{})[0],502)
            with patch.object(app.timetable,'read',side_effect=[{'version':0,'value':[]},{'version':0,'value':[]},{'version':1,'value':[]}]):
                self.assertEqual(self.call('/planning/proposals',{})[0],409)
            self.assertEqual(self.call('/usage/model-experiment')[1]['results'],[])
            self.assertEqual(self.call('/planning/proposals',{})[0],200)
            self.assertEqual(self.call('/decomposition/proposals',{'taskId':'project'})[0],200)
        results = self.call('/usage/model-experiment')[1]['results']
        self.assertEqual([result['module'] for result in results],['decomposition','estimation'])
        with operations.connection(app.DB) as db:
            self.assertEqual([tuple(row) for row in db.execute(
                'SELECT status,tokens,known_calls,ordinal FROM model_exposures ORDER BY rowid')],
                [('rejected',10,1,None),('rejected',10,1,None),('success',10,1,1),('needs_clarification',10,1,2)])

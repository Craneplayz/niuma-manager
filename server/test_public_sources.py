import json
import time
import unittest
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, Request
from pathlib import Path
from unittest.mock import patch
import test_teaching
import public_sources
from contracts import public_import as contract


def capture():
    return json.loads((Path(__file__).parents[1]/'contracts/examples/public-import-v01.json').read_text(encoding='utf-8'))['capture']


class PublicSourcesAPI(test_teaching.TeachingIntegrationTests):
    def test_public_preview_import_is_explicit_idempotent_and_not_a_deadline(self):
        body=dict(contract_version=contract.VERSION,source_id='cs',operation_id='synthetic-public')
        with patch.object(public_sources,'collect',return_value=capture()) as collect:
            status,run=self.call('/sources/public/runs',body);self.assertEqual(status,202,run)
            for _ in range(100):
                runs=self.call('/sources/public/runs')[1]['runs']
                if runs[0]['state']!='running':break
                time.sleep(.01)
            contract.check('run',runs[0]);self.assertEqual(runs[0]['state'],'succeeded');self.assertEqual(collect.call_count,1)
            self.assertEqual(self.call('/sources/public/runs',body)[1]['run_id'],run['run_id'])
        self.assertFalse(self.call('/workspace')[1].get('tasks',{}).get('value'))
        route='/sources/public/runs/'+run['run_id']+'/import'
        choice=dict(contract_version=contract.VERSION,event_ids=['evt_synthetic'],tasks_version=0)
        status,out=self.call(route,choice);self.assertEqual(status,200,out);self.assertEqual(len(out['added']),1)
        task=self.call('/workspace')[1]['tasks']['value'][0]
        self.assertEqual(task['content_type'],'notice');self.assertIsNone(task['due']);self.assertIn('2099-10-20',task['description'])
        choice['tasks_version']=1;self.assertEqual(self.call(route,choice)[1]['already_imported'],1)
        self.assertEqual(self.call(route,dict(choice,event_ids=['forged']))[0],400)
        self.assertEqual(self.call('/sources/public/runs',dict(body,source_id='bio'))[0],409)
        # Deletion must not remove the persistent import marker.
        with self.client.open(Request(self.base+'/data/tasks',data=json.dumps(dict(value=[],version=1)).encode(),headers={'Content-Type':'application/json'},method='PUT')) as response:
            self.assertEqual(response.status,200)
        choice['tasks_version']=2
        self.assertEqual(self.call(route,choice)[1]['already_imported'],1)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'],[])
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.call('/auth/register',dict(name='Other',email='public-other@example.test',password='Synthetic-Test-2026!'))[0],200)
        self.assertEqual(self.call('/sources/public/runs')[1]['runs'],[])
        self.assertEqual(self.call(route,choice)[0],404)

    def test_public_invalid_arbitrary_urls_and_failed_collections_are_visible(self):
        self.assertEqual(self.call('/sources/public/runs',dict(contract_version=contract.VERSION,source_id='http://127.0.0.1',operation_id='bad'))[0],400)
        with patch.object(public_sources,'collect',side_effect=RuntimeError('synthetic')):
            self.assertEqual(self.call('/sources/public/runs',dict(contract_version=contract.VERSION,source_id='bio',operation_id='failure'))[0],202)
            for _ in range(100):
                row=self.call('/sources/public/runs')[1]['runs'][0]
                if row['state']!='running':break
                time.sleep(.01)
        self.assertEqual(row['state'],'failed');self.assertIsNone(row['result']);self.assertIn('未完成',row['error'])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PublicSourcesAPI(name) for name in loader.getTestCaseNames(PublicSourcesAPI) if name.startswith('test_public_'))

"""Real HTTP/SQLite submission controls; crawler and model use synthetic fixtures."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

import app
import teaching
import test_teaching
from urllib.request import Request
from urllib.error import HTTPError
from contracts import submission_operations as ops


def load_tests(loader, tests, pattern):
    # Reuse the HTTP harness without rerunning its unrelated inherited cases.
    import unittest
    return unittest.TestSuite(SubmissionRuntimeAPI(name) for name in loader.getTestCaseNames(SubmissionRuntimeAPI) if name.startswith('test_runtime_'))


class SubmissionRuntimeAPI(test_teaching.TeachingIntegrationTests):
    def call(self,path,body=None,method=None):
        request=Request(self.base+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'},method=method)
        try:
            with self.client.open(request,timeout=20) as response:return response.status,json.load(response)
        except HTTPError as response:return response.code,json.load(response)
    def details(self):
        account=self.call('/sources/credentials')[1]['source_account_id']
        code,out=self.call('/sources/submissions?source_account_id='+account)
        self.assertEqual(code,200,out);ops.check('directory',out)
        return self.call('/sources/submissions/'+out['records'][0]['record_id'])[1]

    def operation(self,detail,kind='associate_actions',**fields):
        obs=detail['observation']
        body=dict(contract_version=ops.VERSION,operation_id='op:'+uuid4().hex,record_id=detail['record_id'],
           expected_snapshot_id=obs['snapshot']['snapshot_id'],expected_control_revision=obs['control_revision'],kind=kind,**fields)
        return body

    def apply(self,body):
        return self.call('/sources/submissions/'+body['record_id']+'/operations',body)

    def link_all(self,detail):
        body=self.operation(detail,scope='submission_only',links=[dict(action_id=a['action_id'],relation='assignment_submission') for a in detail['observation']['actions']])
        status,out=self.apply(body);self.assertEqual(status,201,out);ops.check('receipt',out)
        return out,body

    def test_runtime_completed_task_releases_future_plans_and_manual_reopen_pauses(self):
        view=self.sync_native(self.native_capture('active'))['requests'][0]
        candidate=self.run_request(view)['items'][0]
        self.assertEqual(self.call('/recognition/candidates/'+candidate['id']+'/resolve',dict(action='create',auto_apply=True,version=1))[0],200)
        tasks=self.call('/workspace')[1]['tasks'];task=tasks['value'][0];now=datetime.now(timezone.utc)
        task.update(title='保留用户修改',plans=[dict(id='past',startAt=(now-timedelta(days=1)).isoformat()),dict(id='future',startAt=(now+timedelta(days=1)).isoformat())])
        self.assertEqual(self.call('/data/tasks',tasks,method='PUT')[0],200)
        receipt,body=self.link_all(self.details())
        self.assertEqual(self.apply(body),(200,receipt))
        changed=dict(body,scope='unknown');self.assertEqual(self.apply(changed)[0],409)
        self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))
        saved=self.call('/workspace')[1]['tasks'];task=saved['value'][0]
        self.assertEqual(task['title'],'保留用户修改');self.assertEqual(task['status'],'completed');self.assertEqual([p['id'] for p in task['plans']],['past'])
        task['status']='not_started';self.assertEqual(self.call('/data/tasks',saved,method='PUT')[0],200)
        self.sync_native(self.native_capture('submitted','2026-10-10T12:00:00+08:00'))
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'not_started')
        action=self.details()['observation']['actions'][0];self.assertTrue(action['source_completion_paused'])
        detail=self.details();status,out=self.apply(self.operation(detail,'resume_source_completion',action_ids=[action['action_id']]))
        self.assertEqual(status,201,out)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'not_started')
        self.sync_native(self.native_capture('submitted','2026-10-10T13:00:00+08:00'))
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'completed')

    def test_runtime_first_submitted_with_explicit_scope_skips_model_and_latch_is_resolvable(self):
        self.sync_native(self.native_capture('submitted'));self.link_all(self.details())
        with patch.object(teaching,'recognize',side_effect=AssertionError('no model')):
            self.assertEqual(self.sync_native(self.native_capture('submitted','2026-10-10T11:00:00+08:00'))['requests'],[])
        self.sync_native(self.native_capture('active','2026-10-10T12:00:00+08:00'))
        detail=self.details();self.assertTrue(detail['observation']['review_pending'])
        status,out=self.apply(self.operation(detail,'resolve_source_review',action_ids=[]));self.assertEqual(status,201,out)
        self.assertFalse(self.details()['observation']['review_pending'])
        self.assertTrue(self.sync_native(self.native_capture('active','2026-10-10T13:00:00+08:00'))['requests'])

    def test_runtime_only_mapped_candidate_withdraws_and_ignored_choices_survive(self):
        view=self.sync_native(self.mixed_capture('active'))['requests'][0];items=self.run_request(view)['items']
        detail=self.details();actions=detail['observation']['actions'];self.assertEqual(len(actions),2)
        body=self.operation(detail,scope='mixed',links=[dict(action_id=a['action_id'],relation='assignment_submission' if i==0 else 'other') for i,a in enumerate(actions)])
        self.assertEqual(self.apply(body)[0],201)
        self.sync_native(self.mixed_capture('submitted','2026-10-10T11:00:00+08:00'))
        with app.connection() as db:
            states=[r['state'] for r in db.execute('SELECT state FROM recognition_candidates WHERE user_id=?',(self.uid,))]
        self.assertEqual(sorted(states),['ignored','pending'])
        app.initialize(recover=False)
        with app.connection() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM recognition_candidates WHERE state='ignored' AND user_id=?",(self.uid,)).fetchone()[0],1)

    def test_runtime_stale_operation_credentials_and_changed_content_are_guarded(self):
        self.sync_native(self.native_capture('active'));detail=self.details();_,body=self.link_all(detail)
        other=dict(body,operation_id='another');self.assertEqual(self.apply(other)[0],409)
        capture=self.native_capture('active','2026-10-10T11:00:00+08:00');capture['request']['records'][0]['record']['text']+=' 增加独立阅读要求。'
        self.sync_native(capture);self.assertEqual(self.details()['observation']['scope'],'unknown')
        self.assertEqual(self.call('/sources/submissions/'+detail['record_id']+'/source')[0],200)
        self.call('/sources/credentials',dict(username='different-account',password='synthetic'))
        self.assertEqual(self.apply(body)[0],404)
        self.assertEqual(self.call('/sources/submissions/'+detail['record_id'])[0],404)

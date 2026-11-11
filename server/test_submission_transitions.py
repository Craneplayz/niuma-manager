"""HTTP regressions for explicit controls across candidate/task transitions."""
import json
import unittest

import app
import submission_runtime
from test_submission_runtime import SubmissionRuntimeAPI


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SubmissionTransitionAPI(name) for name in
        loader.getTestCaseNames(SubmissionTransitionAPI) if name.startswith('test_transition_'))


class SubmissionTransitionAPI(SubmissionRuntimeAPI):
    def submission_capture(self):
        capture=self.native_capture('active')
        record=capture['request']['records'][0]['record']
        draft=json.loads(self.drafts[record['input_id']])
        draft['tasks'][0]['submission_relation']='assignment_submission'
        self.drafts[record['input_id']]=json.dumps(draft)
        return capture

    def submit(self,capture):
        capture['assignment'].update(state='submitted',basis='review_submission_history_title')
        capture['request']['records'][0]['record']['collected_at']='2026-10-10T11:00:00+08:00'
        self.sync_native(capture)

    def create(self,item):
        saved=self.call('/workspace')[1].get('tasks',dict(version=0))
        body=dict(action='create',reviewed=True,version=item['version'],tasks_version=saved['version'],
            draft=dict(title=item['candidate']['title'],description='用户核对后保留。',due=None))
        status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',body)
        self.assertEqual(status,200,out)
        return self.call('/workspace')[1]['tasks']['value'][-1]

    def update(self,item,task):
        saved=self.call('/workspace')[1]['tasks']
        body=dict(action='update',reviewed=True,version=self.candidate_row(item)['version'],
            tasks_version=saved['version'],target_id=task['id'],
            draft=dict(title=task['title'],description='包含新增行动要求。',due=None))
        status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',body)
        self.assertEqual(status,200,out)
        return body

    def task_action(self,task,detail=None):
        detail=detail or self.details()
        return next(a for a in detail['observation']['actions']
            if a['action_id']==submission_runtime.identity('task',task['id']))

    def ignore_reopen(self,item,*,intermediate_refresh,restart=False):
        status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='ignore',version=1))
        self.assertEqual(status,200,out)
        if restart: app.initialize()
        if intermediate_refresh: self.details()
        status,out=self.call('/recognition/candidates/'+item['id']+'/resolve',dict(action='reopen',version=2))
        self.assertEqual(status,200,out)

    def check_ignore_reopen(self,*,intermediate_refresh,restart):
        capture=self.submission_capture()
        item=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        self.ignore_reopen(item,intermediate_refresh=intermediate_refresh,restart=restart)
        if restart: app.initialize()
        for _ in range(2):
            action=self.details()['observation']['actions'][0]
            self.assertTrue(action['source_completion_paused'])
        self.submit(capture)
        row=self.candidate_row(item)
        self.assertEqual((row['state'],row['version']),('pending',3))

    def test_transition_ignore_reopen_without_read(self):
        self.check_ignore_reopen(intermediate_refresh=False,restart=False)

    def test_transition_ignore_reopen_without_read_survives_restart(self):
        self.check_ignore_reopen(intermediate_refresh=False,restart=True)

    def test_transition_ignore_reopen_with_read(self):
        self.check_ignore_reopen(intermediate_refresh=True,restart=False)

    def test_transition_ignore_reopen_with_read_survives_restart(self):
        self.check_ignore_reopen(intermediate_refresh=True,restart=True)

    def paused_pair(self):
        capture=self.mixed_capture('active')
        record=capture['request']['records'][0]['record']
        record['text']='请于2026年10月23日前提交第二次作业的习题和课程实验报告。'
        draft=json.loads(self.drafts[record['input_id']])
        for index,task in enumerate(draft['tasks']):
            task.update(title=('提交习题','提交实验报告')[index],description=record['text'],
                notice_type='uncertain',submission_relation='unknown',recommendation='candidate',reason='请选择新增或更新任务。')
            for field in ('action','deadline'):
                task['assessment'][field]['evidence']=[dict(part_id='body',quote=record['text'])]
        self.drafts[record['input_id']]=json.dumps(draft)
        first,second=self.run_request(self.sync_native(capture)['requests'][0])['items']
        self.associate_candidate('assignment_submission')
        # Observe the ignore so this test independently covers task transfer.
        self.ignore_reopen(second,intermediate_refresh=True)
        action=next(a for a in self.details()['observation']['actions']
            if a['action_id']==submission_runtime.identity('candidate',second['id']))
        self.assertTrue(action['source_completion_paused'])
        return capture,first,second

    def check_paused_update(self,*,intermediate_refresh,restart,resume_before=False,resume_after=False):
        capture,first,second=self.paused_pair()
        if resume_before:
            detail=self.details()
            aid=submission_runtime.identity('candidate',second['id'])
            self.assertEqual(self.apply(self.operation(detail,'resume_source_completion',action_ids=[aid]))[0],201)
        task=self.create(first)
        if intermediate_refresh: self.details()
        if restart: app.initialize()
        body=self.update(second,task)
        # The same successful resolution must not replay the transfer.
        self.assertEqual(self.call('/recognition/candidates/'+second['id']+'/resolve',body)[0],200)
        self.assertEqual(self.task_action(task)['source_completion_paused'],not resume_before)
        if resume_after:
            detail=self.details()
            aid=submission_runtime.identity('task',task['id'])
            operation=self.operation(detail,'resume_source_completion',action_ids=[aid])
            self.assertEqual(self.apply(operation)[0],201)
            self.assertEqual(self.apply(operation)[0],200)
        app.initialize()
        for _ in range(2):
            self.assertEqual(self.task_action(task)['source_completion_paused'],not (resume_before or resume_after))
        self.submit(capture)
        after=self.call('/workspace')[1]['tasks']['value'][0]
        self.assertEqual(after['status'],'completed' if resume_before or resume_after else 'not_started')

    def test_transition_paused_candidate_updates_task_without_read(self):
        self.check_paused_update(intermediate_refresh=False,restart=False)

    def test_transition_paused_candidate_updates_task_after_read(self):
        self.check_paused_update(intermediate_refresh=True,restart=False)

    def test_transition_paused_candidate_updates_task_without_read_after_restart(self):
        self.check_paused_update(intermediate_refresh=False,restart=True)

    def test_transition_paused_candidate_updates_task_after_read_and_restart(self):
        self.check_paused_update(intermediate_refresh=True,restart=True)

    def test_transition_resumed_candidate_can_update_and_complete_task(self):
        self.check_paused_update(intermediate_refresh=True,restart=True,resume_before=True)

    def test_transition_inherited_task_pause_can_be_resumed(self):
        self.check_paused_update(intermediate_refresh=True,restart=True,resume_after=True)

    def test_transition_combined_history_inherits_pause_from_later_candidate(self):
        capture,first,second=self.paused_pair()
        self.create(first)
        second=dict(second,version=3)
        self.create(second)
        saved=self.call('/workspace')[1]['tasks']
        first_task,second_task=saved['value']
        first_task['recognitionHistory']+=second_task['recognitionHistory']
        saved['value']=[first_task]
        # A new merged task must inherit protection from every contributor.
        first_task['id']='merged-history-task'
        self.assertEqual(self.call('/data/tasks',saved,method='PUT')[0],200)
        self.assertTrue(self.task_action(first_task)['source_completion_paused'])
        app.initialize();self.submit(capture)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'not_started')

    def check_independent_update(self,intermediate_refresh):
        capture=self.mixed_capture('active')
        record=capture['request']['records'][0]['record']
        draft=json.loads(self.drafts[record['input_id']])
        draft['tasks'][0]['submission_relation']='assignment_submission'
        draft['tasks'][1].update(submission_relation='other',notice_type='uncertain',
            recommendation='candidate',reason='请关联独立阅读要求。')
        self.drafts[record['input_id']]=json.dumps(draft)
        first,second=self.run_request(self.sync_native(capture)['requests'][0])['items']
        task=self.create(first)
        if intermediate_refresh: self.details()
        self.update(second,task)
        app.initialize()
        self.assertEqual(self.task_action(task)['relation'],'unknown')
        self.submit(capture)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'not_started')

    def test_transition_independent_update_invalidates_existing_submission_mapping(self):
        self.check_independent_update(True)

    def test_transition_independent_update_without_intermediate_read(self):
        self.check_independent_update(False)

    def test_transition_foreign_action_invalidates_previous_source_mapping(self):
        first_capture=self.submission_capture()
        first=self.run_request(self.sync_native(first_capture)['requests'][0])['items'][0]
        task=self.create(first)
        first_input=first_capture['request']['records'][0]['record']['input_id']
        capture=self.mixed_capture('active')
        record=capture['request']['records'][0]['record']
        draft=json.loads(self.drafts[record['input_id']])
        record['input_id']+=':independent-source'
        record['source_item_id']+='-independent'
        capture['assignment']['content_id']=record['source_item_id']
        other=draft['tasks'][1]
        other.update(submission_relation='other',notice_type='uncertain',
            recommendation='candidate',reason='请关联独立阅读要求。')
        self.drafts[record['input_id']]=json.dumps(dict(tasks=[other]))
        incoming=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        self.update(incoming,task)
        account=self.call('/sources/credentials')[1]['source_account_id']
        directory=self.call('/sources/submissions?source_account_id='+account)[1]
        for entry in directory['records']:
            detail=self.call('/sources/submissions/'+entry['record_id'])[1]
            if detail['observation']['snapshot']['input_id']==first_input:
                self.assertEqual(self.task_action(task,detail)['relation'],'unknown')
                break
        else: self.fail('original source control disappeared')
        app.initialize()
        self.submit(first_capture);self.submit(capture)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'not_started')

    def test_transition_workspace_history_merge_requires_reconfirmation(self):
        capture=self.mixed_capture('active')
        record=capture['request']['records'][0]['record']
        draft=json.loads(self.drafts[record['input_id']])
        draft['tasks'][0]['submission_relation']='assignment_submission'
        draft['tasks'][1]['submission_relation']='other'
        self.drafts[record['input_id']]=json.dumps(draft)
        first,second=self.run_request(self.sync_native(capture)['requests'][0])['items']
        first_task=self.create(first);self.create(second)
        self.assertEqual(self.task_action(first_task)['relation'],'assignment_submission')
        saved=self.call('/workspace')[1]['tasks']
        target,other=saved['value'];target['recognitionHistory']+=other['recognitionHistory']
        saved['value']=[target]
        self.assertEqual(self.call('/data/tasks',saved,method='PUT')[0],200)
        app.initialize()
        self.assertEqual(self.task_action(target)['relation'],'unknown')
        self.submit(capture)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'not_started')

    def test_transition_ordinary_task_edit_keeps_submission_mapping(self):
        capture=self.submission_capture()
        item=self.run_request(self.sync_native(capture)['requests'][0])['items'][0]
        task=self.create(item)
        self.assertEqual(self.task_action(task)['relation'],'assignment_submission')
        saved=self.call('/workspace')[1]['tasks']
        saved['value'][0]['title']='用户修改后的标题'
        self.assertEqual(self.call('/data/tasks',saved,method='PUT')[0],200)
        self.assertEqual(self.task_action(task)['relation'],'assignment_submission')
        self.submit(capture)
        self.assertEqual(self.call('/workspace')[1]['tasks']['value'][0]['status'],'completed')

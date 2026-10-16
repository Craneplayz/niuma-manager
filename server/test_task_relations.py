"""Real SQLite transactions and native capture state; all content is synthetic."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import app
import teaching
import relation_context as ctx
import task_relations as runtime
from contracts import task_relations as contract
from ddl_text.tests.test_teaching_pipeline import example
import test_teaching as fixture


class TaskRelationsTests(unittest.TestCase):
    setUp=fixture.TeachingIntegrationTests.setUp
    tearDown=fixture.TeachingIntegrationTests.tearDown
    call=fixture.TeachingIntegrationTests.call

    def seed(self,state='active',existing=True):
        request,_=example(('explicit-complete',)*3)
        texts=['实验12报告：甲.pdf，按合并提交入口交付。','实验13报告：乙.pdf，按合并提交入口交付。',
               '一次提交实验12和13的报告，甲.pdf、乙.pdf；只交报告题，不含研讨题。截止2026-11-08 18:00。']
        self.input_ids=[];self.captures=[];self.taskids=[]
        with app.connection() as db:
            for i,entry in enumerate(request['records']):
                e=deepcopy(entry);r=e['record'];r['text']=texts[i]
                r['body_status']='complete';r['references']=[]
                r['collected_at']='2026-10-15T10:00:00+08:00'
                e['source']['course_or_project']='实验原理' if i<2 else '实验实践'
                body=dict(request,records=[e]);payload=body
                if i==2:
                    payload=dict(contract_version='teaching-capture/0.1',request=body,assignment=dict(state=state,
                        basis='review_submission_history_title' if state=='submitted' else 'active_assignment_page',
                        course_id=r['course_id'],content_id=r['source_item_id']))
                self.captures.append(payload);self.input_ids.append(r['input_id'])
                teaching.register(db,self.uid,'synthetic-course-user',[payload])
            sources=list(db.execute('SELECT * FROM teaching_sources WHERE user_id=? ORDER BY input_id',(self.uid,)))
            self.account=sources[0]['account']
            tasks=[]
            for i,input_id in enumerate(self.input_ids[:2]):
                source=next(s for s in sources if s['input_id']==input_id)
                card=dict(title='实验'+str(i+12),description=texts[i],course_or_project='实验原理',
                    candidate_id='c'+str(i),deadline={},review_reasons=[],notice_type='create')
                db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',
                    (self.uid,source['request_id'],'fingerprint',source['request'],'{}'))
                db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',
                    ('candidate'+str(i),self.uid,source['request_id'],json.dumps(card),'applied' if existing else 'pending'))
                if existing:
                    task=runtime.new_task(dict(title=card['title'],description=card['description'],course='实验原理',content_type='task',due=None))
                    task['recognitionHistory']=[dict(storageId='candidate'+str(i),requestId=source['request_id'],candidateId=card['candidate_id'],action='create')]
                    tasks.append(task);runtime.remember(db,self.uid,task);self.taskids.append(task['id'])
            if tasks:runtime.write_tasks(db,self.uid,tasks,0)
        return self.proposal()

    def proposal(self):
        with app.connection() as db:nodes,_=ctx.inventory(db,self.uid)
        actions=[n for n in nodes.values() if n['kind']!='source']
        source=ctx.source_id(self.account,self.input_ids[2])
        members=[n['id'] for n in actions]+[source]
        request=contract.context(list(nodes.values()))
        group=dict(relation='combine',disposition='apply',members=members,
            result=dict(title='提交实验12和13报告',description='只交报告题，不含研讨题。',course='实验原理',content_type='task',due=dict(date='2026-11-08',time='18:00')),
            reason='两份材料由同一个接收入口要求一次提交',
            evidence=[dict(node_id=i,quote=nodes[i]['text']) for i in members],
            completion=[dict(source_id=source,covers=[n['id'] for n in actions])])
        return request,group

    def apply(self,request,group,pid='p1'):
        with app.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO task_relation_proposals VALUES(?,?,?,?,?,?,?,?,?,?)',
                (pid,self.uid,self.account,'job','review',json.dumps(request),json.dumps(group),None,None,runtime.stamp()))
            return runtime.apply_group(db,self.uid,pid,request,group,app.ApiError)

    def tasks(self):
        with app.connection() as db:return runtime.controls.workspace(db,self.uid)[0]

    def test_many_to_one_keeps_two_histories_and_receiver_deadline(self):
        request,group=self.seed();self.assertTrue(self.apply(request,group))
        tasks=self.tasks();self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0]['due'],dict(date='2026-11-08',time='18:00',precision='datetime'))
        self.assertEqual(len(tasks[0]['recognitionHistory']),2)
        self.assertEqual(tasks[0]['status'],'not_started')

    def test_only_receiver_submitted_completes_all_candidates(self):
        request,group=self.seed('submitted',False);self.assertTrue(self.apply(request,group))
        self.assertEqual(self.tasks()[0]['status'],'completed')
        with app.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM recognition_candidates WHERE state='pending'").fetchone()[0],0)

    def test_submission_controls_show_automatic_receiver_link(self):
        request,group=self.seed('submitted');self.assertTrue(self.apply(request,group))
        with app.connection() as db:
            row=runtime.controls.find(db,self.uid,self.account,self.input_ids[2])
            detail,refs=runtime.controls.refresh(db,row)
            actions=detail['observation']['actions']
            self.assertEqual(len(actions),1)
            self.assertEqual((actions[0]['relation'],actions[0]['link_basis'],actions[0]['decision'],actions[0]['completion_origin']),
                ('assignment_submission','model_verified','completed','source_submission'))

    def late_receiver_candidate(self):
        with app.connection() as db:
            source=db.execute('SELECT * FROM teaching_sources WHERE user_id=? AND input_id=?',(self.uid,self.input_ids[2])).fetchone()
            db.execute('INSERT OR IGNORE INTO recognition_batches VALUES(?,?,?,?,?,0)',
                       (self.uid,source['request_id'],'late',source['request'],'{}'))
            card=dict(candidate_id='late-card',title='实验12和13报告提交',description='只交报告题，不含研讨题。',
                      course_or_project='实验实践',deadline={},review_reasons=[],notice_type='create')
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',
                       ('late-candidate',self.uid,source['request_id'],json.dumps(card),'pending'))
            nodes,_=ctx.inventory(db,self.uid)
        action=nodes['candidate:late-candidate'];source=nodes[action['sources'][0]]
        request=contract.context([action,source])
        group=dict(relation='combine',disposition='apply',members=[action['id'],source['id']],
            result=dict(title=card['title'],description=card['description'],course='实验实践',content_type='task',due=None),
            reason='接收入口所收取的同一份作业',evidence=[dict(node_id=n['id'],quote=n['text'][:1000]) for n in [action,source]],
            completion=[dict(source_id=source['id'],covers=[action['id']])])
        return request,group

    def test_late_candidate_joins_existing_verified_completed_delivery(self):
        request,group=self.seed('submitted');self.apply(request,group)
        original=deepcopy(self.tasks()[0])
        request,group=self.late_receiver_candidate()
        self.assertTrue(self.apply(request,group,'late'))
        tasks=self.tasks();self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0]['id'],original['id'])
        self.assertEqual(tasks[0]['title'],original['title'])
        self.assertEqual(tasks[0]['due'],original['due'])
        self.assertEqual(tasks[0]['status'],'completed')
        self.assertEqual(len(tasks[0]['recognitionHistory']),3)
        with app.connection() as db:
            row=db.execute('SELECT * FROM task_relation_proposals WHERE id="late"').fetchone()
            runtime.undo(db,self.uid,row,app.ApiError)
        self.assertEqual(self.tasks(),[original])

    def test_late_candidate_does_not_overwrite_user_changed_delivery(self):
        request,group=self.seed('submitted');self.apply(request,group)
        with app.connection() as db:
            tasks,version=runtime.controls.workspace(db,self.uid)
            tasks[0]['title']='用户保留的标题';runtime.manual_write(db,self.uid,tasks)
            runtime.write_tasks(db,self.uid,tasks,version)
        request,group=self.late_receiver_candidate();self.assertFalse(self.apply(request,group,'late'))
        self.assertEqual(len(self.tasks()),1)
        self.assertEqual(self.tasks()[0]['title'],'用户保留的标题')

    def test_deleted_delivery_is_not_recreated_by_late_source(self):
        request,group=self.seed('submitted');self.apply(request,group)
        with app.connection() as db:
            tasks,version=runtime.controls.workspace(db,self.uid)
            tasks[0].update(status='cancelled',recycleBin=dict(deletedAt='2026-10-15',status='completed'))
            runtime.manual_write(db,self.uid,tasks);runtime.write_tasks(db,self.uid,tasks,version)
        request,group=self.late_receiver_candidate();self.assertFalse(self.apply(request,group,'late'))
        self.assertEqual(len(self.tasks()),1)
        self.assertTrue(self.tasks()[0]['recycleBin'])

    def test_transient_invalid_model_output_gets_only_one_fresh_attempt(self):
        from ddl_text.adapters import AgentError
        self.seed()
        with app.connection() as db:job=runtime.start(db,self.uid,'bounded-recovery-test')
        calls=[]
        def fail_once(request):
            calls.append(request)
            if len(calls)==1:raise AgentError('invalid_agent_output',True,'output_truncated')
            return dict(contract_version=contract.VERSION,context_digest=request['context_digest'],groups=[])
        with patch.object(runtime,'model',side_effect=fail_once):runtime.process(app.DB,self.uid,job,app.ApiError)
        self.assertEqual(len(calls),2)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT state FROM task_relation_jobs WHERE id=?',(job,)).fetchone()[0],'succeeded')

    def test_omitted_submitted_candidate_receives_one_focused_scope_check(self):
        request,group=self.seed('submitted');self.apply(request,group)
        request,group=self.late_receiver_candidate()
        with app.connection() as db:job=runtime.start(db,self.uid,'focused-test')
        calls=[]
        def model(current):
            calls.append(current)
            groups=[] if len(calls)==1 else [group]
            return dict(contract_version=contract.VERSION,context_digest=current['context_digest'],groups=groups)
        with patch.object(runtime,'model',side_effect=model):runtime.process(app.DB,self.uid,job,app.ApiError)
        self.assertEqual(len(calls),2)
        self.assertEqual({n['id'] for n in calls[1]['nodes']},{n['id'] for n in request['nodes']})
        self.assertEqual(len(self.tasks()),1)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT state FROM recognition_candidates WHERE id="late-candidate"').fetchone()[0],'applied')

    def test_relation_completion_uses_shanghai_date(self):
        from datetime import datetime,timezone
        class Clock:
            @staticmethod
            def now(tz=None):return datetime(2026,10,10,17,30,tzinfo=timezone.utc)
        task=dict(plans=[])
        with patch.object(runtime,'datetime',Clock):runtime.complete(task)
        self.assertEqual(task['completedAt'],'2026-10-11')
        task['completedAt']='2026-10-09'
        with patch.object(runtime,'datetime',Clock):runtime.complete(task)
        self.assertEqual(task['completedAt'],'2026-10-09')

    def test_payload_clipping_does_not_change_short_source_revision(self):
        short=dict(id='short',kind='source',text='明确的提交要求',truncated=False)
        nodes=[dict(id=str(i),kind='source',text='长'*6000,truncated=False) for i in range(12)]+[short]
        result=ctx.bounded_context(nodes)
        self.assertEqual(result['nodes'][-1],short)
        self.assertTrue(all(n['truncated'] and len(n['text'])==2000 for n in result['nodes'][:-1]))

    def test_merge_cannot_clear_existing_deadline(self):
        self.seed()
        with app.connection() as db:
            tasks,version=runtime.controls.workspace(db,self.uid)
            tasks[0]['due']=dict(date='2026-11-03',precision='datetime',time='23:30')
            runtime.remember(db,self.uid,tasks[0]);runtime.write_tasks(db,self.uid,tasks,version)
        request,group=self.proposal();group['result']['due']=None
        self.assertFalse(self.apply(request,group))
        self.assertEqual(len(self.tasks()),2)
        self.assertEqual(self.tasks()[0]['due']['time'],'23:30')

    def test_workspace_read_archives_old_import_without_losing_history(self):
        self.seed()
        with app.connection() as db:
            tasks,version=runtime.controls.workspace(db,self.uid)
            tasks[0]['due']=dict(date='2000-01-01',precision='date')
            history=deepcopy(tasks[0]['recognitionHistory'])
            runtime.write_tasks(db,self.uid,tasks,version)
        status,result=self.call('/workspace');self.assertEqual(status,200)
        archived=result['tasks']['value'][0]
        self.assertEqual(archived['status'],'cancelled')
        self.assertEqual(archived['recycleBin']['reason'],'expired_retention')
        self.assertEqual(archived['recognitionHistory'],history)
        self.assertEqual(result['tasks']['value'][1]['status'],'not_started')

    def test_receiver_evidence_survives_in_task_and_next_analysis(self):
        request,group=self.seed();self.apply(request,group)
        task=self.tasks()[0]
        status,result=self.call('/recognition/task-evidence',dict(task_id=task['id']))
        self.assertEqual(status,200)
        self.assertTrue(any('2026-11-08' in q['quote'] for e in result['evidence'] for q in e['quotes']))
        with app.connection() as db:
            nodes,_=ctx.inventory(db,self.uid)
            self.assertIn('2026-11-08',nodes['task:'+task['id']]['text'])
            self.assertEqual(runtime.source_evidence(db,'unrelated-user',task['id']),[])
            runtime.undo(db,self.uid,db.execute('SELECT * FROM task_relation_proposals WHERE id="p1"').fetchone(),app.ApiError)
            self.assertEqual(runtime.source_evidence(db,self.uid,task['id']),[])

    def test_focus_and_completed_statistics_are_not_orphaned(self):
        request,group=self.seed()
        with app.connection() as db:
            db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(self.uid,'focus',json.dumps(dict(taskId=self.taskids[0]))))
        self.assertFalse(self.apply(request,group))
        with app.connection() as db:
            db.execute('DELETE FROM workspace WHERE name="focus"')
            db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(self.uid,'feedback',json.dumps([dict(taskId=self.taskids[0],actualMinutes=40)])))
            with self.assertRaises(app.ApiError):runtime.apply_group(db,self.uid,'p1',request,group,app.ApiError,True)
        self.assertEqual(len(self.tasks()),2)

    def test_partial_coverage_does_not_complete_whole_group(self):
        request,group=self.seed('submitted');group['completion'][0]['covers']=group['completion'][0]['covers'][:1]
        self.assertTrue(self.apply(request,group));self.assertEqual(self.tasks()[0]['status'],'not_started')

    def test_later_native_submission_completes_and_manual_reopen_pauses(self):
        request,group=self.seed();self.apply(request,group)
        capture=deepcopy(self.captures[2]);capture['assignment'].update(state='submitted',basis='review_submission_history_title')
        capture['request']['records'][0]['record']['collected_at']='2026-10-15T11:00:00+08:00'
        with app.connection() as db:
            teaching.register(db,self.uid,'synthetic-course-user',[capture]);runtime.refresh_completions(db,self.uid)
        self.assertEqual(self.tasks()[0]['status'],'completed')
        tasks=self.tasks();tasks[0]['status']='not_started'
        with app.connection() as db:
            runtime.manual_write(db,self.uid,tasks);runtime.write_tasks(db,self.uid,tasks,3)
            runtime.refresh_completions(db,self.uid)
        self.assertEqual(self.tasks()[0]['status'],'not_started')

    def test_late_result_account_switch_and_user_edits_rejected(self):
        request,group=self.seed()
        tasks=self.tasks();tasks[0]['title']='用户手工修改'
        with app.connection() as db:runtime.write_tasks(db,self.uid,tasks,3)
        with self.assertRaises(app.ApiError):self.apply(request,group)
        request,group=self.proposal();self.assertFalse(self.apply(request,group,'p2'))
        with app.connection() as db:db.execute('UPDATE source_credentials SET username=? WHERE user_id=?',('another-account',self.uid))
        with self.assertRaises(app.ApiError):self.apply(request,group,'p3')

    def test_ui_default_normalization_does_not_pause_or_block_undo(self):
        request,group=self.seed();self.apply(request,group)
        tasks=self.tasks();tasks[0].update(completedAt='',planningExcluded=False)
        with app.connection() as db:
            runtime.manual_write(db,self.uid,tasks);runtime.write_tasks(db,self.uid,tasks,5)
            self.assertEqual(db.execute('SELECT paused FROM task_relation_links WHERE user_id=?',(self.uid,)).fetchone()[0],0)
            nodes,_=ctx.inventory(db,self.uid)
            self.assertFalse(nodes['task:'+tasks[0]['id']]['protected'])
            runtime.undo(db,self.uid,db.execute('SELECT * FROM task_relation_proposals WHERE id="p1"').fetchone(),app.ApiError)
        self.assertEqual(len(self.tasks()),2)

    def test_undo_restores_without_overwriting_later_edits(self):
        request,group=self.seed();before=self.tasks();self.apply(request,group)
        with app.connection() as db:
            row=db.execute('SELECT * FROM task_relation_proposals WHERE id="p1"').fetchone()
            runtime.undo(db,self.uid,row,app.ApiError)
        self.assertEqual(self.tasks(),before)
        # Undo is idempotent.
        with app.connection() as db:
            runtime.undo(db,self.uid,db.execute('SELECT * FROM task_relation_proposals WHERE id="p1"').fetchone(),app.ApiError)

    def test_undo_fails_if_task_changed(self):
        request,group=self.seed();self.apply(request,group)
        tasks=self.tasks();tasks[0]['title']='手工编辑'
        with app.connection() as db:
            runtime.write_tasks(db,self.uid,tasks,4)
            with self.assertRaises(app.ApiError):runtime.undo(db,self.uid,db.execute('SELECT * FROM task_relation_proposals WHERE id="p1"').fetchone(),app.ApiError)
        self.assertEqual(self.tasks()[0]['title'],'手工编辑')

    def test_context_retrieves_cross_course_with_no_foreign_account(self):
        self.seed()
        with app.connection() as db:nodes,_=ctx.inventory(db,self.uid)
        windows=list(ctx.windows(nodes));self.assertTrue(windows)
        self.assertTrue(any(len([n for n in w['nodes'] if n['kind']!='source'])==2 for w in windows))
        for w in windows:contract.context(w['nodes'])
        with app.connection() as db:db.execute('UPDATE source_credentials SET username="other" WHERE user_id=?',(self.uid,));nodes,_=ctx.inventory(db,self.uid)
        self.assertFalse(nodes)

    def test_background_failure_preserves_tasks_and_is_visible(self):
        self.seed();before=self.tasks()
        with app.connection() as db:job=runtime.start(db,self.uid,'test')
        with patch.object(runtime,'model',side_effect=ValueError('private error')):runtime.process(app.DB,self.uid,job,app.ApiError)
        self.assertEqual(self.tasks(),before)
        with app.connection() as db:
            saved=dict(db.execute('SELECT * FROM task_relation_jobs WHERE id=?',(job,)).fetchone())
        self.assertEqual(saved['state'],'failed');self.assertNotIn('private error',saved['error'])

    def test_new_sync_supersedes_older_relation_inventory(self):
        self.seed()
        with app.connection() as db:
            old=runtime.start(db,self.uid,'sync:first')
            self.assertEqual(runtime.start(db,self.uid,'manual'),old)
            new=runtime.start(db,self.uid,'sync:second')
            self.assertNotEqual(old,new)
            self.assertFalse(runtime.guard(db,self.uid,old))
            self.assertTrue(runtime.guard(db,self.uid,new))
            self.assertEqual(runtime.start(db,self.uid,'sync:second'),new)

    def test_receiving_bundle_retrieves_all_components_before_similar_noise(self):
        self.seed()
        with app.connection() as db:nodes,_=ctx.inventory(db,self.uid)
        sources=[ctx.source_id(self.account,i) for i in self.input_ids]
        actions=[n for n in nodes.values() if n['kind']!='source']
        for i in range(2):
            attachment=dict(sha256=str(i)*64)
            nodes[sources[i]]['attachments']=[attachment]
            actions[i]['attachments']=[attachment]
            nodes[sources[2]]['attachments'].append(attachment)
        # The two components have no shared title or topic; the receiving
        # endpoint's actual file identities bring them into the same context.
        actions[0]['text']='无机实验独立说明';actions[1]['text']='机械设计另一份材料'
        selected=list(ctx.windows(nodes))[0]['nodes']
        self.assertTrue({n['id'] for n in actions}|{sources[2]} <= {n['id'] for n in selected})

    def test_one_failed_window_does_not_abort_later_valid_window(self):
        request,group=self.seed('submitted',False)
        result=dict(contract_version=contract.VERSION,context_digest=request['context_digest'],groups=[group])
        with app.connection() as db:job=runtime.start(db,self.uid,'test')
        with patch.object(ctx,'windows',return_value=iter([request,request])),patch.object(runtime,'model',side_effect=[ValueError('private'),result]) as model:
            runtime.process(app.DB,self.uid,job,app.ApiError)
        self.assertEqual(model.call_count,2)
        self.assertEqual(self.tasks()[0]['status'],'completed')
        with app.connection() as db:
            windows=[dict(r) for r in db.execute('SELECT * FROM task_relation_windows WHERE job_id=? ORDER BY window_index',(job,))]
            jobrow=db.execute('SELECT state,error FROM task_relation_jobs WHERE id=?',(job,)).fetchone()
            self.assertEqual(db.execute('SELECT count(*) FROM task_relation_cache').fetchone()[0],1)
        self.assertEqual([w['state'] for w in windows],['failed','succeeded'])
        self.assertEqual(jobrow['state'],'failed');self.assertIn('1/2',jobrow['error'])
        self.assertNotIn('private',json.dumps(windows))

    def receiving_capture(self,index,state):
        capture=deepcopy(self.captures[index])
        body=capture.get('request',capture)
        r=body['records'][0]['record']
        r['collected_at']='2026-10-16T11:00:00+08:00'
        return dict(contract_version='teaching-capture/0.1',request=body,assignment=dict(state=state,
            basis='review_submission_history_title' if state=='submitted' else 'active_assignment_page',
            course_id=r['course_id'],content_id=r['source_item_id']))

    def joint_proposal(self,last_state='active'):
        self.seed(last_state)
        with app.connection() as db:
            teaching.register(db,self.uid,'synthetic-course-user',[self.receiving_capture(0,'submitted')])
        request,group=self.proposal()
        other=ctx.source_id(self.account,self.input_ids[0]);group['members'].append(other)
        group['evidence'].append(dict(node_id=other,quote=next(n['text'] for n in request['nodes'] if n['id']==other)))
        old=group['completion'][0]
        group['completion']=[dict(source_ids=[old['source_id'],other],covers=old['covers'])]
        contract.check_pair(request,dict(contract_version=contract.VERSION,context_digest=request['context_digest'],groups=[group]))
        return request,group

    def test_joint_coverage_waits_for_all_receivers_and_honors_manual_reopen(self):
        request,group=self.joint_proposal();self.assertTrue(self.apply(request,group))
        self.assertEqual(self.tasks()[0]['status'],'not_started')
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM task_relation_links').fetchone()[0],0)
            teaching.register(db,self.uid,'synthetic-course-user',[self.receiving_capture(2,'submitted')])
            runtime.refresh_completions(db,self.uid)
        self.assertEqual(self.tasks()[0]['status'],'completed')
        tasks=self.tasks();tasks[0]['status']='not_started'
        with app.connection() as db:
            runtime.manual_write(db,self.uid,tasks);runtime.write_tasks(db,self.uid,tasks,7)
            runtime.refresh_completions(db,self.uid)
            self.assertEqual(db.execute('SELECT paused FROM task_relation_completion_sets').fetchone()[0],1)
        self.assertEqual(self.tasks()[0]['status'],'not_started')

    def test_joint_coverage_completes_immediately_and_undo_removes_mapping(self):
        request,group=self.joint_proposal('submitted');before=self.tasks()
        self.assertTrue(self.apply(request,group));self.assertEqual(self.tasks()[0]['status'],'completed')
        with app.connection() as db:
            runtime.undo(db,self.uid,db.execute('SELECT * FROM task_relation_proposals WHERE id="p1"').fetchone(),app.ApiError)
            self.assertEqual(db.execute('SELECT count(*) FROM task_relation_completion_sets').fetchone()[0],0)
            runtime.refresh_completions(db,self.uid)
        self.assertEqual(self.tasks(),before)

    def test_joint_coverage_cannot_ignore_changed_source_or_user_pause(self):
        request,group=self.joint_proposal();self.apply(request,group)
        with app.connection() as db:
            teaching.register(db,self.uid,'synthetic-course-user',[self.receiving_capture(2,'submitted')])
            db.execute('UPDATE submission_controls SET content_digest="changed" WHERE user_id=? AND input_id=?',(self.uid,self.input_ids[0]))
            runtime.refresh_completions(db,self.uid)
        self.assertEqual(self.tasks()[0]['status'],'not_started')

    def test_sync_automatically_integrates_and_http_undo_restores(self):
        import source_jobs
        request,group=self.seed('submitted',False)
        def answer(request):
            return dict(contract_version=contract.VERSION,context_digest=request['context_digest'],groups=[group])
        with patch.object(app.source_sync,'collect',return_value=dict(requests=[],summary=dict(skipped=3))),patch.object(runtime,'model',side_effect=answer):
            status,run=self.call('/sources/sync-runs',dict(contract_version='source-sync/0.1',sources=['teaching_web'],consent=True,operation_id='relation-test'))
            self.assertEqual(status,202,run)
            source_jobs.wait_for_jobs(app.DB);runtime.wait_for_jobs(app.DB)
        status,listing=self.call('/recognition/relations');self.assertEqual(status,200,listing)
        contract.check_list(listing)
        self.assertEqual(listing['jobs'][0]['state'],'succeeded',listing)
        self.assertEqual(len(listing['proposals']),1)
        self.assertEqual(self.tasks()[0]['status'],'completed')
        proposal=listing['proposals'][0]
        self.assertEqual(self.call('/recognition/relations/'+proposal['id']+'/undo',{})[0],200)
        self.assertFalse(self.tasks())
        with app.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM recognition_candidates WHERE state='pending'").fetchone()[0],2)

    def test_unchanged_observation_reuses_cache_and_dismissal_survives_sync(self):
        request,group=self.seed();group['disposition']='review'
        with app.connection() as db:job=runtime.start(db,self.uid,'test')
        with patch.object(runtime,'model',return_value=dict(contract_version=contract.VERSION,context_digest=request['context_digest'],groups=[group])):
            # Context order from the retriever is significant; use its actual digest.
            with patch.object(runtime,'model',side_effect=lambda r:dict(contract_version=contract.VERSION,context_digest=r['context_digest'],groups=[group])):
                runtime.process(app.DB,self.uid,job,app.ApiError)
        status,listing=self.call('/recognition/relations');self.assertEqual(status,200)
        pid=listing['proposals'][0]['id'];self.assertEqual(self.call('/recognition/relations/'+pid+'/ignore',{})[0],200)
        with app.connection() as db:
            nodes,_=ctx.inventory(db,self.uid);before=list(ctx.windows(nodes))[0]
            native=runtime.controls.find(db,self.uid,self.account,self.input_ids[2]);detail=json.loads(native['detail'])
            detail['observation']['snapshot']['snapshot_id']='another-snapshot'
            db.execute('UPDATE submission_controls SET detail=? WHERE record_id=?',(json.dumps(detail),native['record_id']))
            after=list(ctx.windows(ctx.inventory(db,self.uid)[0]))[0]
            self.assertNotEqual(before['context_digest'],after['context_digest'])
            self.assertEqual(runtime.fingerprint(before),runtime.fingerprint(after))
            self.assertTrue(runtime.dismissed(db,self.uid,self.account,after,group))

    def test_delayed_model_cannot_overwrite_a_manual_task_edit(self):
        request,group=self.seed()
        with app.connection() as db:job=runtime.start(db,self.uid,'test')
        def answer(r):
            tasks=self.tasks();tasks[0]['title']='分析期间手动编辑'
            with app.connection() as db:runtime.write_tasks(db,self.uid,tasks,10)
            return dict(contract_version=contract.VERSION,context_digest=r['context_digest'],groups=[group])
        with patch.object(runtime,'model',side_effect=answer):runtime.process(app.DB,self.uid,job,app.ApiError)
        self.assertEqual(len(self.tasks()),2);self.assertEqual(self.tasks()[0]['title'],'分析期间手动编辑')
        self.assertEqual(self.call('/recognition/relations')[1]['jobs'][0]['state'],'failed')

    def test_stop_rejects_late_model_and_active_job_is_reused(self):
        import threading
        request,group=self.seed();before=self.tasks();entered=threading.Event();release=threading.Event()
        with app.connection() as db:
            job=runtime.start(db,self.uid,'test')
            self.assertEqual(runtime.start(db,self.uid,'repeat'),job)
        def answer(r):
            entered.set();release.wait(5)
            return dict(contract_version=contract.VERSION,context_digest=r['context_digest'],groups=[group])
        with patch.object(runtime,'model',side_effect=answer):
            worker=threading.Thread(target=runtime.process,args=(app.DB,self.uid,job,app.ApiError))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.call('/recognition/relations/'+job+'/stop',{})[0],200)
            finally:release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(self.tasks(),before)
        self.assertEqual(self.call('/recognition/relations')[1]['jobs'][0]['state'],'stopped')


if __name__=='__main__':unittest.main()

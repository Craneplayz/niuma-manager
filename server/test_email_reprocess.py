"""Synthetic tests for explicitly authorized email cache re-recognition."""
import email_test_support as support
import json
import copy
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import email_import
import email_source
import recognition
from email_test_support import no_tasks


ADDRESS='reprocess@stu.pku.edu.cn'


def one_task(request, _error):
    fixture=Path(__file__).resolve().parent.parent/'contracts/text-to-app/text-contract-v0.2.json'
    candidate=json.loads(fixture.read_text(encoding='utf-8'))['expected']['tasks'][0]
    record=request['records'][0]
    candidate['candidate_id']='rerun-candidate'; candidate['input_id']=record['input_id']
    candidate['sources']=[dict(input_id=record['input_id'],platform='email',url=None,
        label=record['source_label'],source_item_id=record['source_item_id'])]
    candidate['evidence'][0]['input_id']=record['input_id']
    result=dict(contract_version='0.2',request_id=request['request_id'],status='success',
        record_results=[dict(input_id=record['input_id'],status='success',task_ids=['rerun-candidate'],warnings=[])],
        tasks=[candidate],errors=[])
    return result


class EmailReprocessTests(support.EmailHTTPTestCase):
    address = ADDRESS
    mail_uids = (1,)


    def test_explicit_reprocess_uses_body_snapshot_and_creates_new_success_version(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        status,job=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        self.assertEqual(status,202)
        original=self.settled(job['job_id'])
        original_id=original['items'][0]['request_id']
        self.assertEqual(self.model.call_count,1)
        fetches=self.imap_calls
        self.assertEqual(self.call('/sources/email/reprocess',dict(operation_id='missing-consent',input_ids=[input_id]))[0],400)
        status,started=self.call('/sources/email/reprocess',dict(operation_id='rerun',input_ids=[input_id],consent=True))
        self.assertEqual(status,202,started); self.assertEqual(started['kind'],'reprocess')
        final=self.settled(started['job_id'])
        self.assertEqual(final['state'],'succeeded',final)
        new_id=final['items'][0]['request_id']
        self.assertNotEqual(new_id,original_id)
        self.assertEqual(self.imap_calls,fetches,'successful reruns must use the stored body')
        self.assertEqual(self.model.call_count,2)
        with app.connection() as db:
            uid=db.execute('SELECT user_id FROM email_source_credentials WHERE address=?',(ADDRESS,)).fetchone()['user_id']
            self.assertTrue(db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,original_id)).fetchone())
            pointer=db.execute('SELECT request_id FROM email_recognition_cache WHERE user_id=? AND input_id=?',(uid,input_id)).fetchone()
            self.assertEqual(pointer['request_id'],new_id)
        ordinary=self.call('/sources/email/jobs',dict(operation_id='ordinary-again',input_ids=[input_id]))[1]
        self.assertEqual(self.settled(ordinary['job_id'])['counts']['cached'],1)
        self.assertEqual(self.model.call_count,2)

    def test_failed_rerun_keeps_previous_success_pointer(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        _,job=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        original=self.settled(job['job_id'])['items'][0]['request_id']
        def failed(request,error):
            result=no_tasks(request,error); result['status']='failed'; result['tasks']=[]
            result['errors']=[dict(input_id=input_id,code='invalid_agent_output',message='synthetic',retryable=True)]
            result['record_results'][0]['status']='failed'; return result
        self.model.side_effect=failed
        _,started=self.call('/sources/email/reprocess',dict(operation_id='failed-rerun',input_ids=[input_id],consent=True))
        final=self.settled(started['job_id'])
        self.assertIn(final['state'],('failed','partial_success'))
        with app.connection() as db:
            uid=db.execute('SELECT user_id FROM email_source_credentials WHERE address=?',(ADDRESS,)).fetchone()['user_id']
            state,_,_,request_id,_=email_import._item_state(db,uid,input_id)
            self.assertIn(state,('has_tasks','no_tasks')); self.assertEqual(request_id,original)
        self.assertEqual(self.call('/sources/email/jobs',dict(operation_id='ordinary-again',input_ids=[input_id]))[1]['counts']['cached'],1)

    def test_legacy_success_rerun_uses_source_version_snapshot_without_imap(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        _,job=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        original=self.settled(job['job_id'])['items'][0]['request_id']
        with app.connection() as db:
            uid=db.execute('SELECT user_id FROM email_source_credentials WHERE address=?',(ADDRESS,)).fetchone()['user_id']
            row=db.execute('SELECT fingerprint,request,result,demo FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,original)).fetchone()
            request=json.loads(row['request']); legacy='email:legacy:stored-success'; request['request_id']=legacy
            other=dict(request['records'][0],input_id=email_source._identity(ADDRESS,'77',999),text='unselected synthetic body')
            request['records'].append(other)
            account=__import__('hashlib').sha256(ADDRESS.encode()).hexdigest()
            db.execute('DELETE FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,original))
            db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,?)',(uid,legacy,row['fingerprint'],json.dumps(request),row['result'],row['demo']))
            db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)',(uid,account,input_id,'synthetic-digest',json.dumps(request)))
        fetches=self.imap_calls
        _,started=self.call('/sources/email/reprocess',dict(operation_id='legacy-rerun',input_ids=[input_id],consent=True))
        final=self.settled(started['job_id'])
        self.assertEqual(final['state'],'succeeded',final)
        self.assertNotEqual(final['items'][0]['request_id'],legacy)
        self.assertEqual(self.imap_calls,fetches)
        self.assertEqual([record['input_id'] for record in self.model.call_args.args[0]['records']],[input_id])
        with app.connection() as db:
            self.assertTrue(db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,legacy)).fetchone())

    def test_policy_projection_reports_four_states_without_recognition(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        with app.connection() as db:
            uid=db.execute('SELECT user_id FROM email_source_credentials WHERE address=?',(ADDRESS,)).fetchone()['user_id']
            db.execute('INSERT INTO email_recognition_cache VALUES(?,?,?,?)',(uid,input_id,'saved-current','policy-current'))
            with patch.object(email_import,'current_policy_version',return_value='policy-current'):
                self.assertEqual(email_import.policy_cache_metadata(db,uid,[input_id])[input_id]['policy_status'],'current')
            db.execute('UPDATE email_recognition_cache SET saved_policy_version=? WHERE user_id=? AND input_id=?',('policy-old',uid,input_id))
            with patch.object(email_import,'current_policy_version',return_value='policy-current'):
                self.assertEqual(email_import.policy_cache_metadata(db,uid,[input_id])[input_id]['policy_status'],'outdated')
            db.execute('UPDATE email_recognition_cache SET saved_policy_version=NULL WHERE user_id=? AND input_id=?',(uid,input_id))
            with patch.object(email_import,'current_policy_version',return_value='policy-current'):
                self.assertEqual(email_import.policy_cache_metadata(db,uid,[input_id])[input_id]['policy_status'],'unknown')
            with patch.object(email_import,'current_policy_version',return_value=None):
                self.assertEqual(email_import.policy_cache_metadata(db,uid,[input_id])[input_id]['policy_status'],'unavailable')
        self.assertEqual(self.model.call_count,0)
        self.assertEqual(self.imap_calls,0)

    def _seed_candidate(self,input_id,state='pending',version=1):
        with app.connection() as db:
            uid=db.execute('SELECT user_id FROM email_source_credentials WHERE address=?',(ADDRESS,)).fetchone()['user_id']
            _,_,_,request_id,_=email_import._item_state(db,uid,input_id)
            card={'input_id':input_id,'candidate_id':'protected-card','title':'untouched'}
            db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate,state,version) VALUES(?,?,?,?,?,?)',
                ('protected-card-'+state,uid,request_id,json.dumps(card),state,version))
            return uid,request_id,'protected-card-'+state,json.dumps(card)

    def test_ignored_accepted_and_edited_candidates_are_never_rewritten(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        _,job=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        self.settled(job['job_id'])
        for state,version in [('ignored',2),('applied',2),('pending',2)]:
            uid,old_request,cid,original=self._seed_candidate(input_id,state,version)
            _,started=self.call('/sources/email/reprocess',dict(operation_id='protect-'+state+str(version),input_ids=[input_id],consent=True))
            self.assertEqual(self.settled(started['job_id'])['state'],'succeeded')
            with app.connection() as db:
                row=db.execute('SELECT request_id,candidate,state,version FROM recognition_candidates WHERE id=?',(cid,)).fetchone()
                self.assertEqual((row['request_id'],row['candidate'],row['state'],row['version']),(old_request,original,state,version))

    def test_review_during_generation_is_rechecked_before_merge(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        _,job=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        self.settled(job['job_id'])
        uid,old_request,cid,original=self._seed_candidate(input_id)
        entered=threading.Event(); release=threading.Event()
        def blocked(request,error):
            entered.set(); release.wait(5); return no_tasks(request,error)
        self.model.side_effect=blocked
        _,started=self.call('/sources/email/reprocess',dict(operation_id='concurrent-review',input_ids=[input_id],consent=True))
        self.assertTrue(entered.wait(3))
        with app.connection() as db:
            db.execute("UPDATE recognition_candidates SET state='ignored',version=version+1,resolution='manual' WHERE id=?",(cid,))
        release.set(); self.assertEqual(self.settled(started['job_id'])['state'],'succeeded')
        with app.connection() as db:
            row=db.execute('SELECT request_id,candidate,state,version,resolution FROM recognition_candidates WHERE id=?',(cid,)).fetchone()
            self.assertEqual((row['request_id'],row['candidate'],row['state'],row['version'],row['resolution']),
                (old_request,original,'ignored',2,'manual'))

    def test_new_candidate_is_uncertain_when_a_protected_card_exists(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        self.imap.body='请在2026年9月30日前提交需求报告。'.encode()
        _,job=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        self.settled(job['job_id'])
        uid,_,old_cid,_=self._seed_candidate(input_id,'ignored',2)
        self.model.side_effect=one_task
        _,started=self.call('/sources/email/reprocess',dict(operation_id='protected-task',input_ids=[input_id],consent=True))
        final=self.settled(started['job_id']); self.assertEqual(final['state'],'succeeded',final)
        with app.connection() as db:
            old=db.execute('SELECT 1 FROM recognition_candidates WHERE id=?',(old_cid,)).fetchone()
            new=json.loads(db.execute("SELECT candidate FROM recognition_candidates WHERE user_id=? AND request_id=? AND id!=?",(uid,final['items'][0]['request_id'],old_cid)).fetchone()['candidate'])
            self.assertTrue(old)
            self.assertEqual(new['notice_type'],'uncertain')
            self.assertTrue(new['needs_review'])
            self.assertIn('受保护的人工处理记录',new['review_reasons'][-1])

    def test_stop_revoke_and_exclude_during_model_prevent_late_persistence(self):
        input_id=email_source._identity(ADDRESS,'77',1)
        _,initial=self.call('/sources/email/jobs',dict(operation_id='initial',input_ids=[input_id]))
        self.settled(initial['job_id'])
        for mode in ('stop','revoke','exclude'):
            if mode=='revoke': self.call('/sources/email/preferences',dict(consent=True,process_all=False))
            if mode=='exclude': self.call('/sources/email/exclusions',dict(input_ids=[input_id],excluded=False))
            entered=threading.Event(); release=threading.Event()
            def blocked(request,error):
                entered.set(); release.wait(5); return no_tasks(request,error)
            self.model.side_effect=blocked
            _,started=self.call('/sources/email/reprocess',dict(operation_id='late-'+mode,input_ids=[input_id],consent=True))
            self.assertTrue(entered.wait(3))
            if mode=='stop': self.call('/sources/email/jobs/'+started['job_id']+'/stop',{})
            elif mode=='revoke': self.call('/sources/email/preferences',dict(consent=False,process_all=False))
            else: self.call('/sources/email/exclusions',dict(input_ids=[input_id],excluded=True))
            release.set(); self.settled(started['job_id'])
            self.assertEqual(self.model.call_count,2+('stop','revoke','exclude').index(mode))
            if mode=='revoke': self.call('/sources/email/preferences',dict(consent=True,process_all=False))
            if mode=='exclude': self.call('/sources/email/exclusions',dict(input_ids=[input_id],excluded=False))
        self.assertEqual(self.imap_calls,1)

    def test_failed_rerun_preserves_existing_pending_cards_and_success(self):
        self.imap.body='请在2026年9月30日前提交需求报告。'.encode()
        input_id=email_source._identity(ADDRESS,'77',1)
        self.model.side_effect=one_task
        _,job=self.call('/sources/email/jobs',dict(operation_id='task-initial',input_ids=[input_id]))
        initial=self.settled(job['job_id'])
        self.assertEqual(initial['state'],'succeeded',initial)
        with app.connection() as db:
            uid=db.execute('SELECT user_id FROM email_source_credentials WHERE address=?',(ADDRESS,)).fetchone()['user_id']
            before=[tuple(row) for row in db.execute('SELECT * FROM recognition_candidates WHERE user_id=?',(uid,))]
        def failed(request,error):
            result=no_tasks(request,error)
            result.update(status='failed',errors=[dict(input_id=input_id,code='invalid_agent_output',message='synthetic',retryable=True)])
            result['record_results'][0]['status']='failed'
            return result
        self.model.side_effect=failed
        _,job=self.call('/sources/email/reprocess',dict(operation_id='task-failed',input_ids=[input_id],consent=True))
        self.assertEqual(self.settled(job['job_id'])['state'],'failed')
        with app.connection() as db:
            after=[tuple(row) for row in db.execute('SELECT * FROM recognition_candidates WHERE user_id=?',(uid,))]
            self.assertEqual(after,before)
            self.assertEqual(email_import._item_state(db,uid,input_id)[3],initial['items'][0]['request_id'])

    def test_saved_workspace_task_is_unchanged_by_successful_reprocessing(self):
        self.imap.body='请在2026年9月30日前提交需求报告。'.encode()
        input_id=email_source._identity(ADDRESS,'77',1)
        self.model.side_effect=one_task
        _,job=self.call('/sources/email/jobs',dict(operation_id='saved-initial',input_ids=[input_id]))
        self.assertEqual(self.settled(job['job_id'])['state'],'succeeded')
        candidate=self.call('/recognition/batches')[1][0]['items'][0]
        workspace=self.call('/workspace')[1]
        status,result=self.call('/recognition/candidates/'+candidate['id']+'/resolve',dict(
            action='create',version=candidate['version'],reviewed=True,tasks_version=workspace.get('tasks',{}).get('version',0),
            draft=dict(title='Saved synthetic task',description='Reviewed synthetic task',due=None)))
        self.assertEqual(status,200,result)
        before=self.call('/workspace')[1]['tasks']
        _,job=self.call('/sources/email/reprocess',dict(operation_id='saved-rerun',input_ids=[input_id],consent=True))
        self.assertEqual(self.settled(job['job_id'])['state'],'succeeded')
        self.assertEqual(self.call('/workspace')[1]['tasks'],before)


class BulkUpdateTests(support.EmailHTTPTestCase):

    def seed_range(self):
        uid=self.call('/auth/me')[1]['user']['id']
        address=support.ADDRESS
        records,_=email_source.fetch_selected(address,'synthetic','synthetic',[email_source._identity(address,'77',1)])
        self.ids={n:email_source._identity(address,'77',n) for n in (1,59,60,61)}
        with app.connection() as db:
            for number,version in ((1,'old'),(59,None),(60,'new'),(61,'old')):
                record=copy.deepcopy(records[0]); record['input_id']=self.ids[number]; record['source_item_id']='77:'+str(number)
                rid=email_import._request_id(record['input_id'])
                request=dict(contract_version='0.2',request_id=rid,records=[record])
                db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',
                    (uid,rid,'synthetic',json.dumps(request),json.dumps(support.no_tasks(request,None))))
                db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?)',
                    (uid,rid,record['input_id'],json.dumps(dict(version=version)) if version else None))
        self.call('/sources/email/exclusions',dict(input_ids=[self.ids[61]],excluded=True))
        def model(request,error):
            recognition._EXECUTION.policy=dict(version='new')
            return support.no_tasks(request,error)
        self.model.side_effect=model
        self.policy=patch.object(email_import,'current_policy_version',return_value='new'); self.policy.start(); self.addCleanup(self.policy.stop)
        status,self.preview=self.call('/sources/email/preview',dict(limit=61,refresh=True))
        self.assertEqual(status,200,self.preview)
        return self.preview['recognition_updates']

    def payload(self,operation='bulk',include_unknown=False):
        metadata=self.preview['recognition_updates']
        return dict(operation_id=operation,range=dict(limit=61,start_date=None),snapshot_id=self.preview['cache']['snapshot_id'],
            policy_version=metadata['policy_version'],selection_token=metadata['selection_token'],consent=True,include_unknown=include_unknown)

    def test_full_range_update_skips_current_excluded_unknown_and_replays_idempotently(self):
        updates=self.seed_range()
        self.assertEqual((updates['outdated_count'],updates['unknown_count']),(1,1))
        self.assertNotIn(self.ids[1],[r['input_id'] for r in self.preview['messages']], 'outdated target is beyond first page')
        calls=self.imap_calls
        body=self.payload()
        status,job=self.call('/sources/email/reprocess',body)
        self.assertEqual(status,202,job)
        final=self.settled(job['job_id'])
        self.assertEqual(final['state'],'succeeded',final)
        self.assertEqual([i['input_id'] for i in final['items']],[self.ids[1]])
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(self.imap_calls,calls)
        status,replay=self.call('/sources/email/reprocess',body)
        self.assertEqual(status,200,replay); self.assertEqual(replay['job_id'],job['job_id'])
        after=self.call('/sources/email/preview',dict(limit=61,cache_only=True))[1]
        self.assertEqual(after['recognition_updates']['outdated_count'],0)
        self.assertEqual(after['recognition_updates']['unknown_count'],1)
        self.assertEqual(self.model.call_count,1)

    def test_unknown_versions_are_only_included_by_explicit_option(self):
        self.seed_range()
        status,job=self.call('/sources/email/reprocess',self.payload(include_unknown=True))
        self.assertEqual(status,202,job)
        final=self.settled(job['job_id'])
        self.assertEqual(final['state'],'succeeded',final)
        self.assertEqual({i['input_id'] for i in final['items']},{self.ids[1],self.ids[59]})
        self.assertEqual(self.model.call_count,2)

    def test_changed_selection_or_rules_require_fresh_confirmation(self):
        self.seed_range()
        body=self.payload()
        self.call('/sources/email/exclusions',dict(input_ids=[self.ids[1]],excluded=True))
        self.assertEqual(self.call('/sources/email/reprocess',body)[0],409)
        self.call('/sources/email/exclusions',dict(input_ids=[self.ids[1]],excluded=False))
        with patch.object(email_import,'current_policy_version',return_value='newer'):
            self.assertEqual(self.call('/sources/email/reprocess',body)[0],409)
        self.assertEqual(self.model.call_count,0)

    def test_snapshot_and_account_boundaries_and_consent(self):
        self.seed_range(); body=self.payload()
        missing=dict(body); missing.pop('consent')
        self.assertEqual(self.call('/sources/email/reprocess',missing)[0],400)
        self.call('/sources/email/preview',dict(limit=61,refresh=True))
        self.assertEqual(self.call('/sources/email/reprocess',body)[0],409)
        other=self.account('bulk-outsider')
        self.call('/sources/email/credentials',dict(address=support.ADDRESS,username='synthetic',password='Synthetic-only',consent=True),client=other)
        self.assertEqual(self.call('/sources/email/reprocess',body,client=other)[0],409)
        self.assertEqual(self.model.call_count,0)

    def test_failed_update_retains_previous_success_and_is_retryable(self):
        self.seed_range()
        original=email_import._request_id(self.ids[1])
        def failed(request,error):
            result=support.no_tasks(request,error); result['status']='failed'; result['record_results'][0]['status']='failed'
            result['errors']=[dict(input_id=self.ids[1],code='invalid_agent_output',message='synthetic',retryable=True)]
            return result
        self.model.side_effect=failed
        status,job=self.call('/sources/email/reprocess',self.payload())
        self.assertEqual(status,202,job); self.assertEqual(self.settled(job['job_id'])['state'],'failed')
        after=self.call('/sources/email/preview',dict(limit=61,cache_only=True))[1]
        self.assertEqual(after['recognition_updates']['outdated_count'],1)
        uid=self.call('/auth/me')[1]['user']['id']
        with app.connection() as db:
            self.assertEqual(email_import._item_state(db,uid,self.ids[1])[3],original)

    def test_range_update_has_no_explicit_id_cap_or_header_hydration(self):
        self.seed_range()
        uid=self.call('/auth/me')[1]['user']['id']
        with app.connection() as db:
            template=json.loads(db.execute('SELECT request FROM recognition_batches WHERE user_id=? LIMIT 1',(uid,)).fetchone()['request'])
            numbers=list(range(2000,3001))
            db.execute('UPDATE email_header_snapshots SET range_key=?,uids=? WHERE user_id=?',
                (json.dumps(dict(limit=2000,start_date=None),sort_keys=True,separators=(',',':')),json.dumps(numbers),uid))
            for number in numbers:
                request=copy.deepcopy(template); record=request['records'][0]
                record['input_id']=email_source._identity(support.ADDRESS,'77',number); record['source_item_id']='77:'+str(number)
                request['request_id']=email_import._request_id(record['input_id'])
                db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',(uid,request['request_id'],'synthetic',json.dumps(request),json.dumps(support.no_tasks(request,None))))
                db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?)',(uid,request['request_id'],record['input_id'],json.dumps(dict(version='old'))))
        calls=self.imap_calls
        status,preview=self.call('/sources/email/preview',dict(limit=2000,cache_only=True))
        self.assertEqual(status,200,preview); self.assertEqual(preview['recognition_updates']['outdated_count'],1001)
        self.assertEqual(self.imap_calls,calls)
        self.preview=preview
        body=self.payload('large-range'); body['range']['limit']=2000
        with patch.object(email_import,'_launch'):
            status,job=self.call('/sources/email/reprocess',body)
        self.assertEqual(status,202,job); self.assertEqual(job['counts']['selected'],1001)
        self.assertEqual(self.model.call_count,0)


if __name__ == "__main__":
    unittest.main()

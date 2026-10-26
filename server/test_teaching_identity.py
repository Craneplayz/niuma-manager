"""Source revisions must depend on content, not order or reader outcomes."""
from copy import deepcopy
import unittest
import json
import sqlite3
from contextlib import closing
from teaching_identity import source_digest, upgrade_pending_candidates, reuse_unchanged_result, CHANGED_SOURCE_REVIEW
from ddl_text.tests.test_teaching_pipeline import example


class SourceIdentityTests(unittest.TestCase):
    def test_result_reuse_ignores_only_attempt_identity_context_and_decision_order(self):
        from ddl_text.tests.test_course_context_boundaries import sample, compile_result, result_envelope
        request,task,_,_=sample()
        original=result_envelope(request,compile_result(request,dict(tasks=[task,dict(task,title='另一项独立行动')])))
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('''CREATE TABLE recognition_batches(user_id TEXT,request_id TEXT,result TEXT);
                CREATE TABLE teaching_submission_requests(user_id TEXT,account TEXT,request_id TEXT,input_id TEXT);''')
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?)',('u','old',json.dumps(original)))
            for rid in ('old','new'):db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)',('u','a',rid,'source'))
            result=deepcopy(original);result['request_id']='new'
            decisions=result['record_results'][0]['decisions'];decisions.reverse()
            for d in decisions:
                d['pair']['assessment']['candidate_id']='new:'+d['title']
                d['pair']['record']['course_context']['revision']='new-retrieval'
                d['pair']['record']['references'][0]['text']+=' More optional material'
            self.assertTrue(reuse_unchanged_result(db,'u','new',result))
            for field in ('title','description','deadline','evidence','primary','count','snapshot','filter'):
                changed=deepcopy(result);decision=changed['record_results'][0]['decisions'][0]
                if field in ('title','description'):decision[field]+=' changed'
                elif field=='deadline':decision['pair']['assessment']['deadline']['value']='2026-12-21'
                elif field=='evidence':decision['pair']['assessment']['action']['evidence'][0]['quote']+=' changed'
                elif field=='primary':decision['pair']['record']['text']+=' changed'
                elif field=='count':changed['record_results'][0]['decisions'].pop()
                elif field=='snapshot':changed['record_results'][0]['snapshot_id']='changed'
                elif field=='filter':decision['filter_reason']='New reason'
                with self.subTest(field=field):self.assertFalse(reuse_unchanged_result(db,'u','new',changed))
            for field,value in [('account','b'),('input_id','different'),('user_id','v')]:
                db.execute('UPDATE teaching_submission_requests SET '+field+'=? WHERE request_id=?',(value,'new'))
                self.assertFalse(reuse_unchanged_result(db,'u','new',result))
                db.execute('UPDATE teaching_submission_requests SET user_id=?,account=?,input_id=? WHERE request_id=?',('u','a','source','new'))
            # Equality is against the most recent result, not any old matching
            # result: a changed/latest attempt must not be silently discarded.
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?)',('u','intervening',json.dumps(changed)))
            db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)',('u','a','intervening','source'))
            self.assertFalse(reuse_unchanged_result(db,'u','new',result))

    def test_legacy_guard_upgrade_scopes_lineage_and_preserves_user_decisions(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('''CREATE TABLE recognition_candidates(id TEXT,user_id TEXT,request_id TEXT,
                candidate TEXT,state TEXT,version INTEGER,resolution TEXT);
                CREATE TABLE teaching_submission_requests(user_id TEXT,account TEXT,request_id TEXT,input_id TEXT);''')
            card = dict(notice_type='create', needs_review=True, review_reasons=[CHANGED_SOURCE_REVIEW])
            for name, uid, account, source, state, version, resolution, candidate in [
                ('old','u','a','s','applied',2,'manual',card),
                ('pending','u','a','s','pending',1,None,card),
                ('reopened','u','a','s','pending',3,None,card),
                ('ignored','u','a','s','ignored',2,'ignored',card),
                ('other-user','v','a','s','pending',1,None,card),
                ('other-account','u','b','s','pending',1,None,card),
                ('other-source','u','a','t','pending',1,None,card),
                ('unmarked','u','a','s','pending',1,None,dict(card, review_reasons=['Other reason']))]:
                db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)', (uid,account,name,source))
                db.execute('INSERT INTO recognition_candidates VALUES(?,?,?,?,?,?,?)',
                           (name,uid,name,json.dumps(candidate),state,version,resolution))
            before = db.execute('SELECT * FROM recognition_candidates ORDER BY id').fetchall()
            upgrade_pending_candidates(db)
            after = db.execute('SELECT * FROM recognition_candidates ORDER BY id').fetchall()
            for old, new in zip(before, after):
                if old[0] in ('pending','reopened'):
                    self.assertEqual(json.loads(new[3]), dict(card,notice_type='uncertain'))
                    self.assertEqual(old[:3]+old[4:], new[:3]+new[4:])
                else: self.assertEqual(old, new)
            upgrade_pending_candidates(db)
            self.assertEqual(after, db.execute('SELECT * FROM recognition_candidates ORDER BY id').fetchall())

    def test_order_read_status_and_collection_time_do_not_change_file_identity(self):
        entry=example()[0]['records'][0]
        entry['record']['references']=[dict(ref_id='file:1',label='A',kind='attachment',read_state='partial',text='Captured'),
                                       dict(ref_id='link:1',label='B',kind='linked_detail',read_state='unread',text=None)]
        files=[dict(ref_id='file:1',sha256='1'*64,mime_type='application/pdf')]
        before=source_digest(entry,None,files)
        other=deepcopy(entry);other['record']['collected_at']='2026-10-19T10:00:00+08:00'
        other['record']['references'][0].update(read_state='failed',text=None)
        other['record']['references'].reverse()
        self.assertEqual(before,source_digest(other,None,files))
        self.assertNotEqual(before,source_digest(other,None,[dict(files[0],sha256='2'*64)]))
        other['record']['text']+=' New requirement'
        self.assertNotEqual(before,source_digest(other,None,files))

    def test_missing_file_and_changed_assignment_are_not_hidden(self):
        entry=example()[0]['records'][0]
        files=[dict(ref_id='file:1',sha256='1'*64,mime_type='application/pdf')]
        entry['record']['references']=[dict(ref_id='file:1',label='A',kind='attachment',read_state='unread',text=None)]
        self.assertNotEqual(source_digest(entry,None,files),source_digest(entry,None,[]))
        self.assertNotEqual(source_digest(entry,{'state':'active'},files),source_digest(entry,{'state':'submitted'},files))

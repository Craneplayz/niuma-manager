"""Source revisions must depend on content, not order or reader outcomes."""
from copy import deepcopy
import unittest
import json
import sqlite3
from contextlib import closing
from teaching_identity import source_digest, upgrade_pending_candidates, CHANGED_SOURCE_REVIEW
from ddl_text.tests.test_teaching_pipeline import example


class SourceIdentityTests(unittest.TestCase):
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

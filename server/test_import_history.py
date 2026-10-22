"""Import history persists grouping and archives only the owner's candidates."""
import json
import sqlite3
import unittest
from types import SimpleNamespace

import recognition


class ImportHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        recognition.initialize(self.db)
        self.db.execute('CREATE TABLE workspace(user_id TEXT,name TEXT,value TEXT,version INTEGER)')
        self.db.execute('INSERT INTO workspace VALUES(?,?,?,?)', ('owner', 'tasks', '[{"id":"existing-task"}]', 1))
        for uid, rid in [('owner', 'first'), ('owner', 'second'), ('other', 'foreign')]:
            self.db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?,NULL)',
                            (uid, rid, 'fingerprint', json.dumps({'request_id': rid, 'records': []}), '{}', 0))
            self.db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',
                            (rid, uid, rid, '{}'))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def call(self, path, body):
        def reply(status, value):
            self.result = value
            self.db.commit()
        def error(status, message):
            return ValueError(message)
        with self.db:
            recognition.route(SimpleNamespace(command='POST', reply=reply), self.db, 'owner', path, body, error)

    def test_grouping_persists_and_archive_keeps_tasks_and_deduplication(self):
        self.call('/api/recognition/imports', dict(import_id='one-import', created_at='2026-10-09T13:00:00Z',
                  source='教学网', request_ids=['first', 'second']))
        self.assertEqual({b['importId'] for b in recognition.batches(self.db, 'owner')}, {'one-import'})
        self.call('/api/recognition/imports/archive', {'request_ids': ['first', 'second']})
        self.assertEqual(recognition.batches(self.db, 'owner'), [])
        self.assertEqual(len(recognition.batches(self.db, 'other')), 1)
        self.assertEqual(self.db.execute('SELECT value FROM workspace').fetchone()[0], '[{"id":"existing-task"}]')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recognition_batches WHERE user_id="owner"').fetchone()[0], 2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recognition_candidates WHERE user_id="owner" AND state="pending"').fetchone()[0], 0)

    def test_foreign_batch_cannot_be_archived_and_rolls_back_entire_request(self):
        with self.assertRaises(ValueError):
            self.call('/api/recognition/imports/archive', {'request_ids': ['first', 'foreign']})
        self.assertEqual(len(recognition.batches(self.db, 'owner')), 2)
        self.assertEqual(self.db.execute('SELECT state FROM recognition_candidates WHERE id="first"').fetchone()[0], 'pending')

class TaskEvidenceTests(ImportHistoryTests):
    def test_archived_evidence_remains_available_and_owned(self):
        task={'id':'task','recognitionHistory':[{'storageId':'first','action':'create'},{'storageId':'foreign','action':'create'}]}
        candidate={'input_id':'notice','evidence':[{'quote':'周五提交报告'}],'deadline':{'basis':'通知明确日期'},'sources':[]}
        record={'input_id':'notice','text':'请在周五提交报告。','source_url':'https://course.pku.edu.cn/notice/1','platform':'teaching_web'}
        self.db.execute('UPDATE workspace SET value=? WHERE user_id="owner"',(json.dumps([task]),))
        self.db.execute('UPDATE recognition_candidates SET candidate=? WHERE id="first"',(json.dumps(candidate),))
        self.db.execute('UPDATE recognition_batches SET request=? WHERE user_id="owner" AND request_id="first"',(json.dumps({'records':[record]}),))
        self.db.commit()
        self.call('/api/recognition/imports/archive',{'request_ids':['first']})
        self.call('/api/recognition/task-evidence',{'task_id':'task'})
        self.assertEqual(len(self.result['evidence']),1)
        self.assertEqual(self.result['evidence'][0]['original'],record['text'])
        self.assertEqual(self.result['evidence'][0]['sourceUrl'],record['source_url'])
        with self.assertRaises(ValueError):
            self.call('/api/recognition/task-evidence',{'task_id':'foreign-task'})

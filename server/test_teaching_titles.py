"""Async display-only work must never change task identity or user decisions."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import app
from ddl_text.adapters import AgentError
import source_jobs
import teaching
import teaching_titles as titles


class TeachingTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        with patch.object(app, 'DB', self.path):
            app.initialize(recover=False)
        self.uid = 'student'
        self.account = teaching.account('campus')
        with source_jobs.connection(self.path) as db:
            db.execute('INSERT INTO source_credentials VALUES(?,?,?)', (self.uid, 'campus', b'dummy'))
            db.execute("INSERT INTO workspace VALUES(?, 'tasks','[]',1)", (self.uid,))

    def published(self, db, cid, title='通知'):
        entry = dict(platform_item=dict(kind='announcement'), source=dict(label=title), record=dict(text=title+'\n闭卷考试安排请见教学网'))
        candidate = dict(title=title)
        draft = dict(title=title, description='original')
        db.execute('INSERT INTO teaching_sources VALUES(?,?,?,?,?,?)',
                   (self.uid, self.account, cid, 'digest', cid, '{}'))
        db.execute('INSERT INTO recognition_candidates VALUES(?,?,?,?,?,?,?)',
                   (cid,self.uid,cid,json.dumps(candidate),'applied',2,json.dumps(dict(auto_apply=True,draft=draft))))
        tasks, _ = titles.submission_runtime.workspace(db,self.uid)
        tasks.append(dict(id=cid, title=title, status='not_started', source='教学网', recognitionHistory=[dict(storageId=cid)]))
        db.execute("UPDATE workspace SET value=? WHERE user_id=? AND name='tasks'", (json.dumps(tasks),self.uid))
        titles.enqueue(db,self.uid,entry,cid,title)

    def test_service_worker_reads_committed_jobs_and_keeps_source_on_unlink(self):
        entered, release = threading.Event(), threading.Event()
        def waiting(_):
            entered.set()
            release.wait(5)
            return '闭卷考试安排'

        with source_jobs.connection(self.path) as db:
            self.published(db,'one')
        with patch.object(titles.runtime_environment,'allow_background_recovery',return_value=True), \
             patch.object(titles,'polish',side_effect=waiting):
            stop = titles.start_worker(self.path)
            try:
                self.assertTrue(entered.wait(5))
                with source_jobs.connection(self.path) as db:
                    self.assertEqual(titles.submission_runtime.workspace(db,self.uid)[0][0]['title'],'通知')
                    db.execute('DELETE FROM source_credentials WHERE user_id=?',(self.uid,))
                release.set()
                limit = time.monotonic() + 5
                while time.monotonic() < limit:
                    with source_jobs.connection(self.path) as db:
                        result = db.execute('SELECT result FROM teaching_title_jobs').fetchone()[0]
                    if result is not None:
                        break
                    time.sleep(.01)
                self.assertIsNotNone(result)
            finally:
                release.set()
                stop.set()
        with source_jobs.connection(self.path) as db:
            self.assertEqual(titles.submission_runtime.workspace(db,self.uid)[0][0]['title'],'通知')

    def test_real_publication_queues_only_after_creating_the_task(self):
        capture = json.loads((teaching.ROOT/'contracts/examples/teaching-platform-native.json').read_text())
        entry, = capture['request']['records']
        heading = '请各位同学注意本次实验的个别要求，以及提交时应当一起检查的最终材料和截止前需完成的全部操作'
        heading += '（完整详细说明请见正文）'
        entry['source']['label'] = heading
        entry['record']['text'] = heading + '\n' + entry['record']['text']
        with source_jobs.connection(self.path) as db:
            view, = teaching.register(db,self.uid,'campus',[capture])['requests']
        with patch.object(titles,'polish',side_effect=AssertionError('must not block publication')), source_jobs.connection(self.path) as db:
            teaching.run(db,self.uid,dict(contract_version=teaching.RUN,request_id=view['request_id']),False,app.ApiError)
            task, = titles.submission_runtime.workspace(db,self.uid)[0]
            self.assertEqual(task['title'],heading)
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_title_jobs').fetchone()[0],1)
        with patch.object(titles,'polish',return_value='本次实验的个别要求'):
            self.assertTrue(titles.process_one(self.path))
        with source_jobs.connection(self.path) as db:
            task, = titles.submission_runtime.workspace(db,self.uid)[0]
            self.assertEqual(task['title'],'本次实验的个别要求')
            self.assertEqual(teaching.register(db,self.uid,'campus',[capture])['requests'],[])
            self.assertIsNotNone(task['due'])

    def test_publish_first_then_cache_reuse_and_baseline(self):
        with source_jobs.connection(self.path) as db:
            self.published(db,'first')
            self.published(db,'second')
            self.published(db,'short','第3章作业')
            tasks, _ = titles.submission_runtime.workspace(db,self.uid)
            self.assertEqual([t['title'] for t in tasks],['通知','通知','第3章作业'])
        with patch.object(titles,'polish',return_value='闭卷考试安排') as model:
            self.assertTrue(titles.process_one(self.path))
            with source_jobs.connection(self.path) as db:
                self.published(db,'third')
                tasks, _ = titles.submission_runtime.workspace(db,self.uid)
                self.assertEqual([t['title'] for t in tasks],['闭卷考试安排','闭卷考试安排','第3章作业','闭卷考试安排'])
                resolution=json.loads(db.execute("SELECT resolution FROM recognition_candidates WHERE id='first'").fetchone()[0])
                self.assertEqual(resolution['source_draft']['title'],'闭卷考试安排')
            self.assertFalse(titles.process_one(self.path))
            model.assert_called_once()

    def test_manual_and_stale_items_are_preserved_during_model_call(self):
        with source_jobs.connection(self.path) as db:
            for cid in ['edited','deleted','complete','updated','review']:
                self.published(db,cid)
        def concurrent_edit(_):
            with source_jobs.connection(self.path) as db:
                tasks, _ = titles.submission_runtime.workspace(db,self.uid)
                tasks[0]['title']='我改的标题'
                tasks[1]['recycleBin']={'at':'now'}
                tasks[2]['status']='completed'
                tasks[3]['recognitionHistory'].append(dict(storageId='later'))
                db.execute('INSERT INTO teaching_submission_sources VALUES(?,?,?,?,?,?)',
                           (self.uid,self.account,'review','active','now',1))
                db.execute("UPDATE workspace SET value=?,version=13 WHERE user_id=? AND name='tasks'", (json.dumps(tasks),self.uid))
            return '闭卷考试安排'
        with patch.object(titles,'polish',side_effect=concurrent_edit):
            self.assertTrue(titles.process_one(self.path))
        with source_jobs.connection(self.path) as db:
            tasks, version=titles.submission_runtime.workspace(db,self.uid)
            self.assertEqual(version,13)
            self.assertEqual([t['title'] for t in tasks],['我改的标题','通知','通知','通知','通知'])

    def test_retry_keeps_original_and_reclaims_expired_work(self):
        with source_jobs.connection(self.path) as db:
            self.published(db,'one')
        with patch.object(titles,'polish',side_effect=AgentError('agent_timeout',True)):
            self.assertTrue(titles.process_one(self.path))
        self.assertFalse(titles.process_one(self.path))
        with source_jobs.connection(self.path) as db:
            tasks, _ = titles.submission_runtime.workspace(db,self.uid)
            self.assertEqual(tasks[0]['title'],'通知')
            db.execute('UPDATE teaching_title_jobs SET available=0')
        with patch.object(titles,'polish',return_value='闭卷考试安排'):
            self.assertTrue(titles.process_one(self.path))
        with source_jobs.connection(self.path) as db:
            self.assertEqual(titles.submission_runtime.workspace(db,self.uid)[0][0]['title'],'闭卷考试安排')

    def test_source_fields_limit_model_and_untrusted_response(self):
        generic = dict(platform_item=dict(kind='announcement'),source=dict(label='通知'),record=dict(text='通知\n期中考试取消'))
        prompt=titles.title_prompt(generic,'通知')
        self.assertEqual(prompt['excerpt'],'期中考试取消')
        self.assertIsNone(titles.title_prompt(generic,'期中考试取消'))
        self.assertIsNone(titles.title_prompt(dict(generic,platform_item={}), '通知'))
        with patch.object(titles,'live_options',return_value=dict(provider='qiniu',key='dummy',model='flash',endpoint='https://api.qnaigc.com/v1')), \
             patch.object(titles.operations,'observe_chat') as chat:
            for proposed, expected in [('期中考试',''),('新编标题',''),('期中考试取消','期中考试取消')]:
                chat.return_value.complete_json.return_value=json.dumps(dict(title=proposed))
                self.assertEqual(titles.polish(prompt),expected)
                self.assertEqual(chat.return_value.complete_json.call_args.args[2].max_output_tokens,160)

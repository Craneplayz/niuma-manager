"""SQLite projections, immutable drafts and the authenticated context endpoint."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from contracts import relation_foundation as contract
import relation_foundation as foundation
import teaching


def seed(db, uid='u', username='synthetic-course-user'):
    """Only the real persistence boundary is replaced, not foundation behavior."""
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE source_credentials(user_id TEXT PRIMARY KEY,username TEXT,secret BLOB);
        CREATE TABLE workspace(user_id TEXT,name TEXT,value TEXT,version INTEGER,PRIMARY KEY(user_id,name));
        CREATE TABLE teaching_sources(user_id TEXT,account TEXT,input_id TEXT,digest TEXT,request_id TEXT,request TEXT);
        CREATE TABLE teaching_asset_files(user_id TEXT,account TEXT,sha256 TEXT,mime_type TEXT,data BLOB,cache_version TEXT,result TEXT);
        CREATE TABLE teaching_asset_refs(user_id TEXT,account TEXT,request_id TEXT,ref_id TEXT,sha256 TEXT,mime_type TEXT);
        CREATE TABLE recognition_candidates(id TEXT,user_id TEXT,request_id TEXT,candidate TEXT,state TEXT,version INTEGER,resolution TEXT);
        CREATE TABLE teaching_submission_requests(user_id TEXT,account TEXT,request_id TEXT,input_id TEXT);
        CREATE TABLE recognition_batches(user_id TEXT,request_id TEXT,request TEXT,result TEXT);
        CREATE TABLE submission_controls(record_id TEXT,user_id TEXT,account TEXT,input_id TEXT,detail TEXT);
        CREATE TABLE submission_operations(user_id TEXT,audit TEXT);
    ''')
    foundation.initialize(db)
    account = teaching.account(username)
    db.execute('INSERT INTO source_credentials VALUES(?,?,?)', (uid, username, b'not-used'))
    for iid, course, text in [('six', 'lecture', '第6讲作业题'), ('seven', 'lecture', '第7讲作业题'),
                               ('submit', 'seminar', '第6—7讲作业：只提交作业题，不用做研讨题')]:
        r = dict(input_id=iid, course_id=course, source_item_id=iid, snapshot_id='snapshot:' + iid,
                 course_term=dict(term_id='2026-fall', starts_on='2026-09-01', ends_on='2027-02-28'), text=text, body_status='complete', references=[])
        entry = dict(record=r, source=dict(label=text))
        req = json.dumps(dict(records=[entry]))
        db.execute('INSERT INTO teaching_sources VALUES(?,?,?,?,?,?)', (uid, account, iid, 'content', iid, req))
        db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)', (uid, account, iid, iid))
        db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?)', (uid, iid, req, '{}'))
    obs = dict(snapshot=dict(source_account_id=account, input_id='submit', course_id='seminar', content_id='submit',
                             state='submitted', basis='review_submission_history_title', snapshot_id='native:1', sequence=1),
               control_revision=0, review_pending=False)
    db.execute('INSERT INTO submission_controls VALUES(?,?,?,?,?)', ('native', uid, account, 'submit', json.dumps(dict(observation=obs))))
    db.execute('INSERT INTO recognition_candidates VALUES(?,?,?,?,?,?,?)', ('c6', uid, 'six', json.dumps(dict(input_id='six', title='第六讲')), 'pending', 1, None))
    db.commit()
    return account


def bundle(ctx):
    by_input = {s['input_id']: s for s in ctx['sources']}
    def quote(s):
        p = s['parts'][0]
        return dict(source_id=s['id'], part_id=p['part_id'], part_revision=p['revision'], offset=0, quote=p['text'])
    materials = [by_input['six'], by_input['seven']]
    receiver = by_input['submit']
    return dict(contract_version=contract.VERSION, context_revision=ctx['revision'], kind='bundle',
        source_ids=[s['id'] for s in ctx['sources']],
        roles=[dict(source_id=s['id'], role='submission' if s['submission'] else 'content', evidence=[quote(s)]) for s in ctx['sources']],
        units=[dict(id='unit:' + s['input_id'], label=s['label'], channel='书面', action_ids=[], evidence=[quote(s)]) for s in materials],
        evidence=[quote(s) for s in ctx['sources']], deadline=None,
        coverage=[dict(source_id=receiver['id'], unit_ids=['unit:six', 'unit:seven'], extent='complete', evidence=[quote(receiver)])])


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.account = seed(self.db)

    def context(self):
        return foundation.context(self.db, 'u', self.account, ['six', 'seven', 'submit'])

    def save(self, ctx=None, relation=None, op='op'):
        ctx = ctx or self.context()
        if not self.db.in_transaction: self.db.execute('BEGIN IMMEDIATE')
        return foundation.record_proposal(self.db, 'u', self.account, op, ctx, relation or bundle(ctx))

    def test_submitted_source_with_zero_tasks_is_present(self):
        ctx = self.context()
        submit = next(s for s in ctx['sources'] if s['input_id'] == 'submit')
        self.assertEqual(submit['submission']['state'], 'submitted')
        self.assertFalse(any(submit['id'] in a['source_ids'] for a in ctx['actions']))
        self.assertEqual(len(ctx['sources']), 3)

    def test_repeated_context_does_not_write_or_depend_on_selection_order(self):
        before = self.db.total_changes
        ctx = self.context()
        second = foundation.context(self.db, 'u', self.account, ['submit', 'seven', 'six'])
        self.assertEqual(ctx, second)
        self.assertEqual(self.db.total_changes, before)

    def test_title_edits_keep_identity_but_invalidate_revision(self):
        before = self.context()
        self.db.execute('UPDATE recognition_candidates SET candidate=?,version=2', (json.dumps(dict(input_id='six', title='编辑后的标题')),))
        after = self.context()
        self.assertEqual(before['actions'][0]['id'], after['actions'][0]['id'])
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertTrue(after['actions'][0]['protected'])

    def test_snapshot_change_keeps_source_identity(self):
        before = self.context()
        self.change_source('six', snapshot_id='new-snapshot')
        after = self.context()
        self.assertEqual([s['id'] for s in before['sources']], [s['id'] for s in after['sources']])
        self.assertNotEqual(before['revision'], after['revision'])

    def test_candidate_record_must_match_source_even_when_request_is_shared(self):
        self.db.execute('INSERT INTO recognition_candidates VALUES(?,?,?,?,?,?,?)',
                        ('unrelated', 'u', 'six', json.dumps(dict(input_id='different', title='同名作业')), 'pending', 1, None))
        self.assertEqual([a['binding_id'] for a in self.context()['actions']], ['c6'])

    def change_source(self, iid, **changes):
        row = self.db.execute('SELECT request FROM teaching_sources WHERE input_id=?', (iid,)).fetchone()
        request = json.loads(row[0]); request['records'][0]['record'].update(changes)
        self.db.execute('UPDATE teaching_sources SET request=? WHERE input_id=?', (json.dumps(request), iid))

    def test_no_title_matching_and_explicit_candidate_promotion(self):
        self.db.execute("UPDATE recognition_candidates SET state='applied'")
        task = dict(id='task1', title='完全不同的标题', status='not_started', recognitionHistory=[dict(storageId='c6')])
        self.db.execute('INSERT INTO workspace VALUES(?,?,?,?)', ('u', 'tasks', json.dumps([task]), 1))
        ctx = self.context()
        self.assertEqual(len(ctx['actions']), 2)
        self.assertEqual(len(ctx['transitions']), 1)
        task['recognitionHistory'] = []
        self.db.execute('UPDATE workspace SET value=?', (json.dumps([task]),))
        self.assertEqual(len(self.context()['actions']), 1)

    def test_ignored_and_deleted_decisions_are_not_reactivated(self):
        self.db.execute("UPDATE recognition_candidates SET state='ignored',version=2")
        ctx = self.context()
        self.assertEqual(ctx['actions'][0]['state'], 'ignored')
        self.save(ctx)
        self.assertEqual(self.db.execute('SELECT state FROM recognition_candidates').fetchone()[0], 'ignored')
        self.assertFalse(self.db.execute('SELECT * FROM workspace').fetchall())

    def test_store_audit_idempotency_restart_and_no_execution(self):
        before = list(self.db.execute('SELECT * FROM recognition_candidates'))
        result = self.save()
        self.db.commit(); foundation.initialize(self.db)
        self.assertEqual(self.save(), result)
        self.assertEqual(result['state'], 'proposed')
        self.assertFalse(result['application_allowed'])
        saved = foundation.read_proposal(self.db, 'u', self.account, result['id'])
        self.assertEqual(saved['audit'][0]['task_mutations'], 0)
        self.assertEqual(len(saved['audit']), 1)
        self.assertEqual(list(self.db.execute('SELECT * FROM recognition_candidates')), before)

    def test_same_operation_different_relation_conflicts(self):
        self.save()
        ctx = self.context(); rel = bundle(ctx); rel['coverage'][0]['extent'] = 'partial'
        with self.assertRaisesRegex(foundation.FoundationError, '操作标识'):
            self.save(ctx, rel)

    def test_atomic_audit_failure_rolls_back_proposal(self):
        self.db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON relation_foundation_events BEGIN SELECT RAISE(ABORT,'forced'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.save()
        self.assertEqual(self.db.execute('SELECT count(*) FROM relation_foundation_proposals').fetchone()[0], 0)

    def test_stale_source_action_and_submission_are_rejected(self):
        for change in ('source', 'action', 'submission'):
            ctx = self.context()
            if change == 'source': self.change_source('six', text='修改后的内容')
            elif change == 'action': self.db.execute('UPDATE recognition_candidates SET version=version+1')
            else:
                obs = json.loads(self.db.execute('SELECT detail FROM submission_controls').fetchone()[0])
                obs['observation']['control_revision'] += 1
                self.db.execute('UPDATE submission_controls SET detail=?', (json.dumps(obs),))
            with self.assertRaisesRegex(foundation.FoundationError, '已变化'): self.save(ctx, op=change)

    def test_other_account_user_and_source_do_not_leak(self):
        for uid, account, inputs in [('other', self.account, ['six']), ('u', 'other', ['six']), ('u', self.account, ['absent'])]:
            with self.assertRaises(foundation.FoundationError) as error:
                foundation.context(self.db, uid, account, inputs)
            self.assertEqual(error.exception.code, 'not_found')

    def test_missing_or_mixed_semester_produces_visible_warning_not_relation(self):
        for value in (None, dict(term_id='2027-fall')):
            self.change_source('six', course_term=value)
            ctx = self.context()
            self.assertIsNone(ctx['term_id'])
            self.assertIn('semester_unresolved', ctx['warnings'])
            with self.assertRaisesRegex(ValueError, 'semester'): self.save(ctx)

    def test_reused_input_identity_does_not_inherit_old_actions_or_submissions(self):
        self.change_source('six', course_id='new-course')
        self.change_source('submit', source_item_id='new-content')
        ctx = self.context()
        self.assertEqual(ctx['actions'], [])
        self.assertIsNone(next(s for s in ctx['sources'] if s['input_id'] == 'submit')['submission'])

    def test_completed_file_cache_preserves_bytes_identity_and_read_state(self):
        self.change_source('six', references=[dict(ref_id='f', kind='attachment', read_state='unread', text=None)])
        self.db.execute('INSERT INTO teaching_asset_files VALUES(?,?,?,?,?,?,?)', ('u', self.account, 'a'*64, 'application/pdf', b'pdf', 'reader-v1', json.dumps(dict(read_state='complete', text='附件真实读取结果'))))
        self.db.execute('INSERT INTO teaching_asset_refs VALUES(?,?,?,?,?,?)', ('u', self.account, 'six', 'f', 'a'*64, 'application/pdf'))
        p = next(s for s in self.context()['sources'] if s['input_id'] == 'six')['parts'][1]
        self.assertEqual((p['text'], p['sha256'], p['read_state']), ('附件真实读取结果', 'a'*64, 'complete'))

    def test_partial_or_large_material_is_not_claimed_fully_read(self):
        self.change_source('six', text='x'*25000)
        p = next(s for s in self.context()['sources'] if s['input_id'] == 'six')['parts'][0]
        self.assertEqual((len(p['text']), p['read_state']), (24000, 'partial'))

    def test_borrowed_course_context_is_not_laundered_as_own_evidence(self):
        self.change_source('six', references=[dict(ref_id='borrowed', kind='course_context', text='外部材料', read_state='complete')])
        self.assertEqual(len(next(s for s in self.context()['sources'] if s['input_id'] == 'six')['parts']), 1)

    def test_manual_unresolved_association_protects_even_unchanged_candidate(self):
        import submission_runtime
        op = dict(kind='associate_actions', links=[dict(action_id=submission_runtime.identity('candidate', 'c6'), relation='unknown')])
        self.db.execute('INSERT INTO submission_operations VALUES(?,?)', ('u', json.dumps(dict(operation=op))))
        self.assertTrue(self.context()['actions'][0]['protected'])

    def test_incomplete_task_history_cannot_authorize_relation(self):
        self.db.execute("UPDATE recognition_candidates SET state='applied'")
        task = dict(id='task1', title='组合任务', status='not_started', recognitionHistory=[dict(storageId='c6'), dict(storageId='external')])
        self.db.execute('INSERT INTO workspace VALUES(?,?,?,?)', ('u', 'tasks', json.dumps([task]), 1))
        ctx = self.context(); rel = bundle(ctx)
        rel['units'][0]['action_ids'] = [next(a['id'] for a in ctx['actions'] if a['kind'] == 'task')]
        with self.assertRaisesRegex(ValueError, 'incomplete task'): self.save(ctx, rel)

    def test_semester_window_conflict_is_visible_and_blocks_proposal(self):
        self.change_source('six', course_term=dict(term_id='2026-fall', starts_on='2026-09-02', ends_on='2027-02-28'))
        ctx = self.context()
        self.assertIn('semester_window_unresolved', ctx['warnings'])
        with self.assertRaisesRegex(ValueError, 'semester window'): self.save(ctx)

    def test_read_proposal_rechecks_owner_after_account_switch(self):
        value = self.save()
        self.db.execute("UPDATE source_credentials SET username='replacement'")
        with self.assertRaises(foundation.FoundationError) as error:
            foundation.read_proposal(self.db, 'u', self.account, value['id'])
        self.assertEqual(error.exception.code, 'not_found')


class FoundationHTTPTests(unittest.TestCase):
    import test_teaching as harness
    tearDown = harness.TeachingIntegrationTests.tearDown
    call = harness.TeachingIntegrationTests.call
    capture = harness.TeachingIntegrationTests.capture

    def setUp(self):
        import app, tempfile, threading
        from pathlib import Path
        from urllib.request import build_opener, HTTPCookieProcessor
        from http.cookiejar import CookieJar
        self.tmp = tempfile.TemporaryDirectory(); self.old_db = app.DB
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, app, 'DB', self.old_db)
        app.DB = Path(self.tmp.name) / 'test.sqlite3'
        app.initialize(recover=False)  # Register a genuinely empty managed data directory first.
        crawler = Path(self.tmp.name) / 'crawler'
        (crawler / 'node_modules' / 'tsx').mkdir(parents=True)
        self.crawler_patch = patch.object(app.source_sync, 'CRAWLER', crawler)
        self.crawler_patch.start(); self.addCleanup(self.crawler_patch.stop)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}/api'
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.call('/auth/register', dict(name='Synthetic', email='foundation@example.test', password='Synthetic-password!'))[0], 200)
        self.assertEqual(self.call('/sources/credentials', dict(username='synthetic-course-user', password='synthetic-password'))[0], 200)
        with app.connection() as db: self.uid = db.execute('SELECT id FROM users').fetchone()['id']
        self.drafts = {}

    def test_actual_registration_http_projection_and_foreign_account(self):
        import app
        request, _ = self.capture(('explicit-complete',))
        ids = [e['record']['input_id'] for e in request['records']]
        body = dict(source_account_id=teaching.account('synthetic-course-user'), input_ids=ids)
        with patch.object(teaching, 'recognize', side_effect=AssertionError('read must not call model')):
            status, result = self.call('/recognition/relation-context', body)
        self.assertEqual(status, 200, result)
        contract.check('context', result)
        self.assertEqual(result['actions'], [])
        self.assertEqual(self.call('/recognition/relation-context', dict(body, source_account_id='foreign'))[0], 404)
        self.assertEqual(self.call('/recognition/relation-context', dict(body, apply=True))[0], 400)
        self.assertEqual(self.call('/recognition/relation-context', dict(body, input_ids=[]))[0], 400)
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM relation_foundation_proposals').fetchone()[0], 0)

    def test_unauthenticated_context_is_rejected(self):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        req = Request(self.base + '/recognition/relation-context', b'{}', {'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as error: urlopen(req)
        self.assertEqual(error.exception.code, 401)

    def test_reset_clears_only_current_account_relation_audit(self):
        import app
        with app.connection() as db:
            for uid in (self.uid, 'other-user'):
                db.execute('INSERT INTO relation_foundation_proposals VALUES(?,?,?,?,?,?,?,?)',
                           (uid, uid, 'account', 'op', 'digest', '{}', '{}', 'now'))
                db.execute('INSERT INTO relation_foundation_events VALUES(?,?,?,?,?,?)', (uid, uid, uid, 'account', '{}', 'now'))
        status, result = self.call('/environment-tools/reset-account', dict(confirm=True, accountId=self.uid))
        self.assertEqual(status, 200, result)
        with app.connection() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT user_id FROM relation_foundation_proposals')], ['other-user'])
            self.assertEqual([r[0] for r in db.execute('SELECT user_id FROM relation_foundation_events')], ['other-user'])

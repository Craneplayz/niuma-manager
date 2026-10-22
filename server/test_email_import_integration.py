"""Synthetic HTTP acceptance checks for the persistent email import flow.

These tests use a temporary database and fake IMAP/model implementations; they
never connect to the user's mailbox or to the blocked manual-test browser origin.
"""
import email_test_support as support
import json
import unittest
from unittest.mock import patch
from urllib.parse import quote

import app
import email_import
import source_sync
import email_source


from email_test_support import ADDRESS, no_tasks


class MixedContentClient:
    def generate(self, instructions, record_json, settings, attempt):
        record = json.loads(record_json)
        return json.dumps({'tasks': [dict(title=title, content_type=kind,
            description=title, prerequisites=[], notice_type='create', target=None, course=None,
            evidence=[dict(input_id=record['input_id'],quote=quote)],times=[],review_reasons=[])
            for title,kind,quote in [('上课地点调整到理教408','notice','上课地点调整到理教408。'),
                                     ('回复参加确认','task','请回复确认是否参加。')]]},ensure_ascii=False)

class EmailImportAcceptanceTests(support.EmailHTTPTestCase):


    def test_encoded_job_identifiers_support_read_stop_retry_and_account_isolation(self):
        input_id = email_source._identity(ADDRESS, '77', 61)
        def first_failure(request, error):
            result = no_tasks(request, error)
            result.update(status='failed', errors=[dict(input_id=input_id, code='invalid_agent_output',
                message='Synthetic invalid output', retryable=True)])
            result['record_results'][0]['status'] = 'failed'
            return result
        self.model.side_effect = first_failure
        job = self.settled(self.start([input_id], 'encoded-original')['job_id'])
        self.assertEqual(job['state'], 'failed', job)
        path = '/sources/email/jobs/' + quote(job['job_id'], safe='')
        self.assertIn('%3A', path)
        self.assertEqual(self.call(path)[0], 200)
        self.assertEqual(self.call(path + '/stop', {})[0], 200)
        outsider = self.account('encoded-outsider')
        self.assertEqual(self.call(path, client=outsider)[0], 404)
        self.assertEqual(self.call(path + '/retry', dict(operation_id='other'), client=outsider)[0], 404)
        self.model.side_effect = no_tasks
        status, retry = self.call(path + '/retry', dict(operation_id='encoded-retry'))
        self.assertEqual(status, 202, retry)
        final = self.settled(retry['job_id'])
        self.assertEqual(final['state'], 'succeeded', final)
        self.assertEqual(final['counts']['processed'], 1)
        self.assertEqual(self.model.call_count, 2)

    def test_header_selection_exclusion_persistent_no_task_cache_and_isolation(self):
        status, preferences = self.call('/sources/email/preferences')
        self.assertEqual(status, 200)
        self.assertTrue(preferences['consent_granted'])
        self.assertFalse(preferences['process_all'])
        status, preview = self.call('/sources/email/preview', dict(limit=50, offset=0, page_size=50))
        self.assertEqual(status, 200, preview)
        self.assertEqual(len(preview['messages']), 50)
        self.assertTrue(all('HEADER.FIELDS' in q for _, q in self.imap.fetches))
        excluded, selected = [m['input_id'] for m in preview['messages'][:2]]
        status, _ = self.call('/sources/email/exclusions', dict(input_ids=[excluded], excluded=True))
        self.assertEqual(status, 200)
        job = self.settled(self.start([excluded, selected], 'first')['job_id'])
        self.assertEqual(job['state'], 'succeeded', job)
        self.assertEqual(job['counts']['excluded'], 1)
        self.assertEqual(job['counts']['processed'], 1)
        self.assertEqual(self.model.call_count, 1)
        body_uids = [uid for uid, q in self.imap.fetches if 'HEADER.FIELDS' not in q]
        self.assertNotIn(int(excluded.rsplit(':', 1)[1]), body_uids)
        calls_after_first = self.imap_calls
        repeat = self.settled(self.start([excluded, selected], 'repeat')['job_id'])
        self.assertEqual(repeat['counts']['cached'], 1, repeat)
        self.assertEqual(self.imap_calls, calls_after_first, 'completed mail must skip IMAP entirely')
        self.assertEqual(self.model.call_count, 1)
        status, preview = self.call('/sources/email/preview', dict(limit=50))
        self.assertEqual(status, 200)
        states = {m['input_id']: m['state'] for m in preview['messages']}
        self.assertEqual(states[excluded], 'excluded')
        self.assertEqual(states[selected], 'no_tasks')
        self.assertEqual(self.call('/sources/email/exclusions', dict(input_ids=[excluded], excluded=False))[0], 200)
        restored = self.call('/sources/email/preview', dict(limit=50))[1]
        restored_state = next(m for m in restored['messages'] if m['input_id'] == excluded)
        self.assertFalse(restored_state['excluded'])
        self.assertEqual(restored_state['state'], 'unprocessed')
        other = self.account('other')
        self.assertEqual(self.call('/sources/email/jobs/' + job['job_id'], client=other)[0], 404)
        self.assertFalse(self.call('/sources/email/preferences', client=other)[1]['consent_granted'])
        status, workspace = self.call('/workspace')
        self.assertEqual(status, 200)
        self.assertFalse(workspace.get('tasks', {}).get('value', []))

    def test_legacy_success_cache_is_reused_without_imap_or_model(self):
        input_id = email_source._identity(ADDRESS, '77', 61)
        records, _ = email_source.fetch_selected(ADDRESS, 'synthetic', 'synthetic', [input_id])
        uid = self.call('/auth/me')[1]['user']['id']
        with app.connection() as db:
            response = source_sync.incremental_requests(db, uid, ADDRESS,
                [dict(contract_version='0.2', request_id='old-selection', records=records)], source='email')
            request = response['requests'][0]
            import hashlib
            fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',
                (uid, request['request_id'], fingerprint, json.dumps(request), json.dumps(no_tasks(request, None))))
        before = self.imap_calls
        job = self.settled(self.start([input_id], 'legacy-cache')['job_id'])
        self.assertEqual(job['state'], 'succeeded', job)
        self.assertEqual(job['counts']['cached'], 1)
        self.assertEqual(job['items'][0]['request_id'], request['request_id'])
        self.assertEqual(self.imap_calls, before)
        self.assertEqual(self.model.call_count, 0)
        # The legacy reprocessing catalog must not offer a force-recognition bypass.
        status, catalog = self.call('/recognition/reprocessing/sources')
        self.assertEqual(status, 200)
        self.assertFalse(any(r['input_id'] == input_id for a in catalog['accounts'] for r in a['records']))
        with app.connection() as db:
            account = __import__('hashlib').sha256(ADDRESS.encode()).hexdigest()
            source = db.execute('SELECT source_id FROM reprocessing_sources WHERE user_id=? AND account=?',
                                (uid, account)).fetchone()
        status, _ = self.call('/recognition/reprocessing/runs', dict(contract_version='reprocessing/0.1',
            operation_id='force-legacy-email', source_account_id=source['source_id'], input_ids=[input_id]))
        self.assertEqual(status, 400)
        self.assertEqual(self.model.call_count, 0)

    def test_range_job_processes_more_than_fifty_and_retries_only_failures(self):
        fail_once = {email_source._identity(ADDRESS, '77', 61)}
        calls = []
        def model(request, error):
            input_id = request['records'][0]['input_id']
            calls.append(input_id)
            if input_id in fail_once:
                fail_once.remove(input_id)
                raise error(503, 'Synthetic temporary failure')
            return no_tasks(request, error)
        self.model.side_effect = model
        status, initial = self.call('/sources/email/jobs',
            dict(operation_id='range', range=dict(limit=61, start_date=None)))
        self.assertEqual(status, 202, initial)
        # This exercises 61 real SQLite commits, not an import latency target.
        job = self.settled(initial['job_id'], timeout=30)
        self.assertEqual(job['counts']['selected'], 61, job)
        self.assertEqual(job['counts']['failed'], 1, job)
        self.assertEqual(job['counts']['processed'], 60, job)
        status, retry = self.call('/sources/email/jobs/' + job['job_id'] + '/retry',
                                  dict(operation_id='range-retry'))
        self.assertEqual(status, 202, retry)
        final = self.settled(retry['job_id'])
        self.assertEqual(final['state'], 'succeeded', final)
        self.assertEqual(len(calls), 62, 'retry must call model only for the failed message')
        status, duplicate = self.call('/sources/email/jobs/' + job['job_id'] + '/retry',
                                      dict(operation_id='range-retry'))
        self.assertIn(status, (200, 202), duplicate)
        self.assertEqual(duplicate['job_id'], retry['job_id'])
        self.assertEqual(len(calls), 62)

    def test_replayed_operation_cannot_change_selection(self):
        first = email_source._identity(ADDRESS, '77', 61)
        second = email_source._identity(ADDRESS, '77', 60)
        job = self.settled(self.start([first], 'same-operation')['job_id'])
        status, replay = self.call('/sources/email/jobs', dict(operation_id='same-operation', input_ids=[first]))
        self.assertIn(status, (200, 202), replay)
        self.assertEqual(replay['job_id'], job['job_id'])
        status, _ = self.call('/sources/email/jobs', dict(operation_id='same-operation', input_ids=[second]))
        self.assertEqual(status, 409, 'idempotency keys must not hide a changed selection')
        self.assertEqual(self.model.call_count, 1)

    def test_mixed_email_content_types_are_reviewed_saved_and_cached(self):
        from ddl_text.extraction import ExtractionAdapter
        from ddl_text.pipeline import process_request
        from ddl_text.config import Settings
        self.imap.body = '上课地点调整到理教408。请回复确认是否参加。'.encode()
        self.model.side_effect = lambda request, _: process_request(request,ExtractionAdapter(MixedContentClient()),Settings())
        input_id = email_source._identity(ADDRESS,'77',61)
        job = self.settled(self.start([input_id],'mixed-types')['job_id'])
        self.assertEqual(job['state'],'succeeded',job)
        items = self.call('/recognition/batches')[1][0]['items']
        self.assertEqual([i['candidate']['content_type'] for i in items],['notice','task'])
        self.assertTrue(all(i['state']=='pending' for i in items))
        self.assertFalse(self.call('/workspace')[1].get('tasks',{}).get('value',[]))
        task = next(item for item in items if item['candidate']['content_type']=='task')
        path = '/recognition/candidates/'+task['id']+'/resolve'
        self.assertEqual(self.call(path,dict(action='create',auto_apply=True,version=task['version']))[0],400)
        self.assertEqual(self.call(path,dict(action='ignore',version=task['version']))[0],200)
        calls = (self.imap_calls,self.model.call_count)
        ignored = self.settled(self.start([input_id],'ignored-cached')['job_id'])
        self.assertEqual((ignored['items'][0]['state'],ignored['items'][0]['task_count']),('has_tasks',2))
        self.assertEqual((self.imap_calls,self.model.call_count),calls)
        task = next(item for item in self.call('/recognition/batches')[1][0]['items'] if item['id']==task['id'])
        self.assertEqual(task['state'],'ignored')
        self.assertEqual(self.call(path,dict(action='reopen',version=task['version']))[0],200)
        items = self.call('/recognition/batches')[1][0]['items']
        for item in items:
            workspace = self.call('/workspace')[1]
            version = workspace.get('tasks',{}).get('version',0)
            c = item['candidate']
            status,result = self.call('/recognition/candidates/'+item['id']+'/resolve',dict(
                action='create',reviewed=True,version=item['version'],tasks_version=version,
                draft=dict(title=c['title'],description=c['description'],course=None,due=None)))
            self.assertEqual(status,200,result)
        saved = self.call('/workspace')[1]['tasks']['value']
        self.assertEqual([t['content_type'] for t in saved],['notice','task'])
        self.assertTrue(saved[0]['planningExcluded'])
        before=self.model.call_count
        repeat=self.settled(self.start([input_id],'mixed-cached')['job_id'])
        self.assertEqual(repeat['counts']['cached'],1)
        self.assertEqual(self.model.call_count,before)

    def test_restart_marks_only_unfinished_work_and_reuses_completed_results(self):
        completed = email_source._identity(ADDRESS, '77', 61)
        pending = email_source._identity(ADDRESS, '77', 60)
        original = self.settled(self.start([completed], 'before-restart')['job_id'])
        uid = self.call('/auth/me')[1]['user']['id']
        with app.connection() as db:
            row = db.execute('SELECT snapshot FROM email_import_jobs WHERE job_id=?', (original['job_id'],)).fetchone()
            snapshot = json.loads(row['snapshot'])
            snapshot.update(job_id='simulated-restart', state='running')
            # Simulate the pre-normalization durable format. initialize() must
            # migrate this legacy `items` array before recovering the job.
            snapshot['items'] = [json.loads(item['item']) for item in db.execute(
                'SELECT item FROM email_import_job_items WHERE user_id=? AND job_id=? ORDER BY ordinal',
                (uid, original['job_id']))]
            snapshot['items'].append(dict(input_id=pending, state='processing', task_count=None,
                                          request_id=None, error=None))
            db.execute('INSERT INTO email_import_jobs VALUES(?,?,?,?,?)',
                       (uid, snapshot['job_id'], 'interrupted-operation', 'running', json.dumps(snapshot)))
        app.initialize(recover=True)
        status, restored = self.call('/sources/email/jobs/simulated-restart')
        self.assertEqual(status, 200)
        self.assertEqual(restored['state'], 'interrupted')
        self.assertEqual(restored['counts']['pending'], 1)
        self.assertEqual(restored['items'][0]['state'], 'no_tasks')
        self.assertEqual(restored['items'][1]['state'], 'interrupted')
        status, retry = self.call('/sources/email/jobs/simulated-restart/retry', dict(operation_id='resume-restart'))
        self.assertEqual(status, 202, retry)
        final = self.settled(retry['job_id'])
        self.assertEqual(final['state'], 'succeeded', final)
        self.assertEqual(final['counts']['processed'], 2)
        self.assertEqual(final['counts']['cached'], 1)
        self.assertEqual(self.model.call_count, 2)

    def test_queued_range_is_bound_to_connection_and_removal_revokes_consent(self):
        with patch.object(email_import, '_launch'):
            status, job = self.call('/sources/email/jobs', dict(operation_id='bound-range', range=dict(limit=50)))
        self.assertEqual(status, 202)
        self.assertEqual(self.imap_calls, 0, 'range submission must not perform synchronous IMAP work')
        status, _ = self.call('/sources/email/credentials', dict(address='different@stu.pku.edu.cn',
            username='different', password='Different-synthetic-password', consent=True, process_all=False))
        self.assertEqual(status, 200)
        email_import._launch(app.DB, self.call('/auth/me')[1]['user']['id'], job['job_id'],
                             app.ApiError, app.DB.parent/'credential.key')
        final = self.settled(job['job_id'])
        self.assertEqual(final['state'], 'failed')
        self.assertEqual(self.imap_calls, 0)
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(self.call('/sources/email/credentials/remove', {})[0], 200)
        self.call('/sources/email/credentials', dict(address='different@stu.pku.edu.cn',
            username='different', password='Different-synthetic-password'))
        self.assertFalse(self.call('/sources/email/preferences')[1]['consent_granted'])

if __name__ == '__main__':
    unittest.main()

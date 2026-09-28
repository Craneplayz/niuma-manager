"""Offline integration of source indexing, per-record dispatch and failed-only retry."""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
import recognition
import source_sync

FIXTURE = json.loads((Path(__file__).resolve().parent.parent / 'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))


def empty_result(request):
    return dict(contract_version='0.2', request_id=request['request_id'], status='no_tasks',
                record_results=[dict(input_id=r['input_id'], status='no_tasks', task_ids=[], warnings=[]) for r in request['records']], tasks=[], errors=[])


def failure(request):
    result = empty_result(request)
    result['status'] = 'failed'
    for row in result['record_results']:
        row['status'] = 'failed'
    result['errors'] = [dict(input_id=r['input_id'], code='agent_timeout', message='Agent timeout', retryable=True) for r in request['records']]
    return result


class IncrementalRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'test.sqlite3'
        self.connect()
        self.request = copy.deepcopy(FIXTURE['request'])
        self.request['records'] = self.request['records'][:2]
        for r in self.request['records']:
            r['platform'] = 'teaching_web'
        self.output = None

    def connect(self):
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        recognition.initialize(self.db)
        source_sync.initialize(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def sync(self, request=None, uid='owner', account='course-user'):
        result = source_sync.incremental_requests(self.db, uid, account, [request or self.request])
        self.db.commit()
        return result

    def recognize(self, request, result=None, retry=True, uid='owner'):
        def reply(status, value):
            self.db.commit()
            self.output = value
        handler = SimpleNamespace(command='POST', reply=reply)
        def harness(req, timeout):
            self.assertEqual(timeout, 95)
            self.captured.append(json.loads(req.data))
            return io.BytesIO(json.dumps(result if result is not None else empty_result(json.loads(req.data))).encode())
        self.captured = []
        with patch.dict(recognition.os.environ, {'TEXT_HARNESS_URL': 'http://offline/recognize'}), patch.object(recognition, 'urlopen', side_effect=harness):
            recognition.route(handler, self.db, uid, '/api/recognition/retry' if retry else '/api/recognition/batches', request, app.ApiError)
        return self.output

    def test_collection_clock_changes_skip_success_and_survive_restart(self):
        first = self.sync()
        self.assertEqual(first['summary'], dict(total=2, skipped=0, pending=2))
        self.assertTrue(all(len(r['records']) == 1 for r in first['requests']))
        for request in first['requests']:
            self.recognize(request)
        changed_clock = copy.deepcopy(self.request)
        for r in changed_clock['records']:
            r['collected_at'] = '2026-09-29T10:00:00+08:00'
        self.db.close()
        self.connect()
        self.assertEqual(self.sync(changed_clock)['summary'], dict(total=2, skipped=2, pending=0))
        changed_clock['records'][0]['text'] += ' 修改后的要求。'
        changed = self.sync(changed_clock)
        self.assertEqual(changed['summary'], dict(total=2, skipped=1, pending=1))
        self.assertNotEqual(changed['requests'][0]['request_id'], first['requests'][0]['request_id'])

    def test_timeout_does_not_block_later_records_and_only_failure_is_retried(self):
        first = self.sync()['requests']
        self.recognize(first[0], failure(first[0]))
        self.recognize(first[1])
        later = copy.deepcopy(self.request)
        later['records'][0]['collected_at'] = '2026-10-01T10:00:00+08:00'
        retry = self.sync(later)['requests']
        self.assertEqual(retry, [first[0]])  # Preserve the original time anchor and fingerprint.
        self.recognize(retry[0])
        self.assertEqual(len(self.captured), 1)
        self.assertEqual(self.sync()['requests'], [])

    def test_user_and_campus_account_isolation_and_abandoned_sync(self):
        first = self.sync()['requests']
        self.assertEqual(self.sync()['requests'], first)  # Fetching does not imply recognition success.
        for request in first:
            self.recognize(request)
        for kwargs in (dict(uid='other'), dict(account='another-campus-user')):
            isolated = self.sync(**kwargs)
            self.assertEqual(isolated['summary']['pending'], 2)
            self.assertTrue(all(r['request_id'] not in [i['request_id'] for i in first] for r in isolated['requests']))

    def test_more_than_twenty_records_are_independent_requests(self):
        request = copy.deepcopy(self.request)
        request['records'] = [dict(request['records'][0], input_id='synthetic:' + str(i)) for i in range(35)]
        result = self.sync(request)
        self.assertEqual(len(result['requests']), 35)
        for record in result['requests']:
            self.recognize(record)
            self.assertEqual(len(self.captured[0]['records']), 1)
        self.assertEqual(self.sync(request)['summary']['skipped'], 35)

    def test_partial_retry_preserves_successful_candidate_and_confirmation_state(self):
        request = copy.deepcopy(FIXTURE['request'])
        request['records'] = request['records'][:2]
        partial = copy.deepcopy(FIXTURE['expected'])
        partial['record_results'] = partial['record_results'][:2]
        partial['record_results'][1].update(status='failed', task_ids=[])
        partial['tasks'] = partial['tasks'][:1]
        partial['errors'] = failure(dict(request, records=request['records'][1:]))['errors']
        saved = self.recognize(request, partial)
        original = saved[0]['items'][0]
        self.db.execute("UPDATE recognition_candidates SET state='applied',version=2,resolution='synthetic' WHERE id=?", (original['id'],))
        self.db.commit()
        repaired = copy.deepcopy(FIXTURE['expected'])
        repaired.update(status='success', record_results=repaired['record_results'][1:2], tasks=repaired['tasks'][1:2], errors=[])
        saved = self.recognize(request, repaired)
        self.assertEqual(self.captured[0]['records'], request['records'][1:2])
        self.assertEqual(saved[0]['result']['status'], 'success')
        self.assertEqual(len(saved[0]['items']), 2)
        preserved = next(i for i in saved[0]['items'] if i['id'] == original['id'])
        self.assertEqual((preserved['state'], preserved['version']), ('applied', 2))
        self.recognize(request)
        self.assertEqual(self.captured, [])
        self.assertEqual(len(self.output[0]['items']), 2)

    def test_retry_rejects_changed_body_and_malformed_reply_preserves_failure(self):
        request = self.sync()['requests'][0]
        self.recognize(request, failure(request))
        changed = copy.deepcopy(request)
        changed['records'][0]['text'] += 'changed'
        with self.assertRaises(app.ApiError) as conflict:
            self.recognize(changed)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(self.captured, [])
        with self.assertRaises(app.ApiError):
            self.recognize(request, {'bad': True})
        stored = json.loads(self.db.execute('SELECT result FROM recognition_batches').fetchone()[0])
        self.assertEqual(stored['status'], 'failed')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recognition_candidates').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()

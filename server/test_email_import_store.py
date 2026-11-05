"""Durable email storage migration and transaction regression checks."""
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import email_import
import email_import_store as store


class EmailImportStoreTests(unittest.TestCase):
    def test_legacy_migration_is_idempotent_and_preserves_account_item_order(self):
        with closing(sqlite3.connect(':memory:')) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("""CREATE TABLE email_import_jobs(
                user_id TEXT,job_id TEXT,operation_id TEXT,state TEXT,snapshot TEXT,
                PRIMARY KEY(user_id,job_id),UNIQUE(user_id,operation_id))""")
            for uid, ids in [('owner', ['second', 'first']), ('other', ['private'])]:
                job = dict(job_id='same-id', state='succeeded',
                           items=[dict(input_id=x, state='no_tasks', cached=True) for x in ids])
                db.execute('INSERT INTO email_import_jobs VALUES(?,?,?,?,?)',
                           (uid, 'same-id', 'op', 'succeeded', json.dumps(job)))
            store.initialize(db, recover=False)
            store.initialize(db, recover=False)
            for uid, ids in [('owner', ['second', 'first']), ('other', ['private'])]:
                raw = db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=?', (uid,)).fetchone()[0]
                self.assertNotIn('items', json.loads(raw))
                self.assertEqual([x['input_id'] for x in store.load_job(db, uid, 'same-id', raw)['items']], ids)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM email_import_job_items').fetchone()[0], 3)

    def test_job_items_and_revision_rollback_together(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'synthetic.sqlite3'
            with store.connection(path, write=True) as db:
                store.initialize(db, recover=False)
            with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'):
                with store.connection(path, write=True) as db:
                    store.insert_job(db, 'owner', 'job', 'op', 'queued',
                        dict(state='queued', items=[dict(input_id='mail', state='queued')]))
                    raise RuntimeError('synthetic rollback')
            with store.connection(path) as db:
                for table in ('email_import_jobs', 'email_import_job_items', 'email_import_user_revision'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)

    def test_incremental_transitions_match_full_snapshot_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'synthetic.sqlite3'
            items = [dict(input_id='a', state='queued', cached=False),
                     dict(input_id='b', state='no_tasks', cached=True)]
            with store.connection(path, write=True) as db:
                store.initialize(db, recover=False)
                store.insert_job(db, 'owner', 'job', 'op', 'running',
                    dict(state='running', queued_at=datetime.now().astimezone().isoformat(),
                         items=items, counts=store.counts(dict(items=items))))
            for change in (dict(state='processing'), dict(state='failed'),
                           dict(state='has_tasks', cached=True)):
                email_import._update_item(path, 'owner', 'job', 'a', change,
                                           allowed=('queued', 'processing', 'failed'))
                with store.connection(path) as db:
                    raw = db.execute('SELECT snapshot FROM email_import_jobs').fetchone()[0]
                    job = store.load_job(db, 'owner', 'job', raw)
                    self.assertEqual(job['counts'], store.counts(job))


if __name__ == '__main__':
    unittest.main()

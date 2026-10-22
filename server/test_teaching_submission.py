"""Unknown scope follows the shared policy; repair only legacy auto-withdrawals."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest

from contracts.submission_policy import decide, observation_digest
import recognition
import teaching_submission as submission


class SubmissionProtectionTests(unittest.TestCase):
    def test_unknown_scope_gates_match_shared_policy(self):
        cases=json.loads((Path(__file__).resolve().parents[1]/'contracts/examples/submission-policy-v01-cases.json').read_text(encoding='utf-8'))['cases']
        template=deepcopy(cases[0]['observation'])
        template['scope']='unknown'
        template['actions']=[dict(action_id='candidate:unmapped',kind='candidate',relation='unknown',
            link_basis='unresolved',decision='pending',completion_origin=None,source_completion_paused=False)]
        for state,basis in [('active','active_assignment_page'),('unknown','unknown'),('submitted','review_submission_history_title')]:
            for review_pending in (False,True):
                with self.subTest(state=state,review_pending=review_pending):
                    observation=deepcopy(template)
                    observation['snapshot'].update(state=state,basis=basis)
                    observation['review_pending']=review_pending
                    expected=decide(observation,verified_digest=observation_digest(observation),
                        source_account_id=observation['snapshot']['source_account_id'],input_id=observation['snapshot']['input_id'],
                        latest_sequence=0,previous_state=state)
                    stored=dict(state=state,review_pending=int(review_pending))
                    self.assertEqual(submission.skip_model(stored),expected['skip_model'])
                    self.assertEqual(bool(submission.review_reason(stored)),expected['needs_review'])
                    self.assertEqual(expected['withdraw_candidate_ids'],[])
                    self.assertEqual(expected['preserve_action_ids'],['candidate:unmapped'])

    def test_migration_recovers_only_bound_automatic_withdrawals_once(self):
        with sqlite3.connect(':memory:') as db:
            db.row_factory=sqlite3.Row
            recognition.initialize(db);submission.initialize(db)
            legacy=json.dumps(dict(action='source_submitted',input_id='source:1',request_id='new-rid'))
            rows=[('auto','owner','old-rid','{}','ignored',2,legacy),
                  ('manual','owner','old-rid','{}','ignored',2,json.dumps(dict(action='ignore',version=1))),
                  ('applied','owner','old-rid','{}','applied',2,legacy),
                  ('unbound','other','other-rid','{}','ignored',2,legacy)]
            db.executemany('INSERT INTO recognition_candidates VALUES(?,?,?,?,?,?,?)',rows)
            db.execute('INSERT INTO teaching_submission_requests VALUES(?,?,?,?)',('owner','campus','old-rid','source:1'))
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?,NULL)',('owner','old-rid','digest','{}','{"raw":"unchanged"}',0))
            submission.initialize(db)
            current={row['id']:row for row in db.execute('SELECT * FROM recognition_candidates')}
            self.assertEqual((current['auto']['state'],current['auto']['version'],current['auto']['resolution']),('pending',3,None))
            for original in rows[1:]: self.assertEqual(tuple(current[original[0]]),original)
            audit=db.execute('SELECT * FROM teaching_submission_recoveries').fetchone()
            self.assertEqual((audit['candidate_id'],audit['user_id'],audit['previous_resolution']),('auto','owner',legacy))
            self.assertEqual(db.execute('SELECT result FROM recognition_batches').fetchone()[0],'{"raw":"unchanged"}')
            # Restart is idempotent, including a subsequent explicit ignore.
            db.execute('UPDATE recognition_candidates SET state="ignored",version=4,resolution=? WHERE id="auto"',
                (json.dumps(dict(action='ignore',version=3)),))
            submission.initialize(db)
            self.assertEqual(tuple(db.execute('SELECT state,version FROM recognition_candidates WHERE id="auto"').fetchone()),('ignored',4))
            self.assertEqual(db.execute('SELECT count(*) FROM teaching_submission_recoveries').fetchone()[0],1)


if __name__=='__main__': unittest.main()

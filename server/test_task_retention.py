import unittest
from datetime import datetime,timedelta,timezone
from copy import deepcopy
from task_retention import expired,archive

class RetentionTests(unittest.TestCase):
    def test_date_only_uses_end_of_day_and_strict_seven_days(self):
        due=dict(date='2026-10-01',precision='date')
        self.assertFalse(expired(due,datetime.fromisoformat('2026-10-08T23:59:59+08:00')))
        self.assertTrue(expired(due,datetime.fromisoformat('2026-10-09T00:00:00+08:00')))
        self.assertFalse(expired(None))

    def test_archive_preserves_content_and_user_restore_wins(self):
        task=dict(id='synthetic',title='入群',status='not_started',due=dict(date='2026-09-16',time='18:40'),recognitionHistory=[dict(storageId='c')])
        now=datetime.fromisoformat('2026-10-16T12:00:00+08:00');original=deepcopy(task)
        self.assertTrue(archive(task,now));self.assertEqual(task['status'],'cancelled')
        self.assertEqual(task['recycleBin']['reason'],'expired_retention')
        self.assertEqual(task['recognitionHistory'],original['recognitionHistory'])
        task.pop('recycleBin');task.update(status='not_started',retentionOverride=True)
        self.assertFalse(archive(task,now));self.assertEqual(task['status'],'not_started')

    def test_completed_and_unrelated_manual_tasks_are_preserved(self):
        for task in (dict(status='completed',recognitionHistory=[{}]),dict(status='not_started')):
            task['due']=dict(date='2000-01-01');self.assertFalse(archive(task))

import unittest

import planning


class PlanningPriorityTests(unittest.TestCase):
    def test_explanations_follow_scheduler_order_and_use_actual_capacity(self):
        request = dict(
            requestedAt='2026-10-02T10:00:00+08:00',
            availability=dict(startTime='19:00', endTime='21:00', weekdays=list(range(7))),
            existingPlans=[],
            tasks=[
                dict(id='later', title='稍后交', priority='high', due=dict(date='2026-10-05', time='23:00')),
                dict(id='soon', title='先交', priority='low', due=dict(date='2026-10-02', time='20:00')),
                dict(id='undated', title='无截止', priority='high', due=None),
            ],
        )
        order = planning.priority_explanations(request, {'later': 60, 'soon': 120, 'undated': 30})
        self.assertEqual([item['taskId'] for item in order], ['soon', 'later'])
        self.assertEqual(order[0]['availableMinutes'], 60)
        self.assertEqual(order[0]['slackMinutes'], -60)
        self.assertIn('截止前可用时间不足', order[0]['reason'])
        self.assertEqual(order[0]['rank'], 1)


if __name__ == '__main__':
    unittest.main()

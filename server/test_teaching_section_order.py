"""Section routing must not drop eligible notices or break incremental retries."""
import copy
import io
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import recognition
import source_sync
import test_incremental_recognition as integration


class TeachingSectionOrderTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        recognition.initialize(self.db)
        source_sync.initialize(self.db)
        self.addCleanup(self.db.close)

    def record(self, name, section, platform='teaching_web'):
        record = copy.deepcopy(integration.FIXTURE['request']['records'][0])
        record.update(input_id='synthetic:section:' + name, platform=platform,
                      text='合成原文：请于2026年10月5日提交练习。',
                      upstream_hints={'section': section, 'source_kind': 'content'})
        return record

    def sync(self, *groups):
        requests = [dict(contract_version='0.2', request_id=f'synthetic:batch:{i}', records=records)
                    for i, records in enumerate(groups)]
        with self.assertLogs(source_sync.LOGGER, level='INFO') as logs:
            result = source_sync.incremental_requests(self.db, 'synthetic-user', 'synthetic-campus', requests)
        self.db.commit()
        self.stats = json.loads(logs.records[-1].args[0])
        self.log = logs.output[-1]
        for request in result['requests']:
            source_sync.validate_request(request)
        return result

    def recognize(self, request, failed=False):
        result = integration.failure(request) if failed else integration.empty_result(request)
        handler = SimpleNamespace(command='POST', reply=lambda *_: None)
        with patch.dict('os.environ', {'TEXT_HARNESS_URL': 'http://synthetic.invalid/recognize'}), \
                patch.object(recognition, 'urlopen', return_value=io.BytesIO(json.dumps(result).encode())) as call:
            recognition.route(handler, self.db, 'synthetic-user', '/api/recognition/retry', request, app.ApiError)
        self.db.commit()
        self.assertEqual(json.loads(call.call_args.args[0].data), request)

    def ids(self, result):
        return [r['records'][0]['input_id'].removeprefix('synthetic:section:') for r in result['requests']]

    def test_stable_order_across_batches_and_deduplication_before_dispatch(self):
        content1, content2 = [self.record(n, '教学内容') for n in ('content1', 'content2')]
        notice1, notice2 = [self.record(n, '课程通知') for n in ('notice1', 'notice2')]
        work1, work2 = [self.record(n, '课程作业') for n in ('work1', 'work2')]
        ignored = self.record('syllabus', '教学大纲')
        original = copy.deepcopy([content1, ignored, notice1, work1, content2, work2, notice2])
        result = self.sync([content1, ignored, notice1], [work1, content2, work2, notice2, content1])
        self.assertEqual(self.ids(result), ['work1', 'work2', 'notice1', 'notice2', 'content1', 'content2'])
        self.assertEqual(result['summary'], dict(total=6, skipped=0, pending=6))
        self.assertEqual(self.stats['collected'], 7)
        self.assertEqual(self.stats['filtered']['unsupported_section'], 1)
        for section in ('课程作业', '课程通知', '教学内容'):
            self.assertEqual(self.stats['sections'][section], dict(pending=2, unchanged=0))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_versions').fetchone()[0], 6)
        self.assertEqual([content1, ignored, notice1, work1, content2, work2, notice2], original)
        for sensitive in ('合成原文', 'synthetic:section:', 'synthetic-user', 'synthetic-campus'):
            self.assertNotIn(sensitive, self.log)

    def test_missing_higher_priority_groups_never_block_lower_priority(self):
        content = self.record('content', '教学内容')
        notice = self.record('notice', '课程通知')
        self.assertEqual(self.ids(self.sync([content, notice])), ['notice', 'content'])
        for section in ('课程作业', '课程通知', '教学内容'):
            with self.subTest(section=section):
                self.assertEqual(self.ids(self.sync([self.record('only-' + section, section)])), ['only-' + section])
        self.assertEqual(self.sync([])['summary'], dict(total=0, skipped=0, pending=0))

    def test_missing_or_bad_section_is_audited_without_guessing_from_kind_or_text(self):
        rows = [self.record('unknown', '不是栏目：合成敏感标注'), self.record('blank', '  '),
                self.record('null', None), self.record('invalid-list', ['课程作业']),
                self.record('invalid-dict', {'name': '课程作业'}), self.record('no-hints', None)]
        rows[-1]['upstream_hints'] = None
        for record in rows:
            if record['upstream_hints'] is not None:
                record['upstream_hints']['source_kind'] = 'assignment'
        result = self.sync(rows)
        self.assertEqual(result['requests'], [])
        self.assertEqual(result['summary'], dict(total=0, skipped=0, pending=0))
        self.assertEqual(self.stats['filtered'], dict(unsupported_section=1, missing_section=3, invalid_section=2))
        self.assertNotIn('合成敏感标注', self.log)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_versions').fetchone()[0], 0)
        self.assertEqual(self.ids(self.sync([self.record('trimmed', ' 课程作业\n')])), ['trimmed'])

    def test_non_teaching_sources_are_not_filtered_or_reordered(self):
        other = [self.record(p, '教学大纲', platform=p) for p in ('wechat', 'email', 'web', 'unknown')]
        self.assertEqual(self.ids(self.sync(other)), ['wechat', 'email', 'web', 'unknown'])
        self.assertEqual(self.stats['other_sources'], 4)
        result = self.sync([self.record('content', '教学内容'), other[0],
                            self.record('work', '课程作业'), other[1]])
        self.assertEqual(self.ids(result), ['work', 'wechat', 'content', 'email'])

    def test_first_record_timeout_preserves_later_groups_and_retries_only_failure(self):
        rows = [self.record('content', '教学内容'), self.record('notice', '课程通知'), self.record('work', '课程作业')]
        first = self.sync(rows)['requests']
        for index, request in enumerate(first):
            self.recognize(request, failed=index == 0)
        later = copy.deepcopy(rows)
        for record in later:
            record['collected_at'] = '2026-10-01T10:00:00+08:00'
        retry = self.sync(later)
        self.assertEqual(retry['requests'], [first[0]])
        self.assertEqual(retry['summary'], dict(total=3, skipped=2, pending=1))
        self.assertEqual(self.stats['filtered'], dict(unsupported_section=0, missing_section=0, invalid_section=0))
        self.assertEqual(self.stats['sections']['课程作业']['pending'], 1)
        self.assertEqual(self.stats['sections']['课程通知']['unchanged'], 1)
        self.assertEqual(self.stats['sections']['教学内容']['unchanged'], 1)
        self.recognize(retry['requests'][0])
        self.assertEqual(self.sync(later)['requests'], [])

    def test_section_correction_and_content_edits_reenter_without_reusing_old_request(self):
        row = self.record('corrected', '教学大纲')
        self.assertEqual(self.sync([row])['requests'], [])
        row['upstream_hints']['section'] = '教学内容'
        first = self.sync([row])['requests'][0]
        self.recognize(first)
        self.assertEqual(self.sync([row])['requests'], [])
        row['text'] += '补充新的要求。'
        changed = self.sync([row])['requests'][0]
        self.assertNotEqual(changed['request_id'], first['request_id'])
        self.recognize(changed)
        row['upstream_hints']['section'] = '课程作业'
        corrected = self.sync([row])['requests'][0]
        self.assertNotEqual(corrected['request_id'], changed['request_id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recognition_batches').fetchone()[0], 2)

    def test_large_first_sync_keeps_all_lower_priority_records(self):
        content = [self.record('content' + str(i), '教学内容') for i in range(35)]
        work = [self.record('work' + str(i), '课程作业') for i in range(4)]
        notices = [self.record('notice' + str(i), '课程通知') for i in range(3)]
        result = self.sync(content[:20], work + content[20:] + notices)
        self.assertEqual(len(result['requests']), 42)
        self.assertEqual(self.ids(result), ['work' + str(i) for i in range(4)]
                         + ['notice' + str(i) for i in range(3)] + ['content' + str(i) for i in range(35)])


if __name__ == '__main__':
    unittest.main()

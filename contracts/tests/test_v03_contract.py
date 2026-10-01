"""Consumer/producer contract tests; synthetic data, no OCR or model calls."""
import copy
import json
import unittest

from jsonschema import ValidationError

from contracts import check_v03_examples as check


def read(path):
    return json.loads((check.ROOT / path).read_text(encoding='utf-8'))


MAIN = dict(request=read('source-to-text/text-attachments-v03-request.json'),
            expected=read('text-to-app/text-attachments-v03-output.json'))
CASES = read('examples/text-attachments-v03-cases.json')
BY_NAME = {c['name']: c for c in CASES}
JOBS = read('source-to-app/wechat-import-v03-examples.json')


def changed(value, path, replacement):
    value = copy.deepcopy(value)
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    return value


class MediaContractTests(unittest.TestCase):
    def test_schema_definitions_and_all_positive_examples(self):
        check.main()

    def test_request_rejections(self):
        r = ('records', 0)
        a = r + ('attachments', 0)
        b = a + ('blocks', 0)
        cases = [
            ('wrong_version', ('contract_version',), '0.2'),
            ('no_records', ('records',), []),
            ('duplicate_record', ('records',), MAIN['request']['records'] * 2),
            ('empty_body_without_attachment', r + ('attachments',), []),
            ('unexpected_user_id', r + ('user_id',), 'foreign-account'),
            ('absolute_path', a + ('file_name',), 'C:\\Users\\someone\\secret.png'),
            ('file_url', r + ('source_url',), 'file:///private/image.png'),
            ('credential_url', r + ('source_url',), 'https://user:password@example.test/'),
            ('bad_sha', a + ('sha256',), 'not-a-hash'),
            ('bad_timestamp', r + ('published_at', 'value'), '2026-02-30T20:00:00+08:00'),
            ('bad_timezone_basis', r + ('timezone_basis',), 'unknown'),
            ('unsupported_complete', a + ('mime_type',), 'application/zip'),
            ('oversize_parsed_file', a + ('size_bytes',), 20 * 1024 * 1024 + 1),
            ('duplicate_assets', r + ('attachments',), MAIN['request']['records'][0]['attachments'] * 2),
            ('duplicate_blocks', a + ('blocks',), MAIN['request']['records'][0]['attachments'][0]['blocks'] * 2),
            ('unknown_quality', b + ('quality',), 'perfect'),
            ('uncertainty_without_reason', b + ('quality',), 'uncertain'),
            ('zero_page', b + ('locator', 'page'), 0),
            ('no_locator', b + ('locator', 'page'), None),
            ('reversed_bbox', b + ('locator', 'bbox'), [0.9, 0.1, 0.2, 0.5]),
            ('bbox_outside_page', b + ('locator', 'bbox'), [0, 0, 1.2, 1]),
            ('truncated_complete', a + ('coverage', 'truncated'), True),
            ('missing_block_text', b + ('text',), ''),
            ('context_as_current_record', r + ('context', 0, 'input_id'), 'wechat:message:002'),
            ('duplicate_context', r + ('context',), MAIN['request']['records'][0]['context'] * 2),
            ('text_budget', b + ('text',), 'a' * 100000),
        ]
        for name, path, value in cases:
            with self.subTest(name=name), self.assertRaises((ValidationError, ValueError)):
                check.check_request(changed(MAIN['request'], path, value))
        request = copy.deepcopy(MAIN['request'])
        del request['records'][0]['attachments']
        with self.assertRaises(ValidationError):
            check.check_request(request)

    def test_pair_rejections(self):
        t = ('tasks', 0)
        rr = ('record_results', 0)
        changes = [
            ('wrong_request_id', ('request_id',), 'other'),
            ('missing_record_result', ('record_results',), []),
            ('missing_task_ids', rr + ('task_ids',), []),
            ('fabricated_coverage', rr + ('coverage', 'complete'), False),
            ('fabricated_attachment_summary', rr + ('attachments', 0, 'block_count'), 999),
            ('status_mismatch', ('status',), 'no_tasks'),
            ('foreign_candidate', t + ('input_id',), 'foreign'),
            ('foreign_source', t + ('sources', 0, 'input_id'), 'foreign'),
            ('invented_source_label', t + ('sources', 0, 'label'), 'not-original'),
            ('invented_evidence', t + ('evidence', 1, 'quote'), '模型凭空编造的原文'),
            ('unknown_asset', t + ('evidence', 1, 'asset_id'), 'foreign-asset'),
            ('unknown_block', t + ('evidence', 1, 'block_id'), 'foreign-block'),
            ('attachment_in_context', t + ('evidence', 1, 'input_id'), 'wechat:message:001'),
            ('wrong_support', t + ('evidence', 0, 'supports'), ['description']),
            ('invented_deadline_raw', t + ('deadline', 'raw'), '2040年2月2日'),
            ('wrong_offset', t + ('deadline', 'value'), '2026-10-05T09:00:00+00:00'),
            ('invalid_date', t + ('deadline', 'value'), '2026-02-30T09:00:00+08:00'),
            ('date_only_with_clock', t + ('deadline', 'precision'), 'date'),
            ('review_without_reason', t + ('needs_review',), True),
            ('invented_course', t + ('course_or_project',), '不存在的课程'),
            ('duplicate_candidates', ('tasks',), MAIN['expected']['tasks'] * 2),
        ]
        for name, path, value in changes:
            with self.subTest(name=name), self.assertRaises((ValidationError, ValueError)):
                check.check_pair(MAIN['request'], changed(MAIN['expected'], path, value))

    def test_incomplete_or_uncertain_never_becomes_no_tasks(self):
        for name in ['partial-pdf-missing-deadline-page', 'missing-asset-is-failure',
                     'unsupported-file-is-failure', 'uncertain-ocr-review-required',
                     'recognition-timeout-preserves-first-task']:
            case = copy.deepcopy(BY_NAME[name])
            out = case['expected']
            out.update(status='no_tasks', tasks=[], errors=[])
            out['record_results'][0].update(status='no_tasks', task_ids=[], warnings=[])
            with self.subTest(name=name), self.assertRaises((ValidationError, ValueError)):
                check.check_pair(case['request'], out)

    def test_partial_results_cannot_drop_review_or_extraction_errors(self):
        for name in ['partial-pdf-missing-deadline-page', 'uncertain-ocr-review-required',
                     'recognition-timeout-preserves-first-task']:
            case = copy.deepcopy(BY_NAME[name])
            case['expected']['tasks'][0].update(needs_review=False, review_reasons=[])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'missing_review'):
                check.check_pair(case['request'], case['expected'])
        case = copy.deepcopy(BY_NAME['partial-pdf-missing-deadline-page'])
        case['expected']['errors'][0]['code'] = 'parse_failed'
        with self.assertRaisesRegex(ValueError, 'lost_extraction_error'):
            check.check_pair(case['request'], case['expected'])

    def test_all_failure_and_mixed_batch_aggregation(self):
        one = copy.deepcopy(MAIN)
        two = copy.deepcopy(BY_NAME['missing-asset-is-failure'])
        two['request']['records'][0]['input_id'] = 'second'
        two['expected']['record_results'][0]['input_id'] = 'second'
        two['expected']['errors'][0]['input_id'] = 'second'
        one['request']['records'] += two['request']['records']
        one['expected']['record_results'] += two['expected']['record_results']
        one['expected']['errors'] += two['expected']['errors']
        one['expected']['status'] = 'partial_success'
        check.check_pair(one['request'], one['expected'])
        # A retry can wait in the queue while earlier successful records remain done.
        retry_job = copy.deepcopy(JOBS[0])
        retry_job['progress'].update(total_records=2, finished_records=1)
        check.check_job(one['request'], retry_job)
        one['expected']['status'] = 'success'
        with self.assertRaisesRegex(ValueError, 'status_mismatch'):
            check.check_pair(one['request'], one['expected'])

    def test_import_definitions_and_progress(self):
        ack = {key: JOBS[0][key] for key in ['contract_version', 'job_id', 'request_id', 'status']}
        check.schema('wechat-import', ack, 'ack')
        check.schema('wechat-import', {'retry_id': 'retry-001'}, 'retry')
        check.schema('wechat-import', {'error': dict(code='unauthorized', message='请登录。', retryable=False)}, 'error')
        with self.assertRaises(ValidationError):
            check.schema('wechat-import', {}, 'retry')
        for name, path, value in [
            ('too_many_finished', ('progress', 'finished_records'), 2),
            ('wrong_request', ('request_id',), 'other'),
            ('wrong_total', ('progress', 'total_records'), 2),
            ('failed_exceeds_finished', ('progress', 'failed_records'), 1),
            ('unexpected_early_result', ('result',), MAIN['expected']),
        ]:
            with self.subTest(name=name), self.assertRaises((ValidationError, ValueError)):
                check.check_job(MAIN['request'], changed(JOBS[0], path, value))
        with self.assertRaises(ValidationError):
            check.check_job(MAIN['request'], changed(JOBS[-1], ('result',), None))
        for name in ['missing-asset-is-failure', 'recognition-timeout-preserves-first-task']:
            case = BY_NAME[name]
            job = copy.deepcopy(JOBS[-1])
            job.update(request_id=case['request']['request_id'], result=case['expected'],
                       status='failed' if case['expected']['status'] == 'failed' else 'partial_failed')
            job['progress']['failed_records'] = 1
            check.check_job(case['request'], job)


if __name__ == '__main__':
    unittest.main()

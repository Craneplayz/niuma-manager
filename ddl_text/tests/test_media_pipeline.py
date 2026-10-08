"""No private files or provider calls; all inputs originate from shared fixtures."""
import copy
import hashlib
import io
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image, PngImagePlugin
from contracts.check_v031_examples import check_pair, check_request
from ddl_text.config import Settings
from ddl_text.image_reader import ImageLimits, MetadataMismatch, read_image
from ddl_text.media_pipeline import MediaInputError, MediaLimits, _prepare, process_media_request
from ddl_text.tests.media_cases import ROOT, FakeMediaClient, draft_task, good_draft, image_bytes, memory_loader, request_for
from ddl_text.media_schema import draft_schema
from ddl_text.tests.run_media_smoke import expected_result
from jsonschema import Draft202012Validator, ValidationError


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.request, self.data = request_for()
        self.settings = Settings(timeout_seconds=10)

    def run_request(self, request=None, draft=None, loader=None, client=None, **kwargs):
        request = request or self.request
        result = process_media_request(request, loader or memory_loader(self.data),
                                       client or FakeMediaClient(draft), self.settings, **kwargs)
        check_pair(request, result)
        return result

    def text_task(self, deadline='not_provided'):
        record = self.request['records'][0]
        record.update(text='请提交实验报告。', text_quality='complete', context=[])
        task = good_draft()['tasks'][0]
        task.update(description='提交实验报告', evidence=[dict(input_id=record['input_id'], field='text',
                    quote=record['text'], supports=['title', 'description'])])
        task['deadline'] = dict(value=None, precision='unknown', timezone='Asia/Shanghai', kind='unknown',
                                status=deadline, raw=None, basis='正文未提供截止日期。', alternatives=[])
        return dict(tasks=[task], image_quality=[])

    def test_shared_file_sample_with_context_is_reviewable_and_sources_are_copied(self):
        before = copy.deepcopy(self.request)
        result = self.run_request()
        task = result['tasks'][0]
        self.assertEqual(result['status'], 'success')
        self.assertTrue(task['needs_review'])
        self.assertTrue(task['review_reasons'])
        self.assertEqual(task['deadline']['value'], '2026-10-05T09:00:00+08:00')
        self.assertEqual(task['course_or_project'], '计算机系统导论')
        self.assertEqual(task['sources'][0]['label'], before['records'][0]['source_label'])
        self.assertEqual(result['record_results'][0]['attachments'][0]['coverage_basis'], 'adapter_confirmed')
        self.assertEqual(before, self.request)
        self.assertNotIn('asset:synthetic', json.dumps(result))

    def test_jpeg_and_date_only_do_not_fabricate_clock(self):
        self.data = image_bytes('JPEG')
        self.request, _ = request_for(self.data)
        self.request['records'][0]['attachments'][0].update(mime_type='image/jpeg', file_name='task.jpg')
        draft = good_draft()
        draft['tasks'][0]['deadline'].update(value='2026-10-05', precision='date')
        result = self.run_request(draft=draft)
        self.assertEqual(result['tasks'][0]['deadline']['value'], '2026-10-05')

    def test_complete_clear_image_with_no_action_is_no_tasks(self):
        draft = good_draft(); draft['tasks'] = []
        result = self.run_request(draft=draft)
        self.assertEqual(result['status'], 'no_tasks')
        self.assertTrue(result['record_results'][0]['coverage']['complete'])

    def test_blurry_empty_result_is_failed_not_no_tasks(self):
        draft = good_draft(); draft['tasks'] = []
        draft['image_quality'][0].update(quality='uncertain', quality_notes=['文字模糊'])
        result = self.run_request(draft=draft)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'low_content_quality')

    def test_unknown_file_and_partial_text_keep_known_action_but_not_false_no_deadline(self):
        draft = self.text_task()
        def missing(*args, **kwargs):
            raise FileNotFoundError('PRIVATE-PATH')
        result = self.run_request(draft=draft, loader=missing)
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(result['errors'][0]['code'], 'file_ref_unavailable')
        self.assertEqual(result['tasks'][0]['deadline']['status'], 'unknown')
        self.assertNotIn('PRIVATE-PATH', json.dumps(result))

    def test_upstream_unsupported_file_does_not_load_bytes(self):
        self.request['records'][0]['attachments'][0].update(mime_type='image/gif', file_name='task.gif')
        draft = self.text_task()
        def forbidden(*args, **kwargs):
            self.fail('unsupported reader must not load bytes')
        # An upstream unsupported file remains unsupported without invoking any reader.
        asset = self.request['records'][0]['attachments'][0]
        asset.update(status='unsupported', file_ref=None, errors=[dict(stage='collect', code='unsupported_type', message='合成不支持文件', retryable=False)])
        result = self.run_request(draft=draft, loader=forbidden)
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(result['errors'][0]['code'], 'unsupported_type')
        self.assertEqual(result['record_results'][0]['attachments'][0]['status'], 'unsupported')

    def test_partial_failure_does_not_suppress_other_image_tasks(self):
        other = copy.deepcopy(self.request['records'][0]['attachments'][0])
        other.update(asset_id='missing', file_ref='asset:missing_asset_123456')
        self.request['records'][0]['attachments'].append(other)
        result = self.run_request()
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(len(result['tasks']), 1)
        self.assertTrue(result['tasks'][0]['needs_review'])
        self.assertEqual(result['errors'][0]['asset_id'], 'missing')

    def test_upstream_failures_are_preserved(self):
        record = self.request['records'][0]
        record['context'] = []
        asset = record['attachments'][0]
        asset.update(status='failed', file_ref=None, errors=[dict(stage='collect', code='decrypt_failed', message='合成解密失败', retryable=False)])
        result = self.run_request()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'decrypt_failed')
        self.assertEqual(result['errors'][0]['stage'], 'collect')

    def test_text_only_v031_and_no_deadline_are_supported(self):
        draft = self.text_task()
        self.request['records'][0]['attachments'] = []
        result = self.run_request(draft=draft)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['tasks'][0]['deadline']['status'], 'not_provided')
        self.assertFalse(result['tasks'][0]['needs_review'])

    def test_unread_or_foreign_evidence_and_fabricated_text_are_rejected(self):
        for mutation in ('asset', 'text', 'source', 'raw', 'timezone', 'quality'):
            with self.subTest(mutation=mutation):
                draft = good_draft()
                task = draft['tasks'][0]
                if mutation == 'asset': task['evidence'][1]['asset_id'] = 'foreign'
                if mutation == 'text': task['evidence'][0]['quote'] = '编造正文'
                if mutation == 'source': task['evidence'][0]['input_id'] = 'foreign'
                if mutation == 'raw': task['deadline']['raw'] = '不存在的日期原话'
                if mutation == 'timezone': task['deadline']['value'] = '2026-10-05T09:00:00+01:00'
                if mutation == 'quality': draft['image_quality'] = []
                result = self.run_request(draft=draft)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['errors'][-1]['code'], 'invalid_agent_output')

    def test_injected_model_coverage_and_verified_claims_are_rejected(self):
        for mutation in ('coverage', 'needs_review', 'verification'):
            draft = good_draft()
            if mutation == 'coverage': draft['coverage'] = dict(complete=True)
            if mutation == 'needs_review': draft['tasks'][0]['needs_review'] = False
            if mutation == 'verification': draft['tasks'][0]['evidence'][1]['verification'] = 'verified'
            self.assertEqual(self.run_request(draft=draft)['status'], 'failed')

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for raw in ('{"tasks":[],"tasks":[],"image_quality":[]}', '{"tasks":NaN,"image_quality":[]}'):
            self.assertEqual(self.run_request(client=FakeMediaClient(raw))['status'], 'failed')

    def test_metadata_mismatch_and_paths_are_rejected_before_model(self):
        for name, value in [('size_bytes', 1), ('sha256', '0' * 64), ('mime_type', 'image/jpeg')]:
            request = copy.deepcopy(self.request)
            request['records'][0]['attachments'][0][name] = value
            with self.subTest(field=name), self.assertRaises(MetadataMismatch):
                self.run_request(request=request)
        self.request['records'][0]['attachments'][0]['file_ref'] = 'file:///private.png'
        with self.assertRaises(MediaInputError):
            self.run_request()

    def test_damaged_image_has_read_error(self):
        self.data = self.data[:30]
        self.request, _ = request_for(self.data)
        self.request['records'][0]['context'] = []
        result = self.run_request()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'file_read_failed')

    def test_animated_png_not_silently_read_as_first_frame(self):
        output = io.BytesIO()
        Image.new('RGB', (4, 4), 'white').save(output, format='PNG', save_all=True,
                  append_images=[Image.new('RGB', (4, 4), 'black')], duration=100)
        self.data = output.getvalue(); self.request, _ = request_for(self.data)
        self.request['records'][0]['context'] = []
        result = self.run_request()
        self.assertEqual(result['errors'][0]['code'], 'reader_unsupported')

    def test_pixel_and_model_budgets_are_explicit_failures(self):
        self.request['records'][0]['context'] = []
        limits = MediaLimits(images=ImageLimits(max_pixels=100))
        result = self.run_request(limits=limits)
        self.assertEqual(result['errors'][0]['code'], 'limit_exceeded')
        result = self.run_request(limits=MediaLimits(model_input_bytes=100))
        self.assertEqual(result['errors'][0]['code'], 'limit_exceeded')

    def test_worker_timeout_does_not_claim_file_was_read(self):
        self.settings = Settings(timeout_seconds=.6)
        started = time.monotonic()
        result = self.run_request(client=FakeMediaClient(behavior='timeout'))
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('agent_timeout', [e['code'] for e in result['errors']])
        self.assertEqual(result['record_results'][0]['attachments'][0]['status'], 'failed')

    def test_image_reencoding_removes_comments_and_keeps_pixels(self):
        output = io.BytesIO(); info = PngImagePlugin.PngInfo(); info.add_text('private', 'PRIVATE-METADATA')
        Image.new('RGB', (20, 10), 'white').save(output, format='PNG', pnginfo=info)
        request, data = request_for(output.getvalue())
        raw = read_image(data, request['records'][0]['attachments'][0], ImageLimits())
        self.assertNotIn(b'PRIVATE-METADATA', raw)
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.size, (20, 10))
            self.assertEqual(image.getpixel((1, 1)), (255, 255, 255))

    def test_shared_native_blocks_retain_substring_evidence(self):
        cases = json.loads((ROOT / 'contracts/examples/text-attachments-v031-cases.json').read_text(encoding='utf-8'))
        for case in cases[:5]:
            with self.subTest(case=case['name']):
                if len(case['request']['records']) != 1:
                    continue
                draft = dict(tasks=[draft_task(task) for task in case['expected']['tasks']], image_quality=[])
                result = self.run_request(request=case['request'], draft=draft)
                self.assertEqual(result['status'], case['expected']['status'])
                self.assertEqual(len(result['tasks']), len(case['expected']['tasks']))

    def test_model_schema_expresses_quality_notes_rule(self):
        draft = good_draft()
        draft['image_quality'][0]['quality_notes'] = ['clear image']
        with self.assertRaises(ValidationError):
            Draft202012Validator(draft_schema()).validate(draft)
        draft = good_draft()
        draft['tasks'][0]['target_reference'] = 'homework label is not an update target'
        with self.assertRaises(ValidationError):
            Draft202012Validator(draft_schema()).validate(draft)

    def test_batch_failure_does_not_leak_into_later_record(self):
        missing = copy.deepcopy(self.request['records'][0])
        missing.update(input_id='missing-only', context=[])
        missing['attachments'][0]['file_ref'] = 'asset:missing_asset_123456'
        self.request['records'].insert(0, missing)
        result = self.run_request()
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual([r['status'] for r in result['record_results']], ['failed', 'success'])

    def test_expired_budget_skips_calls_and_bad_loader_messages_are_sanitized(self):
        def forbidden(*args, **kwargs):
            self.fail('expired budget must not read storage')
        result = self.run_request(loader=forbidden, limits=MediaLimits(batch_seconds=1e-12))
        self.assertEqual(result['status'], 'failed')
        def broken(*args, **kwargs):
            raise RuntimeError('PRIVATE-STORAGE-PATH')
        self.request['records'][0]['context'] = []
        result = self.run_request(loader=broken)
        self.assertEqual(result['errors'][0]['code'], 'file_read_failed')
        self.assertNotIn('PRIVATE-STORAGE-PATH', json.dumps(result))

    def test_model_failure_is_not_automatically_retried(self):
        result = self.run_request(client=FakeMediaClient(behavior='failure'))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('agent_unavailable', [e['code'] for e in result['errors']])
        self.assertTrue(all(e['retryable'] for e in result['errors']))

    def test_model_crash_is_sanitized_and_not_retryable(self):
        result = self.run_request(client=FakeMediaClient(behavior='crash'))
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(result))
        self.assertFalse(any(e['retryable'] for e in result['errors']))

    def test_oversized_middle_image_does_not_spend_later_image_budget(self):
        record = copy.deepcopy(self.request['records'][0])
        record['attachments'] = []
        files = {}
        for index, size in enumerate([(40, 20), (1000, 1000), (40, 20)]):
            data = image_bytes(size=size)
            asset = copy.deepcopy(self.request['records'][0]['attachments'][0])
            asset.update(asset_id=f'image-{index}', file_ref=f'asset:synthetic_asset_{index:03}_001',
                         size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            record['attachments'].append(asset); files[asset['file_ref']] = data
        _, errors, pictures, _ = _prepare(record, lambda ref, **_: files[ref],
                                      MediaLimits(model_input_bytes=1000), time.monotonic() + 20)
        self.assertEqual([picture.asset_id for picture in pictures], ['image-0', 'image-2'])
        self.assertEqual([(e['asset_id'], e['code']) for e in errors], [('image-1', 'limit_exceeded')])

    def test_aggregate_result_limit_keeps_earlier_successful_record(self):
        draft = self.text_task()
        record = self.request['records'][0]
        record['attachments'] = []
        # Both records explicitly share the same quoted context, not each other's message.
        context = {k: copy.deepcopy(v) for k, v in record.items()
                   if k in ('input_id', 'text', 'published_at', 'timezone', 'platform', 'source_url', 'source_label')}
        context['input_id'] = 'shared-source'
        record['context'] = [context]
        draft['tasks'][0]['evidence'][0]['input_id'] = context['input_id']
        first = self.run_request(draft=draft)
        second = copy.deepcopy(record)
        second['input_id'] = 'second-record'
        self.request['records'].append(second)
        boundary = len(json.dumps(first, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 500
        with patch('ddl_text.media_pipeline.RESULT_MAX_BYTES', boundary):
            result = self.run_request(draft=draft)
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual([r['status'] for r in result['record_results']], ['success', 'failed'])
        self.assertEqual(result['tasks'], first['tasks'])
        self.assertEqual(result['errors'][-1]['code'], 'limit_exceeded')

    def yearless_case(self):
        # The real failed wording is kept unchanged; classify it in the new private draft.
        archive = json.loads((ROOT / 'ddl_text/docs/archive/2026-10-06-issue94-images.json').read_text(encoding='utf-8'))
        case = next(c for c in archive['results'] if c['case'] == 'yearless-date')
        self.request = case['request']
        self.data = image_bytes(size=(1400, 230), text=case['synthetic_image_text'])
        # PNG encoding can differ across OS/library builds; authorize this test's
        # generated bytes, while preserving the archived live request unchanged.
        self.request['records'][0]['attachments'][0].update(
            size_bytes=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())
        draft = json.loads(case['raw_draft'])
        draft['tasks'][0]['review_reasons'] = [dict(code='year_completion', message=r)
                                               for r in draft['tasks'][0]['review_reasons']]
        return draft

    def test_routine_year_reason_removed_but_image_review_and_date_preserved(self):
        draft = self.yearless_case()
        deadline = copy.deepcopy(draft['tasks'][0]['deadline'])
        result = self.run_request(draft=draft)
        task = result['tasks'][0]
        self.assertEqual(task['deadline'], deadline)
        self.assertEqual(task['review_reasons'], ['图片引文由模型读取，尚未经原文逐字核对，请确认。'])
        self.assertTrue(task['needs_review'])
        self.assertEqual(task['evidence'], draft['tasks'][0]['evidence'])

    def test_unrelated_reason_survives_normal_year_completion(self):
        draft = self.yearless_case()
        reason = '尚不清楚这份作业适用于哪个班级。'
        draft['tasks'][0]['review_reasons'].append(dict(code='other', message=reason))
        result = self.run_request(draft=draft)
        self.assertEqual(result['tasks'][0]['review_reasons'], [reason, '图片引文由模型读取，尚未经原文逐字核对，请确认。'])

    def test_missing_or_old_original_date_cannot_borrow_current_message_year(self):
        for publication in (dict(value=None, precision='unknown', basis='unknown'),
                            dict(value='2023-10-01', precision='date', basis='source')):
            with self.subTest(publication=publication):
                draft = self.yearless_case()
                asset = self.request['records'][0]['attachments'][0]
                asset.update(origin='forwarded', original_published_at=publication)
                result = self.run_request(draft=draft)
                self.assertIn(draft['tasks'][0]['review_reasons'][0]['message'], result['tasks'][0]['review_reasons'])

    def test_unresolved_deadline_retains_year_reason(self):
        for status in ('unknown', 'conflict', 'inferred'):
            with self.subTest(status=status):
                draft = self.yearless_case()
                deadline = draft['tasks'][0]['deadline']
                if status == 'unknown':
                    deadline.update(status='unknown', value=None, precision='unknown', kind='unknown')
                elif status == 'conflict':
                    deadline.update(status='conflict', value=None, precision='unknown',
                                    alternatives=['2025-10-09', '2026-10-09'])
                else:
                    deadline.update(status='inferred', kind='reference')
                result = self.run_request(draft=draft)
                self.assertIn(draft['tasks'][0]['review_reasons'][0]['message'], result['tasks'][0]['review_reasons'])

    def test_uncertain_read_preserves_year_caution(self):
        draft = self.yearless_case()
        draft['image_quality'][0].update(quality='uncertain', quality_notes=['日期附近有遮挡。'])
        result = self.run_request(draft=draft)
        self.assertIn(draft['tasks'][0]['review_reasons'][0]['message'], result['tasks'][0]['review_reasons'])

    def test_other_time_problem_is_not_removed_by_its_wording(self):
        draft = self.yearless_case()
        reason = '原图出现两个不同年份，需核实。'
        draft['tasks'][0]['review_reasons'] = [dict(code='other', message=reason)]
        result = self.run_request(draft=draft)
        self.assertIn(reason, result['tasks'][0]['review_reasons'])

    def test_year_completion_uses_each_deadline_evidence_source(self):
        draft = self.yearless_case()
        record = self.request['records'][0]
        context = {k: copy.deepcopy(record[k]) for k in
                   ('input_id', 'text', 'published_at', 'timezone', 'platform', 'source_url', 'source_label')}
        context.update(input_id='older-context', text='Due October 9.',
                       published_at=dict(value='2023-10-01', precision='date', basis='source'))
        record['context'].append(context)
        draft['tasks'][0]['evidence'].append(dict(input_id='older-context', field='text',
                                                quote='Due October 9.', supports=['deadline']))
        result = self.run_request(draft=draft)
        self.assertIn(draft['tasks'][0]['review_reasons'][0]['message'], result['tasks'][0]['review_reasons'])

    def test_private_reason_types_are_required_and_unknown_types_rejected(self):
        for reason in ('年份待核实', dict(code='ignore_all_warnings', message='year')):
            with self.subTest(reason=reason):
                draft = good_draft()
                draft['tasks'][0]['review_reasons'] = [reason]
                self.assertEqual(self.run_request(draft=draft)['errors'][-1]['code'], 'invalid_agent_output')

    def test_smoke_assertion_detects_original_false_positive(self):
        archive = json.loads((ROOT / 'ddl_text/docs/archive/2026-10-06-issue94-images.json').read_text(encoding='utf-8'))
        original = next(c for c in archive['results'] if c['case'] == 'yearless-date')['result']
        self.assertFalse(expected_result('yearless-date', original, '2026-10-09', 'date'))
        draft = self.yearless_case()
        self.assertTrue(expected_result('yearless-date', self.run_request(draft=draft), '2026-10-09', 'date'))


if __name__ == '__main__':
    unittest.main()

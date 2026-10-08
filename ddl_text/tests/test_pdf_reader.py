import io
import json
import unittest

from PIL import Image
from pypdf import PdfReader, PdfWriter
from contracts.check_v031_examples import check_pair
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.image_reader import MetadataMismatch
from ddl_text.pdf_reader import PdfLimits, read_pdf
from ddl_text.media_pipeline import MediaLimits, process_media_request, _parts, _prepare
from ddl_text.tests.media_cases import FakeMediaClient, good_draft, memory_loader
from ddl_text.tests.pdf_cases import pdf_bytes, pdf_request
import time


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.data = pdf_bytes(('Submit the synthetic experiment report.', 'Due October 5, 2026 at 09:00.'))
        self.request = pdf_request(self.data)
        self.asset = self.request['records'][0]['attachments'][0]

    def run_request(self, draft=None, limits=None, client=None):
        result = process_media_request(self.request, memory_loader(self.data), client or FakeMediaClient(draft),
            Settings(timeout_seconds=8), limits or MediaLimits())
        check_pair(self.request, result)
        return result

    def test_full_pages_and_labels_have_no_storage_handle(self):
        record = self.request['records'][0]
        summaries, errors, pictures = _prepare(record, memory_loader(self.data), MediaLimits(), time.monotonic()+20)
        self.assertEqual([p[2] for p in pictures], [1, 2])
        parts = _parts(record, summaries, pictures, errors)
        self.assertEqual(json.loads(parts[0]['text'])['reader']['attachments'][0]['status'], 'complete')
        self.assertEqual(summaries[0]['status'], 'failed')  # Await the actual model call.
        self.assertEqual(json.loads(parts[3]['text']), dict(asset_id='asset-001', page=2))
        self.assertNotIn(self.asset['file_ref'], json.dumps(parts))
        read = read_pdf(self.data, self.asset, PdfLimits())
        self.assertEqual(read['total_pages'], 2)
        with Image.open(io.BytesIO(read['pages'][0])) as image:
            self.assertEqual(image.size, (1190, 1684))
        result = self.run_request()
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['record_results'][0]['coverage']['complete'])
        self.assertTrue(result['tasks'][0]['needs_review'])

    def test_page_cap_keeps_candidate_with_explicit_gap(self):
        result = self.run_request(limits=MediaLimits(pdf=PdfLimits(max_pages=1)))
        self.assertEqual(result['status'], 'partial_success')
        summary = result['record_results'][0]['attachments'][0]
        self.assertEqual(summary['status'], 'partial')
        self.assertFalse(summary['coverage']['complete'])
        self.assertTrue(summary['coverage']['truncated'])
        self.assertIn('2', summary['coverage']['unread_locations'][0])
        self.assertEqual(summary['coverage_basis'], 'adapter_confirmed')

    def test_forms_are_explicitly_unsupported(self):
        from reportlab.pdfgen import canvas
        output = io.BytesIO(); document = canvas.Canvas(output)
        document.acroForm.textfield(name='synthetic', x=20, y=20, width=100, height=20)
        document.showPage(); document.save()
        data = output.getvalue(); asset = pdf_request(data)['records'][0]['attachments'][0]
        with self.assertRaises(AgentError) as caught:
            read_pdf(data, asset, PdfLimits())
        self.assertEqual(caught.exception.code, 'reader_unsupported')

    def test_partial_empty_is_failure_not_no_tasks(self):
        draft = good_draft(); draft['tasks'] = []
        result = self.run_request(draft=draft, limits=MediaLimits(pdf=PdfLimits(max_pages=1)))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'limit_exceeded')

    def test_no_action_full_document_is_no_tasks(self):
        draft = good_draft(); draft['tasks'] = []
        self.assertEqual(self.run_request(draft=draft)['status'], 'no_tasks')

    def test_unread_or_unidentified_page_cannot_support_task(self):
        for page in (2, 3, None):
            with self.subTest(page=page):
                draft = good_draft()
                draft['tasks'][0]['evidence'][1]['locator']['page'] = page
                result = self.run_request(draft=draft, limits=MediaLimits(pdf=PdfLimits(max_pages=1)))
                self.assertEqual(result['status'], 'failed')
                self.assertIn('invalid_agent_output', [e['code'] for e in result['errors']])

    def test_later_oversize_page_retains_previous_page(self):
        writer = PdfWriter()
        writer.add_page(PdfReader(io.BytesIO(self.data)).pages[0])
        writer.add_blank_page(width=10000, height=10000)
        output = io.BytesIO(); writer.write(output)
        self.data = output.getvalue(); self.request = pdf_request(self.data)
        result = self.run_request()
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(result['errors'][0]['code'], 'limit_exceeded')

    def test_corrupt_pdf_is_read_failure(self):
        data = b'%PDF-1.7\nbroken'
        asset = pdf_request(data)['records'][0]['attachments'][0]
        with self.assertRaises(AgentError) as caught:
            read_pdf(data, asset, PdfLimits())
        self.assertEqual(caught.exception.code, 'file_read_failed')

    def test_mismatched_receipt_rejected_before_model(self):
        with self.assertRaises(MetadataMismatch):
            read_pdf(self.data+b'bad', self.asset, PdfLimits())
        with self.assertRaises(MetadataMismatch):
            read_pdf(b'not-a-pdf', pdf_request(b'not-a-pdf')['records'][0]['attachments'][0], PdfLimits())

    def test_encrypted_and_embedded_files_are_not_claimed_complete(self):
        for mode in ('password', 'empty-password', 'embedded', 'javascript'):
            with self.subTest(mode=mode):
                writer = PdfWriter(clone_from=io.BytesIO(self.data))
                if mode in ('password', 'empty-password'):
                    writer.encrypt('synthetic-password' if mode == 'password' else '')
                elif mode == 'embedded':
                    writer.add_attachment('hidden.txt', b'Submit unseen work')
                else:
                    writer.add_js("app.alert('synthetic only')")
                output = io.BytesIO(); writer.write(output)
                data = output.getvalue(); asset = pdf_request(data)['records'][0]['attachments'][0]
                with self.assertRaises(AgentError) as caught:
                    read_pdf(data, asset, PdfLimits())
                self.assertIn(caught.exception.code, ('file_read_failed', 'reader_unsupported'))

    def test_limits_and_model_timeout_do_not_leave_complete_summary(self):
        with self.assertRaises(AgentError) as caught:
            read_pdf(self.data, self.asset, PdfLimits(max_page_pixels=10))
        self.assertEqual(caught.exception.code, 'limit_exceeded')
        result = process_media_request(self.request, memory_loader(self.data), FakeMediaClient(behavior='timeout'),
            Settings(timeout_seconds=0.15))
        check_pair(self.request, result)
        self.assertFalse(result['record_results'][0]['coverage']['complete'])
        self.assertEqual(result['record_results'][0]['attachments'][0]['status'], 'failed')

    def test_bounds_validate(self):
        for kwargs in (dict(max_pages=0), dict(max_pages=True), dict(scale=float('nan')), dict(max_total_pixels=0)):
            with self.assertRaises(ValueError):
                PdfLimits(**kwargs)


if __name__ == '__main__':
    unittest.main()

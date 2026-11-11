import io
import unittest
import zipfile
import warnings

from contracts.check_v031_examples import check_pair
from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.docx_reader import DocxLimits, read_docx
from ddl_text.image_reader import ImageLimits, MetadataMismatch
from ddl_text.media_pipeline import MediaLimits, process_media_request
from ddl_text.tests.docx_cases import package, paragraph, relationship, drawing, docx_request
from ddl_text.tests.media_cases import FakeMediaClient, good_draft, image_bytes, memory_loader

TEXT = '请于2026年10月8日18点前提交实验报告，请尽快完成。'


def draft_for(request, quote=TEXT, number=1):
    draft = good_draft(); task = draft['tasks'][0]
    task.update(title='提交实验报告', description='提交实验报告。', review_reasons=[], evidence=[dict(
        input_id=request['records'][0]['input_id'], field='attachment_file', asset_id='asset-001', quote=quote,
        supports=['title', 'description', 'deadline'], verification='model_reported',
        locator=dict(page=None, paragraph=number, bbox=None))])
    task['deadline'].update(value='2026-10-08T18:00:00+08:00', raw='2026年10月8日18点', status='explicit')
    return draft


class DocxTests(unittest.TestCase):
    def read(self, data, limits=None):
        asset = docx_request(data)['records'][0]['attachments'][0]
        return read_docx(data, asset, limits or DocxLimits(), ImageLimits())

    def run_request(self, data, draft=None, limits=None, client=None):
        request = docx_request(data)
        result = process_media_request(request, memory_loader(data), client or FakeMediaClient(draft or draft_for(request)),
            Settings(timeout_seconds=8), limits or MediaLimits())
        check_pair(request, result)
        return result

    def test_sequential_documents_and_read_failure_reap_workers(self):
        import multiprocessing
        children = {child.pid for child in multiprocessing.active_children()}
        for text in (TEXT, '请于2026年10月9日提交第二份报告。'):
            read = self.read(package(paragraph(text)))
            self.assertEqual(read['blocks'][0]['text'], text)
            self.assertTrue(read['coverage']['complete'])
        with self.assertRaises(AgentError):
            self.read(package(paragraph(TEXT), {'word/document.xml':'broken'}))
        self.assertTrue(self.read(package(paragraph(TEXT)))['coverage']['complete'])
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, children)

    def test_body_runs_and_tables_keep_order_and_cell_context(self):
        data = package('<w:p><w:r><w:t>请提交</w:t></w:r><w:r><w:t>报告。</w:t></w:r></w:p>'+
                       '<w:tbl><w:tr><w:tc>'+paragraph('任务')+'</w:tc><w:tc>'+paragraph('10月8日')+'</w:tc></w:tr></w:tbl>')
        read = self.read(data)
        self.assertTrue(read['coverage']['complete'])
        self.assertEqual([b['text'] for b in read['blocks']], ['请提交报告。', '任务', '10月8日'])
        self.assertEqual([b['paragraph'] for b in read['blocks']], [1, 2, 3])
        self.assertIn('行1列2', read['blocks'][2]['location'])

    def test_header_footer_and_notes_are_read(self):
        from ddl_text.docx_reader import W
        body = paragraph(TEXT)+'<w:p><w:r><w:footnoteReference w:id="1"/><w:commentReference w:id="2"/></w:r></w:p><w:sectPr><w:headerReference r:id="h"/><w:footerReference r:id="f"/></w:sectPr>'
        extra = {
            'word/header1.xml': '<w:hdr xmlns:w="'+W[1:-1]+'">'+paragraph('课程通知')+'</w:hdr>',
            'word/footer1.xml': '<w:ftr xmlns:w="'+W[1:-1]+'">'+paragraph('来源标识')+'</w:ftr>',
            'word/footnotes.xml': '<w:footnotes xmlns:w="'+W[1:-1]+'"><w:footnote w:id="1">'+paragraph('补充说明')+'</w:footnote></w:footnotes>',
            'word/comments.xml': '<w:comments xmlns:w="'+W[1:-1]+'"><w:comment w:id="2">'+paragraph('批注意见')+'</w:comment></w:comments>',
        }
        rels = ''.join(relationship(rid, kind, target) for rid,kind,target in
            [('h','header','header1.xml'),('f','footer','footer1.xml'),('n','footnotes','footnotes.xml'),('c','comments','comments.xml')])
        read = self.read(package(body, extra, rels))
        self.assertTrue(read['coverage']['complete'])
        self.assertEqual([b['text'] for b in read['blocks']][-4:], ['课程通知','来源标识','补充说明','批注意见'])

    def test_plain_docx_pipeline_checks_quote_and_retains_file_review(self):
        result = self.run_request(package(paragraph(TEXT)))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['tasks'][0]['deadline']['value'], '2026-10-08T18:00:00+08:00')
        self.assertEqual(result['tasks'][0]['review_reasons'], ['附件引文及定位需核对原件，请确认。'])

    def test_fabricated_quote_page_and_unknown_paragraph_rejected(self):
        data = package(paragraph(TEXT)); request = docx_request(data)
        for kind in ('quote', 'page', 'paragraph'):
            draft = draft_for(request)
            if kind == 'quote': draft['tasks'][0]['evidence'][0]['quote'] += '不存在的要求'
            else: draft['tasks'][0]['evidence'][0]['locator'][kind] = 10
            result = self.run_request(data, draft)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['errors'][0]['code'], 'invalid_agent_output')

    def test_embedded_png_is_decoded_and_can_supply_image_evidence(self):
        data = package(drawing(), {'word/media/image.png':image_bytes()}, relationship('image','image','media/image.png'))
        read = self.read(data)
        self.assertTrue(read['coverage']['complete'])
        self.assertEqual(read['images'][0]['paragraph'], 1)
        result = self.run_request(data)  # Fake quote is allowed only for model-reported image pixels.
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['tasks'][0]['needs_review'])

    def test_external_image_crop_and_missing_picture_are_partial(self):
        variants = [package(paragraph(TEXT)+drawing(), relations=relationship('image','image','https://private.invalid/a.png', True)),
                    package(paragraph(TEXT)+drawing(crop='<a:srcRect l="100"/>'), {'word/media/image.png':image_bytes()}, relationship('image','image','media/image.png')),
                    package(paragraph(TEXT)+drawing(), relations=relationship('image','image','media/missing.png'))]
        for data in variants:
            result = self.run_request(data)
            self.assertEqual(result['status'], 'partial_success')
            self.assertFalse(result['record_results'][0]['coverage']['complete'])

    def test_complex_structure_and_revision_not_silently_dropped(self):
        for extra in ('<w:altChunk r:id="x"/>', '<w:p><w:r><w:object/></w:r></w:p>',
                      '<w:p><w:del><w:r><w:delText>旧截止</w:delText></w:r></w:del></w:p>',
                      '<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:t>旧域值</w:t></w:r></w:p>'):
            data = package(paragraph(TEXT)+extra)
            self.assertEqual(self.run_request(data)['status'], 'partial_success')
            empty = good_draft(); empty['tasks'] = []
            self.assertEqual(self.run_request(data, empty)['status'], 'failed')

    def test_empty_and_clear_no_action_documents_can_be_no_tasks(self):
        draft = good_draft(); draft['tasks'] = []
        for body in ('', paragraph('本页仅介绍课程背景。')):
            self.assertEqual(self.run_request(package(body), draft)['status'], 'no_tasks')

    def test_text_and_paragraph_limits_keep_complete_prefix(self):
        data = package(paragraph(TEXT)+paragraph('稍后还有另一项任务'))
        for limits in (DocxLimits(max_paragraphs=1), DocxLimits(max_text_chars=len(TEXT))):
            read = self.read(data, limits)
            self.assertEqual([b['text'] for b in read['blocks']], [TEXT])
            self.assertTrue(read['coverage']['truncated'])
            result = self.run_request(data, limits=MediaLimits(docx=limits))
            self.assertEqual(result['status'], 'partial_success')

    def test_bounded_zip_xml_receipts_and_unsupported_packages(self):
        data = package(paragraph(TEXT))
        for limits in (DocxLimits(max_entries=1), DocxLimits(max_expanded_bytes=10), DocxLimits(max_xml_bytes=10)):
            with self.assertRaises(AgentError) as caught: self.read(data, limits)
            self.assertEqual(caught.exception.code, 'limit_exceeded')
        with self.assertRaises(MetadataMismatch):
            read_docx(data+b'changed', docx_request(data)['records'][0]['attachments'][0], DocxLimits(), ImageLimits())
        for extra in ({'../../escape':'bad'}, {'word/extra.xml':'x'*1_100_000},
                      {'word/document.xml':'<!DOCTYPE d [<!ENTITY x SYSTEM "file:///private">]><d>&x;</d>'},
                      {'word/document.xml':'broken'}):
            with self.assertRaises(AgentError): self.read(package(paragraph(TEXT), extra))
        data = package(paragraph(TEXT), content_types='<Override PartName="/word/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/>')
        with self.assertRaises(AgentError) as caught: self.read(data)
        self.assertEqual(caught.exception.code, 'reader_unsupported')

    def test_timeout_never_reports_complete_or_no_tasks(self):
        data = package(paragraph(TEXT)); request = docx_request(data)
        result = process_media_request(request, memory_loader(data), FakeMediaClient(behavior='timeout'), Settings(timeout_seconds=0.1))
        check_pair(request, result)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['record_results'][0]['coverage']['complete'])

    def test_invalid_limits(self):
        for kwargs in (dict(max_entries=0), dict(max_images=True), dict(read_seconds=float('inf'))):
            with self.assertRaises(ValueError): DocxLimits(**kwargs)

    def test_duplicates_and_wrong_package_type_are_rejected(self):
        output = io.BytesIO(package(paragraph(TEXT)))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(output, 'a') as archive:
                archive.writestr('word/document.xml', 'duplicate')
        with self.assertRaises(AgentError): self.read(output.getvalue())
        data = package(paragraph(TEXT), {'[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/other"/></Types>'})
        with self.assertRaises(MetadataMismatch): self.read(data)

    def test_struck_or_hidden_text_is_not_a_current_deadline(self):
        for prop in ('strike', 'vanish'):
            prefix = '<w:p><w:r><w:rPr><w:'+prop+'/></w:rPr><w:t>旧截止时间</w:t></w:r></w:p>'
            read = self.read(package(prefix+paragraph(TEXT)))
            self.assertFalse(read['coverage']['complete'])
            self.assertEqual([b['text'] for b in read['blocks']], [TEXT])
            data = package('<w:p><w:r><w:rPr><w:'+prop+' w:val="0"/></w:rPr><w:t>'+TEXT+'</w:t></w:r></w:p>')
            self.assertTrue(self.read(data)['coverage']['complete'])

    def test_image_limit_preserves_text_and_reports_unread_images(self):
        data = package(paragraph(TEXT)+drawing()+drawing(), {'word/media/image.png':image_bytes()},
                       relationship('image','image','media/image.png'))
        read = self.read(data, DocxLimits(max_images=1))
        self.assertEqual(len(read['images']), 1)
        self.assertTrue(read['coverage']['truncated'])
        self.assertEqual(read['error'], 'limit_exceeded')

    def test_final_real_docx_drafts_replay_through_current_compiler(self):
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'docs/archive/2026-10-06-issue94-docx.json'
        report = json.loads(path.read_text(encoding='utf-8'))
        from ddl_text.tests.run_docx_smoke import acceptable
        from ddl_text.media_pipeline import _compile
        from ddl_text.media_schema import parse_draft
        for row in report['results']:
            record = row['request']['records'][0]
            draft = parse_draft(row['raw_draft'], {'asset-001'})
            tasks = _compile(row['request']['request_id'], record, draft, row['result']['record_results'][0]['coverage'])
            self.assertEqual(tasks, row['result']['tasks'])
            self.assertTrue(acceptable(row['synthetic_content'], row['result']))


if __name__ == '__main__':
    unittest.main()

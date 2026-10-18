import hashlib
import json
import time
import unittest
from unittest.mock import Mock, patch

from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.docx_reader import DOCX_MIME
from ddl_text.teaching_attachment_reader import MAX_TEXT, read_attachment
from ddl_text.tests.docx_cases import package, paragraph, drawing, relationship
from ddl_text.tests.media_cases import image_bytes
from ddl_text.tests.pdf_cases import visual_pdf_bytes as pdf_bytes

TEXT = '请于2026年11月17日23:30前提交合成实验报告。'


class AttachmentReaderTests(unittest.TestCase):
    def test_mixed_pdf_reuses_text_pages_and_reads_only_graphical_page(self):
        from ddl_text.tests.pdf_cases import pdf_bytes as plain_pdf
        out,client=self.read(plain_pdf(('Native first page',image_bytes(),'Native third page')),'application/pdf')
        self.assertEqual(out['read_state'],'complete')
        self.assertIn('原件文字：第 1 页',out['text']);self.assertIn('Native third page',out['text'])
        self.assertIn('模型转录：第 2 页',out['text'])
        self.assertEqual(client.complete_json.call_count,1)
        parts=client.complete_json.call_args.args[1]
        self.assertEqual(len(parts),2);self.assertTrue(parts[0]['text'].startswith('第 2 页'))

    def test_long_text_only_pdf_uses_no_transcription_calls(self):
        from ddl_text.tests.pdf_cases import pdf_bytes as plain_pdf
        out,client=self.read(plain_pdf(tuple('Native page '+str(i) for i in range(17))),'application/pdf')
        self.assertEqual(out['read_state'],'complete');self.assertIn('Native page 16',out['text'])
        client.complete_json.assert_not_called()

    def setUp(self):
        # Content tests isolate transport; real wall-clock transport is tested below.
        transport=patch('ddl_text.teaching_attachment_reader.transcribe',
            side_effect=lambda client,instructions,parts,settings:client.complete_json(instructions,parts,settings))
        transport.start();self.addCleanup(transport.stop)

    def read(self, data, mime, pages=None, uncertain=False):
        client=Mock()
        client.complete_json.side_effect=lambda instructions,parts,settings:json.dumps(dict(pages=[dict(index=i+1,text=TEXT,
            uncertain=uncertain,note='右下角被遮挡' if uncertain else '') for i in range(len(parts)//2 if pages is None else pages)]))
        out=read_attachment(data,dict(mime_type=mime,sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data)),
            client,Settings(),expires=time.monotonic()+60)
        return out,client

    def test_docx_text_and_table_context_without_model(self):
        out,client=self.read(package(paragraph(TEXT)),DOCX_MIME)
        self.assertEqual(out['read_state'],'complete');self.assertIn(TEXT,out['text'])
        client.complete_json.assert_not_called()

    def test_png_and_docx_embedded_image_use_visible_content(self):
        data=image_bytes()
        for raw,mime in [(data,'image/png'),(package(drawing(),{'word/media/image.png':data},
                relationship('image','image','media/image.png')),DOCX_MIME)]:
            with self.subTest(mime=mime):
                out,client=self.read(raw,mime)
                self.assertEqual(out['read_state'],'complete');self.assertIn('模型转录',out['text'])
                self.assertIn(TEXT,out['text'])
                parts=client.complete_json.call_args.args[1]
                self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_pdf_batches_cover_seventeen_pages_and_unclear_images_preserve_gaps(self):
        out,client=self.read(pdf_bytes(('Synthetic page',)*17),'application/pdf')
        self.assertEqual(out['read_state'],'complete');self.assertIn('第 17 页',out['text'])
        self.assertEqual(client.complete_json.call_count,3)
        self.assertTrue(all(len(c.args[1])<=12 for c in client.complete_json.call_args_list))
        out,_=self.read(image_bytes(),'image/png',uncertain=True)
        self.assertEqual(out['read_state'],'partial');self.assertIn('右下角被遮挡',out['text'])

    def test_pdf_timeout_resumes_only_unread_pages(self):
        checkpoints=[];client=Mock();seen=[]
        def complete(instructions,parts,settings):
            seen.append(parts[0]['text'])
            if len(seen)==2:raise AgentError('agent_timeout',True)
            return json.dumps(dict(pages=[dict(index=i+1,text=TEXT,uncertain=False) for i in range(len(parts)//2)]))
        client.complete_json.side_effect=complete
        data=pdf_bytes(('Synthetic',)*13)
        asset=dict(mime_type='application/pdf',sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data))
        with self.assertRaises(AgentError):
            read_attachment(data,asset,client,Settings(),expires=time.monotonic()+60,save_checkpoint=checkpoints.append)
        self.assertEqual(len(checkpoints[-1]['pages']),6)
        out=read_attachment(data,asset,client,Settings(),expires=time.monotonic()+60,
                            checkpoint=checkpoints[-1],save_checkpoint=checkpoints.append)
        self.assertEqual(out['read_state'],'complete');self.assertIn('第 13 页',out['text'])
        self.assertEqual(sum(s.startswith('第 1 页') for s in seen),1)
        self.assertEqual(len(checkpoints[-1]['pages']),13)

    def test_pdf_unreadable_later_page_preserves_transcripts_and_gap(self):
        import io
        from pypdf import PdfWriter
        for good_pages in (1,2):
            with self.subTest(good_pages=good_pages):
                writer=PdfWriter()
                for _ in range(good_pages): writer.add_blank_page(width=300,height=300)
                writer.add_blank_page(width=10000,height=10000)
                output=io.BytesIO();writer.write(output)
                out,client=self.read(output.getvalue(),'application/pdf')
                self.assertEqual(out['read_state'],'partial')
                self.assertIn(TEXT,out['text']);self.assertIn('读取缺口',out['text'])
                self.assertIn(f'第 {good_pages+1} 页',out['text'])
                self.assertNotIn(f'【模型转录：第 {good_pages+1} 页】',out['text'])
                client.complete_json.assert_called_once()

    def test_pdf_pixel_budget_continues_instead_of_claiming_unreadable_pages(self):
        from dataclasses import replace
        from ddl_text.pdf_reader import read_pdf
        def render(data,asset,limits):
            return read_pdf(data,asset,replace(limits,max_total_pixels=4_100_000))
        with patch('ddl_text.teaching_attachment_reader.read_pdf',side_effect=render):
            out,client=self.read(pdf_bytes(('Synthetic page',)*3),'application/pdf')
        self.assertEqual(out['read_state'],'complete');self.assertIn('第 3 页',out['text'])
        self.assertEqual([len(c.args[1])//2 for c in client.complete_json.call_args_list],[2,1])

    def test_stop_during_render_does_not_start_a_visual_call(self):
        stopped=[False];client=Mock()
        def guard():
            if stopped[0]: raise RuntimeError('synthetic cancellation')
        def render(*args):
            stopped[0]=True
            return dict(pages=[b'png'],total_pages=1,error=None)
        with patch('ddl_text.teaching_attachment_reader.read_pdf',side_effect=render):
            with self.assertRaisesRegex(RuntimeError,'synthetic cancellation'):
                read_attachment(b'pdf',dict(mime_type='application/pdf'),client,Settings(),
                    expires=time.monotonic()+75,guard=guard,single_pdf_batch=True)
        client.complete_json.assert_not_called()

    def test_pdf_page_limit_keeps_first_128_pages_without_reading_129(self):
        def render(data,asset,limits):
            self.assertLess(limits.start_page,128)
            return dict(pages=[b'png']*limits.max_pages,total_pages=129,error='limit_exceeded')
        with patch('ddl_text.teaching_attachment_reader.read_pdf',side_effect=render):
            out,client=self.read(b'%PDF-synthetic','application/pdf')
        self.assertEqual(out['read_state'],'partial');self.assertEqual(client.complete_json.call_count,22)
        self.assertIn('【模型转录：第 128 页】',out['text'])
        self.assertIn('第 129 页',out['text'])
        self.assertNotIn('【模型转录：第 129 页】',out['text'])

    def test_pdf_text_limit_returns_bounded_partial_with_earlier_text(self):
        client=Mock();client.complete_json.return_value=json.dumps(dict(pages=[
            dict(index=i+1,text=TEXT+'字'*(MAX_TEXT//2),uncertain=False) for i in range(2)]))
        with patch('ddl_text.teaching_attachment_reader.read_pdf',return_value=dict(
                pages=[b'png']*2,total_pages=3,error='limit_exceeded')):
            out=read_attachment(b'pdf',dict(mime_type='application/pdf'),client,Settings(),expires=time.monotonic()+30)
        self.assertEqual(out['read_state'],'partial');self.assertLessEqual(len(out['text']),MAX_TEXT)
        self.assertIn(TEXT,out['text']);self.assertIn('读取缺口',out['text']);self.assertIn('第 3 页',out['text'])

    def test_pdf_resume_at_page_cap_renders_only_remaining_allowed_page(self):
        checkpoint=dict(pdf_checkpoint=1,total_pages=129,pages=[dict(text=TEXT,uncertain=False,note='') for _ in range(127)])
        client=Mock();client.complete_json.return_value=json.dumps(dict(pages=[dict(index=1,text=TEXT,uncertain=False)]))
        def render(data,asset,limits):
            self.assertEqual((limits.start_page,limits.max_pages),(127,1))
            return dict(pages=[b'png'],total_pages=129,error='limit_exceeded')
        with patch('ddl_text.teaching_attachment_reader.read_pdf',side_effect=render):
            out=read_attachment(b'pdf',dict(mime_type='application/pdf'),client,Settings(),
                expires=time.monotonic()+30,checkpoint=checkpoint)
        self.assertEqual(out['read_state'],'partial')
        self.assertIn('【模型转录：第 128 页】',out['text']);self.assertIn('第 129 页',out['text'])

    def test_incomplete_model_output_is_retryable_failure(self):
        with self.assertRaises(AgentError) as error:self.read(image_bytes(),'image/png',pages=0)
        self.assertTrue(error.exception.retryable)

    def test_scanned_pdf_uses_rendered_pages(self):
        import io
        from PIL import Image
        source=Image.open(io.BytesIO(image_bytes())).convert('RGB');stream=io.BytesIO()
        source.save(stream,format='PDF')
        out,client=self.read(stream.getvalue(),'application/pdf')
        self.assertEqual(out['read_state'],'complete');self.assertIn('第 1 页',out['text'])
        client.complete_json.assert_called_once()


class AttachmentTransportTests(unittest.TestCase):
    def test_real_http_slow_drip_is_terminated_and_fast_response_succeeds(self):
        import multiprocessing
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from ddl_text.chat_client import ChatClient
        stop=threading.Event();received=threading.Event();slow=True
        payload=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(
            dict(pages=[dict(index=1,text=TEXT,uncertain=False,note='')]))}}], 'padding':'x'*8000}).encode()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers()
                received.set()
                try:
                    if not slow:self.wfile.write(payload);return
                    for index in range(0,len(payload),8):
                        if stop.wait(.02):return
                        self.wfile.write(payload[index:index+8]);self.wfile.flush()
                except OSError:pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        children={p.pid for p in multiprocessing.active_children()}
        settings=Settings(mode='real',endpoint=f'http://127.0.0.1:{server.server_port}/v1')
        data=image_bytes();asset=dict(mime_type='image/png',sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data))
        try:
            started=time.monotonic()
            with patch('ddl_text.teaching_attachment_reader.read_image',return_value=b'synthetic-png'):
                with self.assertRaises(AgentError) as caught:
                    read_attachment(data,asset,ChatClient('synthetic'),settings,expires=started+4)
            self.assertTrue(received.is_set(),'exercise an established HTTP response, not only process startup')
            self.assertEqual(caught.exception.code,'agent_timeout');self.assertTrue(caught.exception.retryable)
            self.assertLess(time.monotonic()-started,6)
            self.assertFalse({p.pid for p in multiprocessing.active_children()}-children)
            stop.set();slow=False
            with patch('ddl_text.teaching_attachment_reader.read_image',return_value=b'synthetic-png'):
                result=read_attachment(data,asset,ChatClient('synthetic'),settings,expires=time.monotonic()+10)
            self.assertEqual(result['read_state'],'complete');self.assertIn(TEXT,result['text'])
            self.assertFalse({p.pid for p in multiprocessing.active_children()}-children)
        finally:
            stop.set();server.shutdown();server.server_close();thread.join(2)

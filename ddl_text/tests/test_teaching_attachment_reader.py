import hashlib
import json
import time
import unittest
from unittest.mock import Mock, patch

from ddl_text.adapters import AgentError
from ddl_text.config import Settings
from ddl_text.docx_reader import DOCX_MIME
from ddl_text.teaching_attachment_reader import read_attachment
from ddl_text.tests.docx_cases import package, paragraph, drawing, relationship
from ddl_text.tests.media_cases import image_bytes
from ddl_text.tests.pdf_cases import pdf_bytes

TEXT = '请于2026年11月17日23:30前提交合成实验报告。'


class AttachmentReaderTests(unittest.TestCase):
    def setUp(self):
        # Content tests isolate transport; real wall-clock transport is tested below.
        transport=patch('ddl_text.teaching_attachment_reader.transcribe',
            side_effect=lambda client,instructions,parts,settings:client.complete_json(instructions,parts,settings))
        transport.start();self.addCleanup(transport.stop)

    def read(self, data, mime, pages=1, uncertain=False):
        client=Mock()
        client.complete_json.return_value=json.dumps(dict(pages=[dict(index=i+1,text=TEXT,
            uncertain=uncertain,note='右下角被遮挡' if uncertain else '') for i in range(pages)]))
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

    def test_pdf_page_limit_and_unclear_images_preserve_gaps(self):
        out,_=self.read(pdf_bytes(('Synthetic page',)*7),'application/pdf',pages=6)
        self.assertEqual(out['read_state'],'partial');self.assertIn('共 7 页',out['text'])
        out,_=self.read(image_bytes(),'image/png',uncertain=True)
        self.assertEqual(out['read_state'],'partial');self.assertIn('右下角被遮挡',out['text'])

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

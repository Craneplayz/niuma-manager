"""Actual multimodal HTTP serialization against a local fake provider."""
import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings
from ddl_text.media_pipeline import process_media_request
from ddl_text.tests.media_cases import good_draft, request_for, memory_loader


class MediaTransportTests(unittest.TestCase):
    def test_wire_uses_pixels_not_handles_and_redirect_never_forwards_them(self):
        request, data = request_for()
        calls = []
        status = [200]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(status[0])
                if status[0] == 307:
                    self.send_header('Location', '/redirected')
                self.end_headers()
                self.wfile.write(json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(
                    content=json.dumps(good_draft())))] )).encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            settings = Settings(mode='real', model='synthetic-vision-model',
                                endpoint=f'http://127.0.0.1:{server.server_port}/v1', timeout_seconds=5)
            result = process_media_request(request, memory_loader(data), ChatClient('synthetic-key'), settings)
            self.assertEqual(result['status'], 'success', result['errors'])
            self.assertEqual(len(calls), 1)
            payload = calls[0]
            self.assertEqual(payload['model'], 'synthetic-vision-model')
            self.assertNotIn('tools', payload)
            self.assertNotIn('file_ref', json.dumps(payload))
            self.assertNotIn('synthetic-key', json.dumps(payload))
            content = payload['messages'][1]['content']
            pixels = [p['image_url']['url'] for p in content if p['type'] == 'image_url']
            self.assertEqual(len(pixels), 1)
            self.assertTrue(base64.b64decode(pixels[0].split(',', 1)[1]).startswith(b'\x89PNG'))
            self.assertIn('image-task-extraction', payload['messages'][0]['content'])
            status[0] = 307
            failed = process_media_request(request, memory_loader(data), ChatClient('synthetic-key'), settings)
            self.assertEqual(len(calls), 2)
            self.assertEqual(failed['status'], 'failed')
            self.assertIn('agent_unavailable', [e['code'] for e in failed['errors']])
        finally:
            server.shutdown(); server.server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()

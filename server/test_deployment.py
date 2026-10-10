import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db_patch=patch.object(app,'DB',Path(self.tmp.name)/'test.sqlite3')
        self.db_patch.start()
        app.initialize(recover=False)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.db_patch.stop()
        self.tmp.cleanup()

    def request(self,path,body=None,headers=None):
        request=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json',**(headers or {})})
        try:
            return urlopen(request,timeout=5)
        except HTTPError as error:
            return error

    def test_health_does_not_expose_accounts_or_configuration(self):
        with self.request('/api/health') as response:
            self.assertEqual(response.status,200)
            self.assertEqual(json.load(response),{'status':'ok'})
        with self.request('/api/workspace') as response:
            self.assertEqual(response.status,401)

    def test_public_origin_cookie_and_logout(self):
        account=dict(name='部署测试',email='deployment@test.local',password='Synthetic-Test-2026!')
        with patch.dict(app.os.environ,NIUMA_ALLOWED_ORIGINS='https://demo.example.com',NIUMA_COOKIE_SECURE='1'):
            with self.request('/api/auth/register',account,{'Origin':'https://evil.example.com'}) as response:
                self.assertEqual(response.status,403)
            with self.request('/api/auth/register',account,{'Origin':'https://demo.example.com'}) as response:
                self.assertEqual(response.status,200)
                cookie=response.headers['Set-Cookie']
                self.assertIn('; Secure',cookie)
                self.assertIn('; HttpOnly',cookie)
                self.assertIn('; SameSite=Strict',cookie)
            with self.request('/api/auth/logout',{}, {'Origin':'https://demo.example.com','Cookie':cookie.split(';')[0]}) as response:
                self.assertEqual(response.status,200)
                self.assertIn('Max-Age=0; Secure',response.headers['Set-Cookie'])

    def test_local_development_defaults_remain_unchanged(self):
        with patch.dict(app.os.environ,{},clear=True):
            self.assertEqual(app.allowed_origins(),['http://localhost:1331','http://127.0.0.1:1331'])
            self.assertNotIn('Secure',app.session_cookie('test',86400))
            self.assertIn('HttpOnly',app.session_cookie('test',86400))

    def test_forwarded_headers_do_not_authorize_an_origin(self):
        with patch.dict(app.os.environ,NIUMA_ALLOWED_ORIGINS='https://demo.example.com'):
            with self.request('/api/auth/login',{}, {'Origin':'https://evil.example.com',
                 'X-Forwarded-Host':'demo.example.com','X-Forwarded-Proto':'https'}) as response:
                self.assertEqual(response.status,403)

import copy
import io
import json
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch
from zipfile import ZipFile

import app
from auth_test_support import registration_payload
import manual_materials
import wechat_media


class ManualMaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(app.os.environ,NIUMA_ENABLE_WECHAT='0',NIUMA_ENABLE_MANUAL_MATERIALS='1')
        self.env.start()
        self.db=patch.object(app,'DB',Path(self.tmp.name)/'manual.sqlite3')
        self.db.start()
        app.initialize(recover=False)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)+'/api'
        self.client=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call('/auth/register',dict(name='合成测试',email='manual@example.test',password='Synthetic-Test-2026!'))

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        wechat_media.wait_for_jobs(app.DB)
        self.db.stop();self.env.stop();self.tmp.cleanup()

    def call(self,path,body=None,client=None,method=None,headers=None):
        if path == '/auth/register':
            body = registration_payload(body, lambda request:self.call('/auth/email-code',request,client=client,headers=headers))
        data=json.dumps(body).encode() if body is not None else None
        request=Request(self.base+path,data=data,headers={'Content-Type':'application/json',**(headers or {})},method=method)
        return self.send(request,client)

    def send(self,request,client=None):
        try:response=(client or self.client).open(request,timeout=15)
        except HTTPError as error:response=error
        with response:
            raw=response.read()
            return response.status,json.loads(raw) if raw else {}

    def upload(self,data,name='material.bin',client=None):
        boundary='synthetic-material-boundary'
        raw=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+data+f'\r\n--{boundary}--\r\n'.encode())
        return self.send(Request(self.base+'/materials/assets',data=raw,headers={'Content-Type':'multipart/form-data; boundary='+boundary}),client)

    def request(self,receipt,supported=True):
        body=json.loads((Path(__file__).parent.parent/'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
        record=body['records'][0]
        record.update(context=[],source_item_id=None,source_label='用户自行提交的合成材料',published_at=dict(value=None,precision='unknown',basis='unknown'),timezone_basis='caller_default')
        asset=record['attachments'][0]
        asset.update({key:receipt[key] for key in ('file_name','mime_type','size_bytes','sha256','file_ref')})
        if not supported:asset.update(file_ref=None,status='unsupported',errors=[dict(stage='upload',code='unsupported_type',message='当前读取器不支持',retryable=False)])
        return body

    def test_plain_json_and_unknown_attachments_are_accepted(self):
        for data,name in [(b'{"tasks":["synthetic"]}','export.json'),(b'plain synthetic attachment','notes.xyz')]:
            status,receipt=self.upload(data,name)
            self.assertEqual(status,201)
            self.assertEqual(receipt['contract_version'],'manual-materials/0.1')
            self.assertEqual(receipt['mime_type'],'application/octet-stream')
            wechat_media.check_request(self.request(receipt,supported=False))

    def test_png_and_docx_keep_real_mime_types(self):
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as archive:
            archive.writestr('[Content_Types].xml','<Types/>')
            archive.writestr('word/document.xml','<document/>')
        for data,name,mime in [(b'\x89PNG\r\n\x1a\nsynthetic','task.png','image/png'),(buffer.getvalue(),'task.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document')]:
            status,receipt=self.upload(data,name)
            self.assertEqual(status,201)
            self.assertEqual(receipt['mime_type'],mime)
            wechat_media.check_request(self.request(receipt))

    def test_declared_and_known_encrypted_data_never_enters_storage(self):
        samples=[b'{"encrypted":true,"payload":"ciphertext"}',b'{"is_encrypted":true}',b'-----BEGIN PGP MESSAGE-----\nciphertext',b'age-encryption.org/v1\nciphertext',b'Salted__ciphertext',b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'+('EncryptedPackage EncryptionInfo').encode('utf-16le')]
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as archive:archive.writestr('synthetic.txt','synthetic')
        encrypted_zip=bytearray(buffer.getvalue())
        encrypted_zip[6]|=1
        encrypted_zip[encrypted_zip.index(b'PK\x01\x02')+8]|=1
        samples.append(bytes(encrypted_zip))
        for data in samples:
            status,result=self.upload(data)
            self.assertEqual(status,400)
            self.assertIn('加密',result['error']['message'])
        with app.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wechat_assets').fetchone()[0],0)
        with patch.object(app.recognition,'recognize',side_effect=AssertionError('model must not receive encrypted text')):
            for text in ('-----BEGIN PGP MESSAGE-----\nciphertext','{"encrypted":true,"payload":"synthetic"}'):
                self.assertEqual(self.call('/recognition/batches',{'records':[{'platform':'manual','text':text}]})[0],400)

    def test_pdf_security_detection_in_disposable_worker(self):
        import pypdfium2 as pdfium
        buffer=io.BytesIO()
        with pdfium.PdfDocument.new() as document:
            document.new_page(200,200)
            document.save(buffer)
        plain=buffer.getvalue()
        self.assertEqual(self.upload(plain,'plain.pdf')[0],201)
        # An encrypted document must be refused even when its opening password is empty.
        try:from pypdf import PdfWriter
        except ImportError:self.skipTest('optional PDF encryption fixture generator is unavailable')
        for password in ('synthetic-password',''):
            writer=PdfWriter();writer.add_blank_page(width=200,height=200)
            writer.encrypt(password,owner_password='synthetic-owner')
            encrypted=io.BytesIO();writer.write(encrypted)
            self.assertEqual(self.upload(encrypted.getvalue(),'encrypted.pdf')[0],400)

    def test_manual_session_only_and_collector_routes_remain_disabled(self):
        anonymous=build_opener()
        self.assertEqual(self.call('/materials/limits',client=anonymous)[0],401)
        self.assertEqual(self.upload(b'plain',client=anonymous)[0],401)
        with patch.object(app.Handler,'body',side_effect=AssertionError('must not read body')):
            self.assertEqual(self.call('/materials/imports',{},headers={'Authorization':'Bearer synthetic'})[0],403)
            for path in ('credential','assets','imports','imports/job/retry'):
                self.assertEqual(self.call('/sources/wechat/'+path,{})[0],403)
        self.assertEqual(self.call('/materials/limits')[0],200)
        self.assertEqual(self.call('/materials/credential',{})[0],404)

    def test_foreign_assets_and_bad_metadata_rejected(self):
        _,receipt=self.upload(b'\x89PNG\r\n\x1a\nsynthetic','file.png')
        body=self.request(receipt)
        other=build_opener(HTTPCookieProcessor(CookieJar()))
        self.call('/auth/register',dict(name='Other',email='other@example.test',password='Synthetic-Test-2026!'),client=other)
        self.assertEqual(self.call('/materials/imports',body,client=other)[0],404)
        changed=copy.deepcopy(body);changed['records'][0]['attachments'][0]['size_bytes']+=1
        self.assertEqual(self.call('/materials/imports',changed)[0],400)
        self.assertEqual(self.call('/materials/imports',{'records':'invalid'})[0],400)

    def test_manual_job_is_marked_idempotent_and_legacy_jobs_stay_hidden(self):
        _,receipt=self.upload(b'plain synthetic','file.json')
        body=self.request(receipt,supported=False)
        with patch.object(wechat_media,'_start_job') as start:
            status,accepted=self.call('/materials/imports',body)
            self.assertEqual(status,202)
            self.assertEqual(self.call('/materials/imports',body)[0],200)
            start.assert_called_once()
        with app.connection() as db:
            self.assertTrue(db.execute('SELECT 1 FROM manual_material_jobs WHERE job_id=?',(accepted['job_id'],)).fetchone())
            db.execute('DELETE FROM manual_material_jobs')
        self.assertEqual(self.call('/materials/imports')[1]['jobs'],[])
        self.assertEqual(self.call('/materials/imports/'+accepted['job_id'])[0],404)
        self.assertEqual(self.call('/materials/imports/'+accepted['job_id']+'/retry',{'retry_id':'synthetic'})[0],404)

    def test_restart_interrupts_only_manual_jobs_without_model_replay(self):
        _,receipt=self.upload(b'plain synthetic','file.json')
        with patch.object(wechat_media,'_start_job'):
            _,accepted=self.call('/materials/imports',self.request(receipt,supported=False))
        with app.connection() as db:
            row=db.execute('SELECT * FROM wechat_jobs WHERE id=?',(accepted['job_id'],)).fetchone()
            db.execute('INSERT INTO wechat_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',('legacy',row['user_id'],'legacy-request',row['fingerprint'],row['request'],'running',None,None,None,0))
        with patch.object(wechat_media,'resume') as resume:
            app.initialize()
            resume.assert_not_called()
        with app.connection() as db:
            self.assertEqual(db.execute('SELECT status FROM wechat_jobs WHERE id=?',('legacy',)).fetchone()[0],'running')
            self.assertEqual(db.execute('SELECT status FROM wechat_jobs WHERE id=?',(accepted['job_id'],)).fetchone()[0],'failed')

    def test_request_level_failure_has_a_working_idempotent_retry(self):
        _,receipt=self.upload(b'plain synthetic','file.json')
        body=self.request(receipt,supported=False)
        with patch.object(wechat_media,'_start_job'):
            _,accepted=self.call('/materials/imports',body)
        # Reproduce a failure already saved by the older runtime.
        old=dict(contract_version='0.3.1',request_id=body['request_id'],status='failed',record_results=[],tasks=[],
                 errors=[dict(input_id=None,asset_id=None,block_id=None,stage='request',code='agent_unavailable',message='synthetic',retryable=True)])
        with app.connection() as db:db.execute("UPDATE wechat_jobs SET status='failed',result=? WHERE id=?",(json.dumps(old),accepted['job_id']))
        path='/materials/imports/'+accepted['job_id']+'/retry'
        with patch.object(wechat_media,'_start_job') as start:
            self.assertEqual(self.call(path,dict(retry_id='synthetic-retry'))[0],202)
            self.assertEqual(self.call(path,dict(retry_id='synthetic-retry'))[0],200)
            start.assert_called_once()
        with app.connection() as db:
            row=db.execute('SELECT previous_result FROM wechat_jobs WHERE id=?',(accepted['job_id'],)).fetchone()
            previous=json.loads(row['previous_result'])
        wechat_media.check_pair(body,previous)
        self.assertEqual(wechat_media.retryable_records(previous),{body['records'][0]['input_id']})
        self.assertEqual(self.call('/materials/imports')[1]['jobs'][0]['retry_remaining'],2)
        with app.connection() as db:
            db.execute("UPDATE wechat_jobs SET attempts=3,status='failed',result=?,previous_result=NULL WHERE id=?",(json.dumps(previous),accepted['job_id']))
        self.assertEqual(self.call('/materials/imports')[1]['jobs'][0]['retry_remaining'],0)
        self.assertEqual(self.call(path,dict(retry_id='over-limit'))[0],409)
        self.assertEqual(self.call(path,dict(retry_id='synthetic-retry'))[0],200)


if __name__=='__main__':unittest.main()

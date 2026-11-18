import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import test_usage as support


class EvaluationHTTPTests(unittest.TestCase):
    setUp=support.UsageTests.setUp
    tearDown=support.UsageTests.tearDown
    call=support.UsageTests.call

    def grant(self):
        with patch.dict(os.environ,NIUMA_ADMIN_USER_IDS=self.uid):
            self.assertEqual(self.call('/admin/auth/login',dict(email='usage@example.test',password='Usage-Test-2026!'))[0],200)
        with support.app.connection()as db:
            support.usage.initialize(db);db.execute('INSERT OR IGNORE INTO usage_admins VALUES(?)',(support.usage.actor(self.uid),));db.commit()

    def fixture(self):
        directory=Path(self.temp.name)/'model-evaluations';directory.mkdir(exist_ok=True)
        value=dict(id='a'*64,version='benchmark-report/1',sourceVersion='fixture',datasetHash='hash',summary={},rows=[])
        (directory/('a'*64+'.json')).write_text(json.dumps(value));return directory,value

    def test_requires_separate_admin_session(self):
        self.fixture();self.assertEqual(self.call('/admin/evaluations')[0],401)
        self.assertEqual(self.call('/admin/auth/login',dict(email='usage@example.test',password='Usage-Test-2026!'))[0],403)
        self.grant();self.assertEqual(self.call('/admin/evaluations')[0],200)
        self.assertEqual(self.call('/admin/evaluations/'+'a'*64)[0],200)
        self.call('/admin/auth/logout',{});self.assertEqual(self.call('/admin/evaluations')[0],401)

    def test_read_only_missing_and_malformed_paths(self):
        self.fixture();self.grant()
        self.assertEqual(self.call('/admin/evaluations',{})[0],405)
        self.assertEqual(self.call('/admin/evaluations/not-a-run')[0],400)
        self.assertEqual(self.call('/admin/evaluations/'+'b'*64)[0],404)

    def test_rejects_symlink_and_does_not_serve_arbitrary_json(self):
        folder,value=self.fixture();self.grant()
        target=folder/('a'*64+'.json');target.unlink();other=Path(self.temp.name)/'private.json';other.write_text(json.dumps(value));target.symlink_to(other)
        self.assertEqual(self.call('/admin/evaluations/'+'a'*64)[0],400)

    def test_lazy_rows_export_and_revoked_admin(self):
        directory,value=self.fixture();self.grant()
        row=dict(id='case-1',model='model/a',input='synthetic input',expected={'count':1},output={'tasks':[]},call_usage=[{'total_tokens':5}],checks={'contract':True})
        value['rows']=[row];(directory/('a'*64+'.json')).write_text(json.dumps(value))
        url='/admin/evaluations/'+'a'*64
        code,body=self.call(url)
        self.assertEqual(code,200);self.assertNotIn('input',body['rows'][0]);self.assertNotIn('call_usage',body['rows'][0])
        self.assertEqual(self.call(url+'?case=case-1&model=model%2Fa')[1]['row'],row)
        self.assertEqual(self.call(url+'?full=1')[1]['rows'],[row])
        self.assertEqual(self.call(url+'?case=case-1')[0],400)
        self.assertEqual(self.call(url+'?case=missing&model=model%2Fa')[0],404)
        with support.app.connection()as db:
            db.execute('DELETE FROM usage_admins');db.commit()
        self.assertEqual(self.call(url+'?full=1')[0],403)

if __name__=='__main__':unittest.main()

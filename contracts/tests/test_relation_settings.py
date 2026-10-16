import unittest
from jsonschema import ValidationError
from contracts import relation_settings as c

class SettingsContractTests(unittest.TestCase):
    def test_modes_and_strict_update(self):
        value=dict(contract_version=c.VERSION,source_account_id='account:synthetic',revision=0,mode='manual')
        c.check(value)
        c.check(dict(value,mode='automatic',consent=True),update=True)
        for change in ({'mode':'unknown'},{'revision':True},{'revision':-1},{'account':'foreign'},{'consent':'yes'}):
            with self.subTest(change=change),self.assertRaises(ValidationError):c.check(dict(value,consent=True,**change) if 'consent' not in change else dict(value,**change),update=True)

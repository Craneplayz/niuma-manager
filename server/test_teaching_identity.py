"""Source revisions must depend on content, not order or reader outcomes."""
from copy import deepcopy
import unittest
from teaching_identity import source_digest
from ddl_text.tests.test_teaching_pipeline import example


class SourceIdentityTests(unittest.TestCase):
    def test_order_read_status_and_collection_time_do_not_change_file_identity(self):
        entry=example()[0]['records'][0]
        entry['record']['references']=[dict(ref_id='file:1',label='A',kind='attachment',read_state='partial',text='Captured'),
                                       dict(ref_id='link:1',label='B',kind='linked_detail',read_state='unread',text=None)]
        files=[dict(ref_id='file:1',sha256='1'*64,mime_type='application/pdf')]
        before=source_digest(entry,None,files)
        other=deepcopy(entry);other['record']['collected_at']='2026-10-19T10:00:00+08:00'
        other['record']['references'][0].update(read_state='failed',text=None)
        other['record']['references'].reverse()
        self.assertEqual(before,source_digest(other,None,files))
        self.assertNotEqual(before,source_digest(other,None,[dict(files[0],sha256='2'*64)]))
        other['record']['text']+=' New requirement'
        self.assertNotEqual(before,source_digest(other,None,files))

    def test_missing_file_and_changed_assignment_are_not_hidden(self):
        entry=example()[0]['records'][0]
        files=[dict(ref_id='file:1',sha256='1'*64,mime_type='application/pdf')]
        entry['record']['references']=[dict(ref_id='file:1',label='A',kind='attachment',read_state='unread',text=None)]
        self.assertNotEqual(source_digest(entry,None,files),source_digest(entry,None,[]))
        self.assertNotEqual(source_digest(entry,{'state':'active'},files),source_digest(entry,{'state':'submitted'},files))

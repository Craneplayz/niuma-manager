"""Task/notice classification travels through the email v0.2 compiler."""
import json
from pathlib import Path
import unittest
from ddl_text.adapters import AgentError
from ddl_text.contracts import normalize_record
from ddl_text.contract_v02 import validate_result
from ddl_text.draft_schema import draft_schema
from ddl_text.extraction import compile_drafts, load_instructions
from ddl_text.content_types import content_type_instructions
from ddl_text.tests.test_extraction import draft


class EmailContentTypeTests(unittest.TestCase):
    def record(self):
        request=json.loads((Path(__file__).resolve().parents[2]/'contracts/source-to-text/text-frontend-v02-request.json').read_text())
        r=request['records'][0]
        r.update(platform='email',text='上课地点调整到理教408。请回复确认是否参加。',course_or_project=None,context=[],upstream_hints=None)
        return normalize_record(r,'0.2')

    def test_email_schema_requires_type_and_both_prompts_share_definitions(self):
        r=self.record();schema=draft_schema(r)['properties']['tasks']['items']
        self.assertIn('content_type',schema['required'])
        self.assertEqual(schema['properties']['content_type']['enum'],['task','notice'])
        self.assertIn(content_type_instructions(),load_instructions(r))
        from ddl_text.teaching_draft import teaching_instructions
        self.assertIn(content_type_instructions(),teaching_instructions())
        r['platform']='manual'
        self.assertNotIn('content_type',draft_schema(r)['properties']['tasks']['items']['properties'])

    def test_mixed_notice_and_undated_action_survive_compilation_and_contract(self):
        r=self.record()
        proposals=[draft(r,title='上课地点调整到理教408',content_type='notice'),draft(r,title='回复参加确认',content_type='task')]
        tasks=compile_drafts(json.dumps({'tasks':proposals}),r)
        self.assertEqual([t['content_type'] for t in tasks],['notice','task'])
        self.assertTrue(all(t['deadline']['value'] is None for t in tasks))
        result=dict(contract_version='0.2',request_id='mixed',status='success',record_results=[dict(input_id=r['input_id'],status='success',task_ids=[t['candidate_id'] for t in tasks],warnings=[])],tasks=tasks,errors=[])
        source={k:v for k,v in r.items() if k!='contract_version'}
        request=dict(contract_version='0.2',request_id='mixed',records=[source])
        validate_result(request,result)
        from server.text_contract import validate_result as server_validate
        server_validate(request,result)

    def test_invalid_type_rejected_and_old_email_draft_defaults_to_task(self):
        r=self.record()
        with self.assertRaises(AgentError):compile_drafts(json.dumps({'tasks':[draft(r,content_type='event')]}),r)
        tasks=compile_drafts(json.dumps({'tasks':[draft(r)]}),r)
        self.assertEqual(tasks[0]['content_type'],'task')

    def test_notice_evidence_still_must_be_in_source(self):
        r=self.record();d=draft(r,content_type='notice')
        d['evidence']=[{'input_id':r['input_id'],'quote':'INVENTED_NOTICE'}]
        with self.assertRaises(AgentError):compile_drafts(json.dumps({'tasks':[d]}),r)

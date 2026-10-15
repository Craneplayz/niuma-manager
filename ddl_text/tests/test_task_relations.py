"""Boundary tests use synthetic sources; semantic quality has separate fixtures."""
from copy import deepcopy
import json
import unittest
from jsonschema import ValidationError
from contracts import task_relations as c
from ddl_text.task_relations import RelationAdapter
from ddl_text.adapters import AgentError


def example():
    request = c.context([
        dict(id='a', kind='candidate', text='完成实验甲报告。', course='实验课'),
        dict(id='b', kind='task', text='完成实验乙报告。', course='实验课'),
        dict(id='s', kind='source', text='提交实验甲和乙的报告；演示不需提交。', course='实验提交入口'),
    ])
    result = dict(contract_version=c.VERSION, context_digest=request['context_digest'], groups=[dict(
        relation='combine', disposition='apply', members=['a','b','s'],
        result=dict(title='提交甲乙实验报告', description='仅提交报告，不含演示。', course='实验课',
                    content_type='task', due=dict(date='2026-11-03', time='23:00')),
        reason='同一提交入口覆盖两份报告',
        evidence=[dict(node_id=n['id'], quote=n['text']) for n in request['nodes']],
        completion=[dict(source_id='s', covers=['a','b'])])])
    return request, result


class Client:
    def __init__(self, response): self.response = response
    def complete_json(self, instructions, payload, settings): return self.response


class RelationsContractTests(unittest.TestCase):
    def test_many_to_one_cross_course(self):
        request,result=example()
        self.assertEqual(c.check_pair(request,result),result)
        self.assertNotIn('completed',result['groups'][0])

    def test_independent_members_may_be_left_out(self):
        request,result=example();result['groups']=[]
        c.check_pair(request,result)

    def test_partial_completion_is_representable_not_whole_group(self):
        request,result=example();result['groups'][0]['completion'][0]['covers']=['a']
        c.check_pair(request,result)

    def test_mutations_rejected(self):
        mutations=[
            lambda r:r.update(context_digest='0'*64),
            lambda r:r['groups'][0]['members'].append('foreign'),
            lambda r:r['groups'][0]['members'].append('a'),
            lambda r:r['groups'][0]['evidence'][0].update(quote='invented'),
            lambda r:r['groups'][0]['evidence'].pop(0),
            lambda r:r['groups'][0]['completion'][0].update(source_id='a'),
            lambda r:r['groups'][0]['completion'][0].update(covers=['foreign']),
            lambda r:r['groups'].append(deepcopy(r['groups'][0])),
            lambda r:r['groups'][0]['result']['due'].update(date='2026-02-30'),
            lambda r:r['groups'][0]['result']['due'].update(time='25:00'),
            lambda r:r['groups'][0]['result']['due'].update(time='9:000'),
            lambda r:r['groups'][0].update(completed=True),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                request,result=example();mutate(result)
                with self.assertRaises((ValueError,ValidationError)):c.check_pair(request,result)

    def test_context_guards(self):
        request,result=example()
        for nodes in ([],request['nodes']*2,[dict(id='x',kind='source',text='x'*20001)]):
            with self.assertRaises(ValueError):c.context(nodes)
        request['nodes'][0]['text']='changed'
        with self.assertRaises(ValueError):c.check_pair(request,result)

    def test_date_precision_and_notice_deadline(self):
        for due in (None,dict(date='2026-11-03',time=None)):
            request,result=example();result['groups'][0]['result'].update(content_type='notice',due=due)
            c.check_pair(request,result)

    def test_adapter_parses_json_and_redacts_failures(self):
        request,result=example()
        self.assertEqual(RelationAdapter(Client(json.dumps(result))).extract(request,None,0),[result])
        for raw in ('private raw error','{"groups":[],"groups":[]}',json.dumps(dict(result,private=True))):
            with self.subTest(raw=raw),self.assertRaises(AgentError) as error:
                RelationAdapter(Client(raw)).extract(request,None,0)
            self.assertEqual(error.exception.code,'invalid_agent_output')


if __name__=='__main__':unittest.main()

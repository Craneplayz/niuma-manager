"""Boundary tests use synthetic sources; semantic quality has separate fixtures."""
from copy import deepcopy
import json
import unittest
from jsonschema import ValidationError
from contracts import task_relations as c
from ddl_text.task_relations import RelationAdapter,evidence_context,model_schema
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
    def complete_json(self, instructions, payload, settings):
        value=json.loads(payload)
        if 'proposals' in value:
            return json.dumps(dict(checks=[dict(index=p['index'],approved=True,reason='合成交付证据完整') for p in value['proposals']]))
        return self.response


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

    def test_context_only_noise_does_not_discard_action_groups(self):
        request,result=example();noisy=deepcopy(result)
        noisy['groups'].extend([dict(members=['s']), dict(members=['s','s'],reason='context only')])
        self.assertEqual(RelationAdapter(Client(json.dumps(noisy))).extract(request,None,0),[result])
        for members in (['a'],['unknown'],['s','unknown']):
            bad=deepcopy(result);bad['groups'].append(dict(members=members))
            with self.assertRaises(AgentError):RelationAdapter(Client(json.dumps(bad))).extract(request,None,0)

    def test_excerpt_references_resolve_to_original_not_summary(self):
        request,result=example();wire,catalog=evidence_context(request)
        response=deepcopy(result)
        for item in response['groups'][0]['evidence']:
            item.pop('quote');item['excerpt_id']='e0'
        self.assertEqual(RelationAdapter(Client(json.dumps(response))).extract(request,None,0),[result])
        self.assertNotIn('text',wire['nodes'][0])
        self.assertIn('ref',model_schema()['properties']['groups']['items']['properties']['evidence']['items']['properties'])
        for identity in ('unknown','e1'):
            bad=deepcopy(response);bad['groups'][0]['evidence'][0]['excerpt_id']=identity
            with self.assertRaises(AgentError):RelationAdapter(Client(json.dumps(bad))).extract(request,None,0)
        for raw in ('null','[]','{"groups":[null]}'):
            with self.assertRaises(AgentError):RelationAdapter(Client(raw)).extract(request,None,0)

    def test_model_short_ids_are_resolved_before_database_contract(self):
        request,result=example()
        class AliasClient:
            def complete_json(self,instructions,payload,settings):
                if 'proposals' in json.loads(payload):return Client('').complete_json(instructions,payload,settings)
                nodes=json.loads(payload)['context']['nodes'];ids=[n['id'] for n in nodes]
                answer=deepcopy(result);g=answer['groups'][0]
                g['members']=ids;g['evidence']=[dict(node_id=i,excerpt_id='e0') for i in ids]
                g['completion']=[dict(source_id=ids[2],covers=ids[:2])]
                return json.dumps(answer)
        self.assertEqual(RelationAdapter(AliasClient()).extract(request,None,0),[result])

    def test_global_reference_binds_node_and_excerpt_without_cross_pair_guess(self):
        request,result=example()
        class GlobalClient(Client):
            def complete_json(self,instructions,payload,settings):
                data=json.loads(payload)
                if 'proposals' in data:return super().complete_json(instructions,payload,settings)
                answer=deepcopy(result);answer.pop('context_digest');answer.pop('contract_version')
                g=answer['groups'][0];nodes=data['context']['nodes']
                g.pop('members')
                g['actions']=[dict(id=n['id'],outcome='required') for n in nodes[:2]]
                g['evidence']=[dict(ref=n['excerpts'][0]['id']) for n in nodes if n['kind']=='source']
                g['completion']=[dict(source_id=nodes[2]['id'],covers=[n['id'] for n in nodes[:2]])]
                self_ids=[e['id'] for n in nodes for e in n.get('excerpts',[])]
                assert len(self_ids)==len(set(self_ids))
                assert all('excerpts' not in n for n in nodes[:2])
                return json.dumps(answer)
        answer=RelationAdapter(GlobalClient('')).extract(request,None,0)[0]
        self.assertEqual(answer['groups'][0]['completion'],result['groups'][0]['completion'])
        self.assertEqual({e['node_id'] for e in answer['groups'][0]['evidence']},{'a','b','s'})

    def test_verifier_can_reject_structurally_valid_destructive_merge(self):
        request,result=example()
        class Reject(Client):
            def complete_json(self,instructions,payload,settings):
                if 'proposals' in json.loads(payload):
                    return json.dumps(dict(checks=[dict(index=p['index'],approved=False,reason='独立提交要求，不属于同一交付') for p in json.loads(payload)['proposals']]))
                return self.response
        checked=RelationAdapter(Reject(json.dumps(result))).extract(request,None,0)[0]
        self.assertEqual(checked['groups'][0]['disposition'],'review')
        self.assertIn('独立提交',checked['groups'][0]['reason'])

    def test_single_json_fence_is_accepted_but_extra_prose_and_duplicate_keys_are_not(self):
        request,result=example()
        answer=RelationAdapter(Client('```json\n'+json.dumps(result)+'\n```')).extract(request,None,0)[0]
        self.assertEqual(answer,result)
        for raw in ('explanation\n'+json.dumps(result),'```json\n{"groups":[],"groups":[]}\n```'):
            with self.assertRaises(AgentError):RelationAdapter(Client(raw)).extract(request,None,0)

    def test_missing_verification_never_falls_back_to_apply(self):
        request,result=example()
        class Broken(Client):
            def complete_json(self,instructions,payload,settings):
                if 'proposals' in json.loads(payload):return '{"checks":[]}'
                return self.response
        with self.assertRaises(AgentError) as error:RelationAdapter(Broken(json.dumps(result))).extract(request,None,0)
        self.assertEqual(error.exception.diagnostic,'relation_verification')

    def test_compiler_derives_members_and_keeps_explicit_receiver_waivers(self):
        request,result=example()
        class SelectionClient(Client):
            def complete_json(self,instructions,payload,settings):
                data=json.loads(payload)
                if 'proposals' in data:return super().complete_json(instructions,payload,settings)
                nodes=data['context']['nodes'];g=deepcopy(result['groups'][0]);g.pop('members')
                g['actions']=[dict(id=nodes[0]['id'],outcome='required'),dict(id=nodes[1]['id'],outcome='waived_by_receiver')]
                g['evidence']=[dict(ref=nodes[2]['excerpts'][0]['id'])]
                g['completion']=[dict(source_id=nodes[2]['id'],covers=[n['id'] for n in nodes[:2]])]
                return json.dumps(dict(groups=[g]))
        answer=RelationAdapter(SelectionClient('')).extract(request,None,0)[0]
        c.check_pair(request,answer)
        self.assertEqual(set(answer['groups'][0]['members']),{'a','b','s'})
        self.assertEqual(answer['groups'][0]['waived_actions'],['b'])
        self.assertEqual({q['node_id'] for q in answer['groups'][0]['evidence']},{'a','b','s'})

    def test_own_source_rewording_does_not_promote_review_candidate(self):
        request,result=example();request['nodes'][0]['sources']=['s'];request=c.context(request['nodes'])
        result['context_digest']=request['context_digest'];g=result['groups'][0]
        g['members']=['a','s'];g['completion']=[];g['evidence']=[q for q in g['evidence'] if q['node_id']!='b']
        answer=RelationAdapter(Client(json.dumps(result))).extract(request,None,0)[0]
        self.assertEqual(answer['groups'],[])
        g['completion']=[dict(source_id='s',covers=['a'])]
        self.assertEqual(RelationAdapter(Client(json.dumps(result))).extract(request,None,0),[result])

    def test_omitted_excerpt_is_not_selectable(self):
        request=c.context([dict(id='s',kind='source',text='x'*6000)])
        wire,catalog=evidence_context(request)
        self.assertEqual([e['id'] for e in wire['nodes'][0]['excerpts']],['e0','e1','e2','e8','e9'])
        self.assertNotIn(('s','e3'),catalog)
        self.assertEqual(wire['nodes'][0]['omitted_middle_excerpts'],5)

    def test_explicit_joint_sources_require_each_citation(self):
        request,result=example()
        request=c.context(request['nodes']+[dict(id='s2',kind='source',text='第二份报告的接收入口。')])
        result['context_digest']=request['context_digest'];g=result['groups'][0]
        g['members'].append('s2');g['evidence'].append(dict(node_id='s2',quote='第二份报告的接收入口。'))
        g['completion']=[dict(source_ids=['s','s2'],covers=['a','b'])]
        c.check_pair(request,result)
        for ids in (['s'],['s','s'],['s','a'],['s','foreign']):
            bad=deepcopy(result);bad['groups'][0]['completion'][0]['source_ids']=ids
            with self.assertRaises((ValueError,ValidationError)):c.check_pair(request,bad)
        g['evidence'].pop()
        with self.assertRaises(ValueError):c.check_pair(request,result)


if __name__=='__main__':unittest.main()

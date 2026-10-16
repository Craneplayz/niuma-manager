"""Bounded semantic relation call; no credentials in inputs and no persistence."""
import json
from copy import deepcopy
from pathlib import Path
from jsonschema import ValidationError
from contracts import task_relations as contract
from .adapters import AgentError
from .extraction import _object
from .runner import run_attempt

SKILL = Path(__file__).parent / 'skills/task-relations/SKILL.md'


def response_json(raw):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>contract.MAX_BYTES:raise ValueError('invalid size')
    raw=raw.strip()
    # A single JSON fence is presentation, not an invalid semantic result.
    # Arbitrary prose, multiple blocks and duplicate keys remain rejected.
    lines=raw.splitlines()
    if len(lines)>=3 and lines[0].lower() in ('```json','```') and lines[-1]=='```':raw='\n'.join(lines[1:-1])
    return json.loads(raw,object_pairs_hook=_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def evidence_context(request):
    """Present selectable original excerpts, not model-authored quote strings."""
    wire=deepcopy(request);catalog={}
    for node in wire['nodes']:
        text=node.pop('text');excerpts=[]
        for offset in range(0,len(text),600):
            identity='e'+str(offset//600)
            quote=text[offset:offset+600]
            excerpts.append(dict(id=identity,text=quote));catalog[(node['id'],identity)]=quote
        node['excerpts']=excerpts
        if node['kind']=='source' and len(excerpts)>5:
            node['excerpts']=excerpts[:3]+excerpts[-2:]
            node['omitted_middle_excerpts']=len(excerpts)-5
            for excerpt in excerpts[3:-2]:catalog.pop((node['id'],excerpt['id']))
        # Actions carry their current summaries and source IDs; identical raw
        # text is available on those source nodes. Keep a short own citation so
        # the model need not read the same PDF repeatedly for every derived task.
        if node['kind']!='source' and len(excerpts)>2 and node.get('sources'):
            source_texts={n['id']:n['text'] for n in request['nodes'] if n['kind']=='source'}
            if any(text==source_texts.get(sid) for sid in node['sources']):
                node['excerpts']=excerpts[:2];node['more_evidence_in_sources']=True
                for excerpt in excerpts[2:]:catalog.pop((node['id'],excerpt['id']))
    return wire,catalog


def model_schema(wire=None):
    schema=deepcopy(contract.OUTPUT)
    # The model selects one globally unique reference, rather than independently
    # guessing a node and its locally numbered excerpt (a common invalid pair).
    schema['properties'].pop('contract_version');schema['properties'].pop('context_digest')
    schema['required']=['groups']
    group=schema['properties']['groups']['items']
    group['properties'].pop('members');group['properties'].pop('waived_actions',None)
    group['properties']['actions']=dict(type='array',minItems=1,maxItems=24,items=contract.obj(dict(
        id=contract.ID,outcome=dict(enum=['required','waived_by_receiver']))))
    group['required']=[k for k in group['required'] if k!='members']+['actions']
    schema['properties']['groups']['items']['properties']['evidence']['items']=contract.obj(
        dict(ref=contract.string(32,1)))
    schema['properties']['groups']['items']['properties']['evidence']['maxItems']=24
    if wire is not None:
        nodes=wire['nodes'];ids=[n['id'] for n in nodes]
        actions=[n['id'] for n in nodes if n['kind']!='source']
        receivers=[n['id'] for n in nodes if n.get('submission_state') in ('active','submitted')]
        group=schema['properties']['groups']['items']['properties']
        group['actions']['items']['properties']['id']=dict(enum=actions)
        group['evidence']['items']['properties']['ref']=dict(enum=[e['id'] for n in nodes if n['kind']=='source' for e in n['excerpts']])
        if receivers:
            for alternative in group['completion']['items']['oneOf']:
                properties=alternative['properties']
                if 'source_id' in properties:properties['source_id']=dict(enum=receivers)
                else:properties['source_ids']['items']=dict(enum=receivers)
                properties['covers']['items']=dict(enum=actions)
        else:group['completion']['maxItems']=0
    return schema


def reference_context(request):
    wire,legacy=evidence_context(request)
    wire,aliases=alias_context(wire)
    references={}
    for node in wire['nodes']:
        if node['kind']!='source':
            # Actions are old extraction results, not a second evidence source.
            # Their authoritative originals are already present on source nodes.
            node.pop('excerpts',None)
            continue
        for excerpt in node['excerpts']:
            identity='r'+str(len(references))
            references[identity]=dict(node_id=aliases[node['id']],quote=excerpt['text'])
            excerpt['id']=identity
    return wire,aliases,legacy,references


def bind_references(result,references):
    result=deepcopy(result)
    if not isinstance(result,dict) or not isinstance(result.get('groups'),list):return result
    for group in result['groups']:
        if not isinstance(group,dict) or not isinstance(group.get('evidence'),list):continue
        evidence=[]
        for item in group['evidence']:
            if isinstance(item,dict) and 'ref' in item:
                if set(item)!={'ref'} or item['ref'] not in references:raise ValueError('unknown evidence excerpt')
                item=deepcopy(references[item['ref']])
            evidence.append(item)
        group['evidence']=evidence
    return result


def compile_groups(result,request,aliases):
    """Compile selected actions and source evidence into the durable graph.

    Membership and citations are no longer three independent lists for the model
    to keep consistent. Never infer an action from title similarity or rationale.
    """
    result=resolve_aliases(result,aliases)
    if not isinstance(result,dict) or not isinstance(result.get('groups'),list):return result
    nodes={n['id']:n for n in request['nodes']}
    for group in result['groups']:
        if not isinstance(group,dict) or 'actions' not in group:continue
        actions=group.pop('actions')
        if not isinstance(actions,list) or not actions:raise ValueError('missing or overlapping actions')
        ids=[];waived=[]
        for action in actions:
            if not isinstance(action,dict) or set(action)!={'id','outcome'}:raise ValueError('invalid action')
            identity=aliases.get(action['id'],action['id'])
            if identity not in nodes or nodes[identity]['kind']=='source' or identity in ids:raise ValueError('unknown relation member')
            if action['outcome'] not in ('required','waived_by_receiver'):raise ValueError('invalid action')
            ids.append(identity)
            if action['outcome']=='waived_by_receiver':waived.append(identity)
        if 'members' in group:raise ValueError('duplicate membership')
        group['members']=list(ids)
        if waived:group['waived_actions']=waived
        for item in group.get('evidence',[]):
            identity=item['node_id']
            if identity not in nodes or nodes[identity]['kind']!='source':raise ValueError('invalid evidence reference')
            if identity not in group['members']:group['members'].append(identity)
        for link in group.get('completion',[]):
            if not set(link['covers'])<=set(ids):raise ValueError('invalid completion scope')
            for identity in contract.completion_sources_for(link):
                if identity not in nodes or nodes[identity]['kind']!='source':raise ValueError('invalid completion source')
                if identity not in group['members']:group['members'].append(identity)
                if not any(q['node_id']==identity for q in group['evidence']):
                    group['evidence'].append(dict(node_id=identity,quote=nodes[identity]['text'][:1200]))
        # Server-bound action originals identify exactly which saved actions are
        # replaced; the independently selected source citations prove the claim.
        group['evidence'].extend(dict(node_id=i,quote=nodes[i]['text'][:1200]) for i in ids)
    return result


def alias_context(wire):
    wire=deepcopy(wire)
    aliases={n['id']:('s' if n['kind']=='source' else 'a')+str(i) for i,n in enumerate(wire['nodes'])}
    for node in wire['nodes']:
        node['id']=aliases[node['id']]
        node['title']=node.get('summary',{}).get('title') or (node['excerpts'][0]['text'].split('\n',1)[0] if node.get('excerpts') else '')
        if node['kind']=='source':node['role']='receiving_endpoint' if node.get('submission_state') in ('active','submitted') else 'reference_material'
        if 'sources' in node:node['sources']=[aliases[s] for s in node['sources'] if s in aliases]
    return wire,{alias:identity for identity,alias in aliases.items()}


def resolve_aliases(result,aliases):
    result=deepcopy(result)
    if not isinstance(result,dict) or not isinstance(result.get('groups'),list):return result
    for group in result['groups']:
        if not isinstance(group,dict):continue
        if isinstance(group.get('members'),list):group['members']=[aliases.get(i,i) for i in group['members']]
        for evidence in group.get('evidence',[]) or []:
            if isinstance(evidence,dict) and 'node_id' in evidence:
                evidence['node_id']=aliases.get(evidence['node_id'],evidence['node_id'])
        for link in group.get('completion',[]) or []:
            if not isinstance(link,dict):continue
            if 'source_id' in link:link['source_id']=aliases.get(link['source_id'],link['source_id'])
            for field in ('source_ids','covers'):
                if isinstance(link.get(field),list):link[field]=[aliases.get(i,i) for i in link[field]]
    return result


def resolve_evidence(result,catalog):
    result=deepcopy(result)
    if not isinstance(result,dict) or not isinstance(result.get('groups'),list):
        raise ValueError('invalid output structure')
    for group in result.get('groups',[]):
        if not isinstance(group,dict) or not isinstance(group.get('evidence'),list):
            raise ValueError('invalid output structure')
        for evidence in group.get('evidence',[]):
            if not isinstance(evidence,dict):raise ValueError('invalid evidence reference')
            if 'excerpt_id' not in evidence:continue  # Compatible with saved 0.1 responses.
            if set(evidence)!={'node_id','excerpt_id'}:raise ValueError('invalid evidence reference')
            quote=catalog.get((evidence['node_id'],evidence['excerpt_id']))
            if quote is None:raise ValueError('unknown evidence excerpt')
            evidence.pop('excerpt_id');evidence['quote']=quote
    return result


def actionable_result(request, result):
    """Ignore known context-only groups: they have no writable destination.

    Do not repair unknown IDs, action membership, evidence or semantic decisions.
    An invalid group containing any action still goes through correction/failure.
    """
    if isinstance(result, dict) and isinstance(result.get('groups'), list):
        sources = {n['id'] for n in request['nodes'] if n['kind'] == 'source'}
        nodes={n['id']:n for n in request['nodes']}
        def context_only(group):
            members = group.get('members') if isinstance(group, dict) else None
            if not isinstance(members,list) or not members or not all(isinstance(i,str) and i in nodes for i in members):return False
            actions=[i for i in members if i not in sources]
            if not actions:return True
            # Rewording an action from its own source is extraction, not a new
            # relationship. It must not bypass that candidate's review decision.
            return (len(actions)==1 and 'sources' in nodes[actions[0]] and group.get('completion')==[]
                    and set(members)-set(actions)<=set(nodes[actions[0]]['sources']))
        result = dict(result, groups=[g for g in result['groups'] if not context_only(g)])
    return result


def correction_reason(error):
    if isinstance(error, ValidationError):
        # Paths and validators are structural; never include raw private values.
        path = '.'.join(str(p) for p in error.absolute_path)
        return 'Schema ' + str(error.validator) + ' at ' + path[:160]
    return str(error) if str(error) in {
        'stale context', 'unknown relation member', 'missing or overlapping actions',
        'unknown or fabricated evidence', 'uncited action', 'invalid completion source',
        'invalid completion scope', 'duplicate completion requirement', 'invalid date',
        'invalid time', 'empty title or reason','invalid evidence reference',
        'unknown evidence excerpt'} else 'invalid JSON or output structure'


class RelationAdapter:
    def __init__(self, client):
        self.client = client

    def extract(self, request, settings, attempt):
        wire,aliases,catalog,references=reference_context(request)
        instructions = SKILL.read_text(encoding='utf-8') + '\nJSON Schema:\n' + json.dumps(model_schema(wire))
        payload=json.dumps(dict(context=wire, action_ids=[n['id'] for n in wire['nodes']
                           if n['kind'] != 'source']),ensure_ascii=False)
        repair=0;fallback=None
        while True:
            response = self.client.complete_json(instructions, payload, settings)
            try:
                result = response_json(response)
                result=bind_references(result,references)
                if isinstance(result,dict):
                    result.setdefault('contract_version',request['contract_version'])
                    result.setdefault('context_digest',request['context_digest'])
                result = actionable_result(request, compile_groups(result,request,aliases))
                result = resolve_evidence(result,catalog)
                contract.check_pair(request, result)
                verified=self.verify(request,deepcopy(result),settings)
                rejected=[dict(index=i,reason=g['reason']) for i,g in enumerate(verified['groups'])
                          if result['groups'][i]['disposition']=='apply' and g['disposition']=='review']
                if rejected and not repair:
                    repair+=1;fallback=verified
                    payload=json.dumps(dict(context=wire,verification_errors=rejected,previous_output=response[:80_000],
                        correction_request='逐条对照原行动完整范围修正关联，返回完整groups JSON。不得把总提醒缩成某个部分，不得混入其他交付。仍不明确时保留review或不关联。actions与source ref编号沿用当前context。'),ensure_ascii=False)
                    continue
                return [verified]
            except (ValueError, TypeError, KeyError, ValidationError) as error:
                if repair:
                    if fallback is not None:return [fallback]
                    diagnostic='draft_evidence' if correction_reason(error) in ('unknown evidence excerpt','uncited action','invalid completion scope','unknown or fabricated evidence') else 'draft_schema'
                    raise AgentError('invalid_agent_output', True, diagnostic) from None
                repair+=1
                # One correction shares the same disposable-process deadline.
                # Never 'fix' semantic relations in Python or expose private errors.
                payload=json.dumps(dict(context=wire,validation_error=correction_reason(error),correction_request=
                    '按下面的上次输出修正并返回完整JSON。根字段只需groups；不要加Schema以外的字段。每组actions列出所有相关行动及required/waived_by_receiver。evidence只选source的ref，不要引用行动片段，不输出members。completion.covers只能引用本组actions。独立交付不要合并。',
                    previous_output=response[:80_000]),ensure_ascii=False)

    def verify(self,request,result,settings):
        """A separate factual check before any destructive model-authored rewrite.

        Check against immutable original nodes, without the proposer's rationale.
        A syntactically valid proposal is not proof of a common delivery.
        """
        wire,aliases,_,_=reference_context(request)
        reverse={identity:alias for alias,identity in aliases.items()}
        proposed=[dict(index=i,members=g['members'],result=g['result'],completion=g['completion'],waived_actions=g.get('waived_actions',[]))
                  for i,g in enumerate(result['groups']) if g['disposition']=='apply']
        if not proposed:return result
        for group in proposed:
            group['members']=[reverse[i] for i in group['members']]
            group['waived_actions']=[reverse[i] for i in group['waived_actions']]
            # Do not mutate the canonical result while assigning aliases.
            group['completion']=deepcopy(group['completion'])
            for link in group['completion']:
                if 'source_id' in link:link['source_id']=reverse[link['source_id']]
                else:link['source_ids']=[reverse[i] for i in link['source_ids']]
                link['covers']=[reverse[i] for i in link['covers']]
        # Verify preservation for EACH original action, not only the group's
        # appealing new title. A three-part reminder must not become part three.
        by_id={n['id']:n for n in wire['nodes']}
        originals={reverse[n['id']]:n['text'] for n in request['nodes'] if n['kind']!='source'}
        checks=[]
        for group in proposed:
            for identity in group['members']:
                if by_id[identity]['kind']=='source':continue
                checks.append(dict(index=len(checks),group_index=group['index'],
                    action=dict(by_id[identity],original_text=originals[identity]),
                    waived=identity in group['waived_actions'],result=group['result'],
                    completion=[link for link in group['completion'] if identity in link['covers']]))
        proposed=checks
        schema=contract.obj(dict(checks=dict(type='array',items=contract.obj(dict(
            index=dict(enum=[g['index'] for g in proposed]),approved=dict(type='boolean'),
            reason=contract.string(800,1))),minItems=len(proposed),maxItems=len(proposed))))
        instructions=(SKILL.parent/'VERIFY.md').read_text(encoding='utf-8')+'\nJSON Schema:\n'+json.dumps(schema)
        payload=json.dumps(dict(nodes=[n for n in wire['nodes'] if n['kind']=='source'],proposals=proposed),ensure_ascii=False)
        raw=self.client.complete_json(instructions,payload,settings)
        try:
            checked=response_json(raw)
            from jsonschema import Draft202012Validator
            Draft202012Validator(schema).validate(checked)
            if {c['index'] for c in checked['checks']}!={g['index'] for g in proposed}:raise ValueError('coverage')
        except (ValueError,TypeError,KeyError,ValidationError):
            raise AgentError('invalid_agent_output',True,'relation_verification') from None
        for check in checked['checks']:
            if not check['approved']:
                group=result['groups'][proposed[check['index']]['group_index']]
                group.update(disposition='review',reason='关联未自动应用：'+check['reason'])
        return result


def relate(nodes, *, client, settings):
    request = contract.context(nodes)
    # One disposable process bounds model latency; no unbounded correction loop.
    result = run_attempt(RelationAdapter(client), request, settings, 0)
    if len(result) != 1:
        raise AgentError('invalid_agent_output', False)
    try:
        return contract.check_pair(request, result[0])
    except (ValueError, TypeError, KeyError, ValidationError):
        raise AgentError('invalid_agent_output', False) from None

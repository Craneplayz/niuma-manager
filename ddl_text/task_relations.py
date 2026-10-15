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
    schema['properties']['groups']['items']['properties']['evidence']['items']=contract.obj(
        dict(node_id=contract.ID,excerpt_id=contract.string(32,1)))
    if wire is not None:
        nodes=wire['nodes'];ids=[n['id'] for n in nodes]
        actions=[n['id'] for n in nodes if n['kind']!='source']
        receivers=[n['id'] for n in nodes if n.get('submission_state') in ('active','submitted')]
        group=schema['properties']['groups']['items']['properties']
        group['members']['items']=dict(enum=ids)
        group['evidence']['items']['properties']['node_id']=dict(enum=ids)
        if receivers:
            for alternative in group['completion']['items']['oneOf']:
                properties=alternative['properties']
                if 'source_id' in properties:properties['source_id']=dict(enum=receivers)
                else:properties['source_ids']['items']=dict(enum=receivers)
                properties['covers']['items']=dict(enum=actions)
        else:group['completion']['maxItems']=0
    return schema


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
        wire,catalog=evidence_context(request)
        wire,aliases=alias_context(wire)
        instructions = SKILL.read_text(encoding='utf-8') + '\nJSON Schema:\n' + json.dumps(model_schema(wire))
        payload=json.dumps(dict(context=wire, action_ids=[n['id'] for n in wire['nodes']
                           if n['kind'] != 'source']),ensure_ascii=False)
        repair=0
        while True:
            response = self.client.complete_json(instructions, payload, settings)
            try:
                if not isinstance(response, str) or len(response.encode('utf-8')) > contract.MAX_BYTES:
                    raise ValueError('invalid size')
                result = json.loads(response, object_pairs_hook=_object,
                                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                result = actionable_result(request, resolve_aliases(result,aliases))
                result = resolve_evidence(result,catalog)
                contract.check_pair(request, result)
                return [result]
            except (ValueError, TypeError, KeyError, ValidationError) as error:
                if repair:
                    raise AgentError('invalid_agent_output', False) from None
                repair+=1
                # One correction shares the same disposable-process deadline.
                # Never 'fix' semantic relations in Python or expose private errors.
                payload=json.dumps(dict(context=wire,validation_error=correction_reason(error),correction_request=
                    '按下面的上次输出修正并返回完整JSON。根字段只能是contract_version/context_digest/groups，不加notes等。每组必须包含task/candidate，只有source的关系省略。所有evidence.node_id必须属于本组members，每个行动和completion来源必须有evidence。仅选择已有excerpt_id；不要抄写摘要或编造编号。独立行动不要合并。',
                    previous_output=response[:80_000]),ensure_ascii=False)


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

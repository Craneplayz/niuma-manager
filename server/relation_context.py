"""Account-scoped retrieval for semantic relations, never a merge decision."""
import json
import math
import re
from collections import Counter
from contracts.task_relations import digest, context
import submission_runtime as controls


def source_id(account, input_id):
    return 'source:' + digest([account,input_id])


def task_hash(task):
    # UI serialization adds these defaults even when another task was edited.
    # Such a round trip is not an authored change or an automatic-completion pause.
    return digest(dict(task,completedAt=task.get('completedAt') or '',
                       planningExcluded=bool(task.get('planningExcluded'))))


def source_text(entry):
    record=entry['record']
    parts=[record.get('text',''),record.get('source_metadata_text','')]
    for ref in record.get('references',[]):
        parts.extend([ref.get('label',''),ref.get('text','')])
    return '\n'.join(dict.fromkeys(p for p in parts if isinstance(p,str) and p))


def inventory(db,uid):
    account=controls.current_account(db,uid)
    if not account:return {},{}
    nodes,bindings={},{}
    sources={}
    for row in db.execute('SELECT * FROM teaching_sources WHERE user_id=? AND account=?',(uid,account)):
        entry=json.loads(row['request'])['records'][0];record=entry['record']
        # Use enriched text only for this same registered snapshot.
        enriched=db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,row['request_id'])).fetchone()
        if enriched:entry=json.loads(enriched['request'])['records'][0]
        text=source_text(entry)
        attachments=[dict(a) for a in db.execute('SELECT ref_id,sha256,mime_type FROM teaching_asset_refs WHERE user_id=? AND account=? AND request_id=?',
                                                (uid,account,row['request_id']))]
        identity=source_id(account,row['input_id']);sources[row['input_id']]=identity
        native=controls.find(db,uid,account,row['input_id'])
        state=json.loads(native['detail'])['observation'] if native else None
        nodes[identity]=dict(id=identity,kind='source',text=text[:6000],truncated=len(text)>6000,
            source_digest=row['digest'],attachments=attachments,course=entry['source'].get('course_or_project'),
            published_at=record.get('published_at'),course_term=record.get('course_term'),
            submission_state=state['snapshot']['state'] if state else 'unknown',
            submission_basis=state['snapshot']['basis'] if state else 'unknown',
            control_revision=state['control_revision'] if state else None,
            source_paused=bool(state and (state['review_pending'] or any(a.get('source_completion_paused') for a in state['actions']))),
            source_snapshot=state['snapshot']['snapshot_id'] if state else None)
        bindings[identity]=dict(input_id=row['input_id'],request_id=row['request_id'],account=account,control=state)
    candidates={}
    for row in db.execute('''SELECT DISTINCT c.*,r.input_id FROM recognition_candidates c
        JOIN teaching_submission_requests r ON r.user_id=c.user_id AND r.request_id=c.request_id
        WHERE r.user_id=? AND r.account=?''',(uid,account)):
        if row['input_id'] in sources:candidates[row['id']]=dict(row)
    tasks,_=controls.workspace(db,uid)
    def original_text(linked):
        parts=[]
        for c in linked:
            batch=db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,c['request_id'])).fetchone()
            if batch:
                for entry in json.loads(batch['request'])['records']:
                    if isinstance(entry.get('record'),dict):parts.append(source_text(entry))
        return '\n'.join(dict.fromkeys(parts))[:6000]
    for task in tasks:
        history=task.get('recognitionHistory',[])
        linked=[candidates[h['storageId']] for h in history if isinstance(h,dict) and h.get('storageId') in candidates]
        if not linked:continue
        refs=sorted({sources[c['input_id']] for c in linked})
        from task_relations import source_evidence
        inherited=source_evidence(db,uid,task['id'],account)
        refs=sorted(set(refs)|{e['node_id'] for e in inherited if e['node_id'] in nodes})
        text='\n'.join(dict.fromkeys([e['quote'] for e in inherited]+[original_text(linked)]))
        identity='task:'+task['id']
        managed=db.execute('SELECT digest FROM task_relation_managed WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone()
        protected=not managed or managed['digest']!=task_hash(task) or task['status'] in ('completed','cancelled') or bool(task.get('recycleBin'))
        nodes[identity]=dict(id=identity,kind='task',text=text[:6000],truncated=len(text)>6000,
            attachments=[a for sid in refs for a in nodes[sid]['attachments']],
            summary=dict(title=task['title'],description=task.get('description','')[:800],due=task.get('due'),course=task.get('course')),
            sources=refs,protected=bool(protected),revision=task_hash(task),status=task['status'])
        bindings[identity]=dict(task=task,candidates=linked)
    for row in candidates.values():
        # An applied candidate whose task was deleted is not a new action.
        if row['state']!='pending':continue
        sid=sources[row['input_id']]
        if row['request_id']!=bindings[sid]['request_id']:continue
        card=json.loads(row['candidate']);identity='candidate:'+row['id']
        nodes[identity]=dict(id=identity,kind='candidate',text=original_text([row]),sources=[sid],
            attachments=nodes[sid]['attachments'],
            summary=dict(title=card['title'],description=card['description'][:800],deadline=card['deadline'],
                         course=card['course_or_project'],notice_type=card['notice_type'],review_reasons=card['review_reasons']),
            revision=digest(dict(row)),protected=row['version']!=1)
        bindings[identity]=dict(candidate=row)
    return nodes,bindings


def terms(node):
    text=json.dumps(node.get('summary',{}),ensure_ascii=False)+' '+node['text']+' '+json.dumps(node.get('attachments',[]))
    text=re.sub(r'\s+',' ',text.lower())
    # Character bigrams work with unsegmented scripts; identifiers and attachment
    # names remain signals. Scores only retrieve context and never authorize writes.
    words=re.findall(r'[a-z0-9][a-z0-9_.-]*',text)
    words.extend(text[i:i+2] for i in range(len(text)-1) if all('\u4e00'<=c<='\u9fff' for c in text[i:i+2]))
    return set(words)


def windows(nodes):
    """Bound model payloads; every action is an anchor, including low-similarity ones."""
    actions=[n for n in nodes.values() if n['kind']!='source' and n.get('status') not in ('completed','cancelled')]
    token={n['id']:terms(n) for n in nodes.values()}
    headings={n['id']:terms(dict(text=n.get('summary',{}).get('title') or n['text'].split('\n',1)[0])) for n in nodes.values()}
    hashes={n['id']:{a['sha256'] for a in n.get('attachments',[]) if a.get('sha256')} for n in nodes.values()}
    frequencies=Counter(t for ts in token.values() for t in ts)
    def score(a,b):
        shared=token[a]&token[b]
        body=sum(math.log(1+len(nodes)/frequencies[t]) for t in shared)/math.sqrt(max(1,len(token[a])*len(token[b])))
        title=len(headings[a]&headings[b])/math.sqrt(max(1,len(headings[a])*len(headings[b])))
        material=len(hashes[a]&hashes[b])/max(1,len(hashes[a]|hashes[b]))
        # Retrieval only: identical materials help bring content and receiving
        # pages together; the model still decides whether delivery scopes match.
        return body*.2+title+material*2
    # Database UUIDs change after a fresh import; they must not change which
    # source family is analyzed first or which equally ranked context is seen.
    stable=lambda n:(tuple(sorted(n.get('sources',[]))),n['kind'],n.get('summary',{}).get('title',''),n['id'])
    used=set()
    for action in sorted(actions,key=stable):
        if action['id'] in used:continue
        # Retrieve the other components of an explicit receiving endpoint first.
        # This only widens model context; shared files never authorize a merge.
        endpoints=[n for n in nodes.values() if n['kind']=='source'
                   and n.get('submission_state') in ('active','submitted')
                   and hashes[action['id']] & hashes[n['id']]]
        endpoint=max(endpoints,key=lambda n:score(action['id'],n['id']),default=None)
        companion=lambda n:bool(endpoint and (hashes[n['id']] & hashes[endpoint['id']] or endpoint['id'] in n['sources']))
        ranked=sorted(actions,key=lambda n:(n['id']!=action['id'],not companion(n),-score(action['id'],n['id']),stable(n)))
        best=max((score(action['id'],n['id']) for n in ranked if n['id']!=action['id']),default=0)
        selected={};chosen=[]
        for other in ranked:
            if other['id']!=action['id'] and not companion(other) and score(action['id'],other['id'])<max(.25,best*.55):continue
            related=sorted(other['sources'],key=lambda sid:(-score(other['id'],sid),sid))
            group=[other['id']]+related[:8]
            if len(set(selected)|set(group))>18:continue
            chosen.append(other['id'])
            for key in group:selected[key]=nodes[key]
            if len(chosen)==8:break
        if endpoint and endpoint['id'] not in selected and len(selected)<18:selected[endpoint['id']]=endpoint
        relevance=lambda source:max(score(a,source) for a in chosen)
        best_source={a:max((score(a,n['id']) for n in nodes.values() if n['kind']=='source'),default=0) for a in chosen}
        sources=sorted((n for n in nodes.values() if n['kind']=='source'),
                       key=lambda n:(-relevance(n['id']),n['id']))
        for source in sources:
            if source['id'] in selected:continue
            if len(selected)>=12:break
            if any(score(a,source['id'])>=max(.2,best_source[a]*.5) for a in chosen):selected[source['id']]=source
        used.update(chosen)
        if len(selected)>1:
            # UTF-8 budget also applies for non-ASCII texts.
            yield bounded_context(list(selected.values()))


def bounded_context(nodes):
    try:return context(nodes)
    except ValueError:
        return context([dict(n,text=n['text'][:2000],truncated=True) if len(n['text'])>2000 else n for n in nodes])


def refresh_context(db,uid,request):
    nodes,_=inventory(db,uid)
    if any(n['id'] not in nodes for n in request['nodes']):return None
    current=[nodes[n['id']] for n in request['nodes']]
    # Repeat the same deliberate clipping; never validate against stale text.
    for index,old in enumerate(request['nodes']):
        if old.get('truncated') and len(old['text'])==2000:
            current[index]=dict(current[index],text=current[index]['text'][:2000],truncated=True)
    return context(current)


def submitted_windows(nodes):
    """Small source-local checks for pending actions omitted by broad retrieval."""
    for source in sorted(nodes.values(),key=lambda n:n['id']):
        if source['kind']!='source' or source.get('submission_state')!='submitted' or source.get('source_paused'):continue
        actions=[n for n in nodes.values() if n['kind']=='candidate' and not n.get('protected')
                 and n.get('sources')==[source['id']]]
        for start in range(0,len(actions),8):
            yield bounded_context(actions[start:start+8]+[source])

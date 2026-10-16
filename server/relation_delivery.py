"""Reuse a server-managed delivery only after the model proved full coverage.

Identity comes from the exact receiving endpoint(s) and content snapshots, never
course names, titles or chapter numbers. User decisions remain protected.
"""
import json
import relation_context as context
import submission_runtime as controls
from contracts import task_relations as contract


def requirements(db,uid,account,group,bindings,actions):
    conditions=[]
    for coverage in group['completion']:
        if set(coverage['covers'])!=set(actions):continue
        condition=[]
        for identity in contract.completion_sources_for(coverage):
            source=bindings[identity]
            row=controls.find(db,uid,account,source['input_id'])
            if not row:break
            condition.append(dict(input_id=source['input_id'],content_digest=row['content_digest']))
        if len(condition)==len(contract.completion_sources_for(coverage)):
            conditions.append(sorted(condition,key=lambda r:r['input_id']))
    # Multiple alternative completion conditions do not define a single identity.
    return conditions[0] if conditions and all(c==conditions[0] for c in conditions) else None


def existing(db,uid,account,condition,tasks):
    if not condition:return []
    found=[]
    for task in tasks:
        managed=db.execute('SELECT digest FROM task_relation_managed WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone()
        links=list(db.execute('SELECT * FROM task_relation_links WHERE user_id=? AND account=? AND task_id=?',(uid,account,task['id'])))
        sets=list(db.execute('SELECT * FROM task_relation_completion_sets WHERE user_id=? AND account=? AND task_id=?',(uid,account,task['id'])))
        if len(links)==1 and not sets:
            owned=[dict(input_id=links[0]['input_id'],content_digest=links[0]['content_digest'])]
        elif len(sets)==1 and not links:
            owned=sorted(json.loads(sets[0]['requirements']),key=lambda r:r['input_id'])
        else:continue
        if owned!=condition:continue
        if (task.get('recycleBin') or task['status']=='cancelled' or not managed
                or managed['digest']!=context.task_hash(task) or any(r['paused'] for r in links+sets)):
            return None
        for item in condition:
            row=controls.find(db,uid,account,item['input_id'])
            obs=json.loads(row['detail'])['observation']
            if obs['review_pending'] or any(a.get('source_completion_paused') for a in obs['actions']):return None
        found.append(task)
    return found

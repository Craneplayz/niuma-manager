"""Durable semantic integration, conservative writes and reversible account-local history."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import threading

from contracts import task_relations as contract
from ddl_text.task_relations import relate, SKILL
from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings
from ddl_text.codex_client import live_options
from runtime_environment import model_env_path
from server.model_queue import ACTIVE, cancel_if
import relation_context as context
import submission_runtime as controls

_lock=threading.Lock()
_workers={}


def stamp():return datetime.now(timezone.utc).isoformat()


def initialize(db,recover=False):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS task_relation_managed(user_id TEXT,task_id TEXT,digest TEXT,
        PRIMARY KEY(user_id,task_id));
      CREATE TABLE IF NOT EXISTS task_relation_jobs(id TEXT PRIMARY KEY,user_id TEXT,account TEXT,
        credential TEXT,origin TEXT,state TEXT,error TEXT,created_at TEXT);
      CREATE UNIQUE INDEX IF NOT EXISTS task_relation_active ON task_relation_jobs(user_id)
        WHERE state='running';
      CREATE TABLE IF NOT EXISTS task_relation_cache(user_id TEXT,account TEXT,digest TEXT,
        PRIMARY KEY(user_id,account,digest));
      CREATE TABLE IF NOT EXISTS task_relation_proposals(id TEXT PRIMARY KEY,user_id TEXT,account TEXT,
        job_id TEXT,state TEXT,request TEXT,result TEXT,before_state TEXT,after_state TEXT,created_at TEXT);
      CREATE TABLE IF NOT EXISTS task_relation_links(user_id TEXT,account TEXT,input_id TEXT,
        content_digest TEXT,task_id TEXT,task_digest TEXT,paused INTEGER DEFAULT 0,
        PRIMARY KEY(user_id,account,input_id,task_id));
      CREATE TABLE IF NOT EXISTS task_relation_completion_sets(user_id TEXT,account TEXT,set_id TEXT,
        task_id TEXT,requirements TEXT,task_digest TEXT,paused INTEGER DEFAULT 0,
        PRIMARY KEY(user_id,account,set_id,task_id));
      CREATE TABLE IF NOT EXISTS task_relation_windows(job_id TEXT,window_index INTEGER,
        state TEXT,error_code TEXT,PRIMARY KEY(job_id,window_index));
      CREATE TABLE IF NOT EXISTS task_relation_diagnostics(job_id TEXT,window_index INTEGER,
        diagnostic TEXT,PRIMARY KEY(job_id,window_index));
    ''')
    if recover:db.execute("UPDATE task_relation_jobs SET state='interrupted',error='服务重启，已保存整合结果保留；可继续分析。' WHERE state='running'")


def remember(db,uid,task):
    db.execute('INSERT OR REPLACE INTO task_relation_managed VALUES(?,?,?)',(uid,task['id'],context.task_hash(task)))


def manual_write(db,uid,tasks):
    old={t['id']:t for t in controls.workspace(db,uid)[0]}
    for task in tasks:
        if task['id'] not in old or context.task_hash(old[task['id']])!=context.task_hash(task):
            db.execute('DELETE FROM task_relation_managed WHERE user_id=? AND task_id=?',(uid,task['id']))
            db.execute('UPDATE task_relation_links SET paused=1 WHERE user_id=? AND task_id=?',(uid,task['id']))
            db.execute('UPDATE task_relation_completion_sets SET paused=1 WHERE user_id=? AND task_id=?',(uid,task['id']))


def source_evidence(db,uid,task_id,account=None):
    """Follow server-owned merge ancestry, including receiving pages with no task."""
    ancestors={task_id};evidence=[];seen=set()
    sql="SELECT * FROM task_relation_proposals WHERE user_id=? AND state='applied'"
    args=[uid]
    if account is not None:sql+=' AND account=?';args.append(account)
    for row in db.execute(sql+' ORDER BY rowid DESC',args):
        after=json.loads(row['after_state'])
        if not ancestors.intersection(t['id'] for t in after['tasks']):continue
        ancestors.update(t['id'] for t in json.loads(row['before_state'])['tasks'])
        nodes={n['id']:n for n in json.loads(row['request'])['nodes']}
        group=json.loads(row['result'])
        for quote in group['evidence']:
            node=nodes[quote['node_id']]
            key=(node['id'],quote['quote'])
            if node['kind']!='source' or key in seen:continue
            seen.add(key)
            evidence.append(dict(node_id=node['id'],quote=quote['quote'],course=node.get('course'),reason=group['reason']))
    return evidence


def evidence_entries(db,uid,task_id):
    return [dict(action='update',quotes=[dict(input_id=e['node_id'],field='text',quote=e['quote'])],
                 basis=e['reason'],sources=[dict(input_id=e['node_id'],label=e['course'] or '关联接收入口',platform='teaching_web')],
                 original='',sourceLabel=e['course'],platform='teaching_web') for e in source_evidence(db,uid,task_id)]


def write_tasks(db,uid,tasks,version):
    import usage
    old=controls.workspace(db,uid)[0]
    db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',
               (uid,'tasks',json.dumps(tasks),version+1))
    usage.workspace_changes(db,uid,old,tasks)


def completion_ready(db,uid,account,input_id):
    row=controls.find(db,uid,account,input_id)
    if not row:return False
    obs=json.loads(row['detail'])['observation']
    return (obs['snapshot']['source_account_id']==account and obs['snapshot']['state']=='submitted'
            and obs['snapshot']['basis']=='review_submission_history_title' and not obs['review_pending'])


def complete(task):
    now=datetime.now(timezone.utc)
    task.update(status='completed',completedAt=now.date().isoformat())
    # Preserve completed plans; future reservations should no longer occupy time.
    def past(plan):
        try:return datetime.fromisoformat(plan['startAt'].replace('Z','+00:00'))<=now
        except (ValueError,KeyError,TypeError):return True
    task['plans']=[p for p in task.get('plans',[]) if past(p)]


def refresh_completions(db,uid):
    account=controls.current_account(db,uid)
    if not account:return
    tasks,version=controls.workspace(db,uid);changed=False
    for link in db.execute('SELECT * FROM task_relation_links WHERE user_id=? AND account=? AND paused=0',(uid,account)).fetchall():
        task=next((t for t in tasks if t['id']==link['task_id']),None)
        row=controls.find(db,uid,account,link['input_id'])
        if not task or task['status'] in ('completed','cancelled') or task.get('recycleBin'):continue
        if context.task_hash(task)!=link['task_digest'] or not row or row['content_digest']!=link['content_digest']:continue
        if not requirements_ready(db,uid,account,[dict(link)]):continue
        complete(task);remember(db,uid,task);changed=True
    for link in db.execute('SELECT * FROM task_relation_completion_sets WHERE user_id=? AND account=? AND paused=0',(uid,account)).fetchall():
        task=next((t for t in tasks if t['id']==link['task_id']),None)
        if not task or task['status'] in ('completed','cancelled') or task.get('recycleBin'):continue
        if context.task_hash(task)!=link['task_digest']:continue
        if not requirements_ready(db,uid,account,json.loads(link['requirements'])):continue
        complete(task);remember(db,uid,task);changed=True
    if changed:write_tasks(db,uid,tasks,version)


def requirements_ready(db,uid,account,requirements):
    if not requirements:return False
    for item in requirements:
        row=controls.find(db,uid,account,item['input_id'])
        if not row or row['content_digest']!=item['content_digest']:return False
        observation=json.loads(row['detail'])['observation']
        if any(a.get('source_completion_paused') for a in observation['actions']):return False
        if not completion_ready(db,uid,account,item['input_id']):return False
    return True


def new_task(result):
    task=dict(id=secrets.token_hex(16),title=result['title'],description=result['description'],
        course=result['course'],category='课程作业' if result['course'] else '个人事务',priority='medium',
        status='not_started',source='教学网',subtasks=[],plans=[],createdAt=stamp(),recognitionHistory=[])
    update_fields(task,result)
    return task


def update_fields(task,result):
    due=result['due']
    was_notice=task.get('content_type')=='notice'
    task.update(title=result['title'],description=result['description'],course=result['course'],
        content_type=result['content_type'],due=None if due is None else
        dict(date=due['date'],precision='datetime' if due['time'] is not None else 'date',**({'time':due['time']} if due['time'] is not None else {})))
    if result['content_type']=='notice':task['planningExcluded']=True
    elif was_notice:task.pop('planningExcluded',None)


def current_group(db,uid,request,group):
    # Check only this group's nodes. An unrelated workspace edit need not discard it.
    nodes,bindings=context.inventory(db,uid)
    wanted=set(group['members'])
    original={n['id']:n for n in request['nodes']}
    for identity in wanted:
        if identity not in nodes:return None
        fresh=deepcopy(nodes[identity]);old=original[identity]
        if old.get('truncated') and len(old['text'])==2000:fresh.update(text=fresh['text'][:2000],truncated=True)
        if fresh!=old:return None
    return nodes,bindings


def apply_group(db,uid,pid,request,group,ApiError,manual=False):
    account=controls.current_account(db,uid)
    checked=current_group(db,uid,request,group)
    if not checked:raise ApiError(409,'关联依据或任务已变化，请重新分析')
    nodes,bindings=checked
    actions=[nodes[i] for i in group['members'] if nodes[i]['kind']!='source']
    # Known deadlines may be replaced by a verified update, never silently
    # cleared by a merge. Keep the originals and proposal available for review.
    if not manual and group['result']['due'] is None and any(a.get('summary',{}).get('due') for a in actions):
        return False
    if any(a.get('protected') for a in actions) and not manual:return False
    if group['disposition']=='review' and not manual:return False
    # Automatic grouping cannot erase an existing separate plan, subtasks or a
    # user decision. Manual acceptance also never resurrects deleted tasks.
    for action in actions:
        if action['kind']=='task':
            t=bindings[action['id']]['task']
            if t.get('recycleBin') or t['status']=='cancelled':raise ApiError(409,'关联包含已删除或取消任务，请先处理该任务')
            if (t.get('plans') or t.get('subtasks')) and not manual:return False
    tasks,version=controls.workspace(db,uid)
    old_tasks=[deepcopy(bindings[a['id']]['task']) for a in actions if a['kind']=='task']
    affected={t['id'] for t in old_tasks}
    focus=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="focus"',(uid,)).fetchone()
    if focus and json.loads(focus['value']).get('taskId') in affected:
        if not manual:return False
        raise ApiError(409,'请先结束并移除该任务的专注计时，再确认整合')
    feedback=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="feedback"',(uid,)).fetchone()
    if feedback and any(f.get('taskId') in affected for f in json.loads(feedback['value'])):
        if not manual:return False
        raise ApiError(409,'任务已有完成用时记录，请保留原任务并手动调整，避免丢失统计关联')
    old_links=[dict(r) for r in db.execute('SELECT * FROM task_relation_links WHERE user_id=?',(uid,)) if r['task_id'] in affected]
    old_sets=[dict(r) for r in db.execute('SELECT * FROM task_relation_completion_sets WHERE user_id=?',(uid,)) if r['task_id'] in affected]
    candidate_rows=[bindings[a['id']]['candidate'] for a in actions if a['kind']=='candidate']
    survivor=next((t for t in tasks if t['id'] in {a['id'][5:] for a in actions if a['kind']=='task'}),None)
    if not survivor:survivor=new_task(group['result']);tasks.append(survivor)
    update_fields(survivor,group['result'])
    history=survivor.setdefault('recognitionHistory',[])
    merged_ids={t['id'] for t in old_tasks if t['id']!=survivor['id']}
    for task in old_tasks:
        for h in task.get('recognitionHistory',[]):
            if h not in history:history.append(h)
        if task['id'] in merged_ids:
            # Keep all authored work on the survivor, not just model fields.
            for key in ('subtasks','plans'):
                for item in task.get(key,[]):
                    if item not in survivor[key]:survivor[key].append(item)
    for row in candidate_rows:
        card=json.loads(row['candidate'])
        h=dict(storageId=row['id'],requestId=row['request_id'],candidateId=card['candidate_id'],action='update')
        if h not in history:history.append(h)
        db.execute("UPDATE recognition_candidates SET state='applied',version=version+1,resolution=? WHERE id=? AND user_id=?",
                   (json.dumps(dict(action='task_relation',proposal_id=pid,target_id=survivor['id'])),row['id'],uid))
    tasks=[t for t in tasks if t['id'] not in merged_ids]
    action_ids={a['id'] for a in actions}
    new_links=[];new_sets=[]
    # Each stored link covers the WHOLE survivor, never just one component.
    # Multiple partial submission endpoints cannot be unioned into false completion.
    for coverage in group['completion']:
        if set(coverage['covers'])!=action_ids:continue
        requirements=[]
        for identity in contract.completion_sources_for(coverage):
            source=bindings[identity]
            native=controls.find(db,uid,account,source['input_id'])
            if not native:break
            obs=json.loads(native['detail'])['observation']
            paused=obs['review_pending'] or any(a.get('source_completion_paused') for a in obs['actions'])
            if paused:break
            requirements.append(dict(input_id=source['input_id'],content_digest=native['content_digest']))
        if len(requirements)!=len(contract.completion_sources_for(coverage)):continue
        if len(requirements)==1:new_links.extend(requirements)
        else:new_sets.append(requirements)
        if requirements_ready(db,uid,account,requirements):complete(survivor)
    before=dict(tasks=old_tasks,candidates=candidate_rows,links=old_links,completion_sets=old_sets)
    after_candidates=[dict(db.execute('SELECT * FROM recognition_candidates WHERE id=? AND user_id=?',(r['id'],uid)).fetchone()) for r in candidate_rows]
    after=dict(tasks=[deepcopy(survivor)],candidates=after_candidates)
    write_tasks(db,uid,tasks,version);remember(db,uid,survivor)
    for identity in affected:
        db.execute('DELETE FROM task_relation_links WHERE user_id=? AND task_id=?',(uid,identity))
        db.execute('DELETE FROM task_relation_completion_sets WHERE user_id=? AND task_id=?',(uid,identity))
    for link in new_links:
        db.execute('INSERT OR REPLACE INTO task_relation_links VALUES(?,?,?,?,?,?,0)',
            (uid,account,link['input_id'],link['content_digest'],survivor['id'],context.task_hash(survivor)))
    for requirements in new_sets:
        db.execute('INSERT OR REPLACE INTO task_relation_completion_sets VALUES(?,?,?,?,?,?,0)',
            (uid,account,contract.digest(sorted(r['input_id'] for r in requirements)),survivor['id'],
             json.dumps(requirements),context.task_hash(survivor)))
    db.execute("UPDATE task_relation_proposals SET state='applied',before_state=?,after_state=? WHERE id=? AND user_id=?",
               (json.dumps(before),json.dumps(after),pid,uid))
    return True


def undo(db,uid,row,ApiError):
    if row['state']=='undone':return
    if row['state']!='applied':raise ApiError(409,'仅已应用的整合可以撤回')
    before=json.loads(row['before_state']);after=json.loads(row['after_state'])
    tasks,version=controls.workspace(db,uid);by_id={t['id']:t for t in tasks}
    for task in after['tasks']:
        if task['id'] not in by_id or context.task_hash(by_id[task['id']])!=context.task_hash(task):raise ApiError(409,'整合后任务已被修改，不能覆盖；请在任务中调整')
    removed={t['id'] for t in before['tasks']}-{t['id'] for t in after['tasks']}
    if removed & by_id.keys():raise ApiError(409,'原任务编号已重新使用，不能撤回')
    for candidate in after['candidates']:
        current=db.execute('SELECT * FROM recognition_candidates WHERE id=? AND user_id=?',(candidate['id'],uid)).fetchone()
        # Rows in inventory contain input_id from a join, persisted rows do not.
        if not current or dict(current)!={k:v for k,v in candidate.items() if k!='input_id'}:raise ApiError(409,'候选已变化，不能覆盖')
    tasks=[t for t in tasks if t['id'] not in {t['id'] for t in after['tasks']}]+before['tasks']
    for candidate in before['candidates']:
        db.execute('UPDATE recognition_candidates SET state=?,version=version+1,resolution=? WHERE id=? AND user_id=?',
            (candidate['state'],candidate['resolution'],candidate['id'],uid))
    for task in after['tasks']:
        db.execute('DELETE FROM task_relation_links WHERE user_id=? AND task_id=?',(uid,task['id']))
        db.execute('DELETE FROM task_relation_completion_sets WHERE user_id=? AND task_id=?',(uid,task['id']))
        db.execute('DELETE FROM task_relation_managed WHERE user_id=? AND task_id=?',(uid,task['id']))
    for link in before.get('links',[]):
        db.execute('INSERT OR REPLACE INTO task_relation_links VALUES(?,?,?,?,?,?,1)',
                   tuple(link[k] for k in ('user_id','account','input_id','content_digest','task_id','task_digest')))
    for link in before.get('completion_sets',[]):
        db.execute('INSERT OR REPLACE INTO task_relation_completion_sets VALUES(?,?,?,?,?,?,1)',
                   tuple(link[k] for k in ('user_id','account','set_id','task_id','requirements','task_digest')))
    # Restored tasks are deliberately user-owned: do not auto-merge again.
    write_tasks(db,uid,tasks,version)
    db.execute("UPDATE task_relation_proposals SET state='undone' WHERE id=?",(row['id'],))


def model(request):
    options=live_options(model_env_path(Path(__file__).resolve().parent.parent))
    if options['provider']!='qiniu' or not options['key'].strip():raise ValueError('model_configuration')
    settings=Settings(mode='real',model=options['model'],endpoint=options['endpoint'],timeout_seconds=120,max_retries=0,
                      thinking_mode='disabled' if options['model'].startswith('deepseek/') else None)
    if not ACTIVE.acquire():raise TimeoutError('model queue')
    try:return relate(request['nodes'],client=ChatClient(options['key']),settings=settings)
    finally:ACTIVE.release()


def fingerprint(request):
    try:
        options=live_options(model_env_path(Path(__file__).resolve().parent.parent))
        model_identity=[options['provider'],options['model'],options['endpoint']]
    except (OSError,ValueError,KeyError):model_identity=['unconfigured']
    stable=deepcopy(request);stable.pop('context_digest',None)
    for node in stable['nodes']:
        node.pop('source_snapshot',None)
        node.pop('control_revision',None)
    return contract.digest([stable,SKILL.read_text(encoding='utf-8').replace('\r\n','\n'),
        (SKILL.parent/'VERIFY.md').read_text(encoding='utf-8').replace('\r\n','\n'),contract.OUTPUT,model_identity,'global-references-verified-v2'])


def dismissed(db,uid,account,request,group):
    nodes={n['id']:n for n in request['nodes']}
    actions={i for i in group['members'] if nodes[i]['kind']!='source'}
    for row in db.execute("SELECT request,result FROM task_relation_proposals WHERE user_id=? AND account=? AND state IN ('ignored','undone')",(uid,account)):
        old=json.loads(row['result']);old_nodes={n['id']:n for n in json.loads(row['request'])['nodes']}
        if actions=={i for i in old['members'] if old_nodes[i]['kind']!='source'}:return True
    return False


def start(db,uid,origin):
    import source_jobs
    account=controls.current_account(db,uid)
    if not account:return None
    credential=source_jobs.credential(db,uid)
    db.execute("UPDATE task_relation_jobs SET state='interrupted',error='账号凭据变化，旧关联已停止。' WHERE user_id=? AND state='running' AND (account<>? OR credential<>?)",(uid,account,credential))
    prior=db.execute("SELECT id,origin FROM task_relation_jobs WHERE user_id=? AND state='running'",(uid,)).fetchone()
    if prior:
        if not origin.startswith('sync:') or prior['origin']==origin:return prior['id']
        # A later sync has new sources: its completion must not point to a job
        # that captured an older inventory. In-flight writes check this guard.
        db.execute("UPDATE task_relation_jobs SET state='interrupted',error='新的同步已完成，关联分析由新一轮接续。' WHERE id=?",(prior['id'],))
    job=secrets.token_hex(16)
    full=db.execute("SELECT count(*) FROM task_relation_jobs WHERE state='running'").fetchone()[0]>=4
    db.execute('INSERT INTO task_relation_jobs VALUES(?,?,?,?,?,?,?,?)',
        (job,uid,account,credential,origin,'failed' if full else 'running','关联队列已满，请稍后重新分析。' if full else None,stamp()))
    return job


def guard(db,uid,job):
    import source_jobs
    row=db.execute('SELECT * FROM task_relation_jobs WHERE id=? AND user_id=?',(job,uid)).fetchone()
    return row and row['state']=='running' and row['account']==controls.current_account(db,uid) and row['credential']==source_jobs.credential(db,uid)


def process_window(path,uid,job,request,ApiError):
    import source_jobs
    with source_jobs.connection(path) as db:
        if not guard(db,uid,job):return
        nodes,_=context.inventory(db,uid)
        # Do not feed a previous window's rewritten action into another window
        # as fresh evidence. All windows belong to one immutable action snapshot.
        remaining=[nodes[n['id']] for n in request['nodes'] if n['id'] in nodes
                   and (n['kind']=='source' or n.get('revision')==nodes[n['id']].get('revision'))
                   and nodes[n['id']].get('status') not in ('completed','cancelled')]
        clipped={n['id'] for n in request['nodes'] if n.get('truncated') and len(n['text'])==2000}
        remaining=[dict(n,text=n['text'][:2000],truncated=True) if n['id'] in clipped else n for n in remaining]
        if not any(n['kind']!='source' for n in remaining):return
        request=contract.context(remaining);key=fingerprint(request);account=controls.current_account(db,uid)
        if db.execute('SELECT 1 FROM task_relation_cache WHERE user_id=? AND account=? AND digest=?',(uid,account,key)).fetchone():return
    def cancelled():
        with source_jobs.connection(path) as db:return not guard(db,uid,job)
    with cancel_if(cancelled):result=model(request)
    contract.check_pair(request,result)
    with source_jobs.connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not guard(db,uid,job):return
        if context.refresh_context(db,uid,request)!=request:
            raise ApiError(409,'关联分析期间来源或任务发生变化，请重新分析')
        for group in result['groups']:
            if dismissed(db,uid,account,request,group):continue
            pid=secrets.token_hex(16)
            db.execute('INSERT INTO task_relation_proposals VALUES(?,?,?,?,?,?,?,?,?,?)',
                (pid,uid,account,job,'review',json.dumps(request),json.dumps(group),None,None,stamp()))
            apply_group(db,uid,pid,request,group,ApiError)
        db.execute('INSERT OR IGNORE INTO task_relation_cache VALUES(?,?,?)',(uid,account,key))


def failure_code(error):
    from ddl_text.adapters import AgentError
    if isinstance(error,AgentError):
        return error.code if error.code in ('invalid_agent_output','agent_timeout','agent_unavailable') else 'model_failure'
    if isinstance(error,TimeoutError):return 'timeout'
    if getattr(error,'status',None)==409:return 'context_changed'
    return 'validation_or_write_failure'


def process(path,uid,job,ApiError):
    import source_jobs
    failed=0
    try:
        with source_jobs.connection(path) as db:
            if not guard(db,uid,job):return
            refresh_completions(db,uid)
            requests=list(context.windows(context.inventory(db,uid)[0]))
        for index,request in enumerate(requests):
            with source_jobs.connection(path) as db:
                if not guard(db,uid,job):return
            code=None;diagnostic=None
            try:process_window(path,uid,job,request,ApiError)
            except Exception as error:
                failed+=1;code=failure_code(error)
                from ddl_text.adapters import AgentError
                diagnostic=AgentError(diagnostic=getattr(error,'diagnostic',None)).diagnostic
            with source_jobs.connection(path) as db:
                if not guard(db,uid,job):return
                db.execute('INSERT OR REPLACE INTO task_relation_windows VALUES(?,?,?,?)',
                           (job,index,'failed' if code else 'succeeded',code))
                db.execute('INSERT OR REPLACE INTO task_relation_diagnostics VALUES(?,?,?)',(job,index,diagnostic))
        with source_jobs.connection(path) as db:
            if guard(db,uid,job):
                error=f'关联分析有 {failed}/{len(requests)} 组上下文未完成；已成功整合的结果保留，可重新分析失败部分。' if failed else None
                db.execute('UPDATE task_relation_jobs SET state=?,error=? WHERE id=?',('failed' if failed else 'succeeded',error,job))
    except Exception:
        with source_jobs.connection(path) as db:
            db.execute("UPDATE task_relation_jobs SET state='failed',error='关联分析未完成，已识别的任务和已保存整合均保留；可以重新分析。' WHERE id=? AND state='running'",(job,))
    finally:
        with source_jobs.connection(path) as db:
            db.execute("UPDATE task_relation_jobs SET state='interrupted',error='来源或账号已变化，请重新分析。' WHERE id=? AND state='running'",(job,))


def launch(path,uid,job,ApiError):
    if not job:return
    key=(str(path),job)
    def work():
        try:process(path,uid,job,ApiError)
        finally:
            with _lock:_workers.pop(key,None)
    with _lock:
        if key in _workers:return
        worker=threading.Thread(target=work,daemon=True,name='task-relations')
        _workers[key]=worker;worker.start()


def wait_for_jobs(path,timeout=30):
    with _lock:workers=[w for (p,_),w in _workers.items() if p==str(path)]
    for worker in workers:worker.join(timeout)
    if any(w.is_alive() for w in workers):raise TimeoutError('relation jobs')


def route(handler,db,uid,path,body,ApiError,database):
    root='/api/recognition/relations'
    if path!=root and not path.startswith(root+'/'):return False
    if handler.headers.get('Authorization'):raise ApiError(403,'关联操作需要登录会话')
    account=controls.current_account(db,uid)
    if not account:raise ApiError(404,'请先连接教学网')
    if path==root and handler.command=='GET':
        jobs=[{k:r[k] for k in ('id','origin','state','error','created_at')} for r in db.execute(
            'SELECT * FROM task_relation_jobs WHERE user_id=? AND account=? ORDER BY rowid DESC LIMIT 10',(uid,account))]
        proposals=[dict(id=r['id'],state=r['state'],result=json.loads(r['result']),
                       before=[n['summary'] for n in json.loads(r['request'])['nodes']
                               if n['id'] in json.loads(r['result'])['members'] and n.get('summary')]) for r in db.execute(
            'SELECT * FROM task_relation_proposals WHERE user_id=? AND account=? ORDER BY rowid DESC LIMIT 100',(uid,account))]
        handler.reply(200,contract.check_list(dict(contract_version=contract.VERSION,jobs=jobs,proposals=proposals)));return True
    if path==root and handler.command=='POST':
        if body!=dict(consent=True):raise ApiError(400,'请同意使用当前账号已保存的来源进行关联分析')
        db.execute('BEGIN IMMEDIATE');job=start(db,uid,'manual');db.commit();launch(database,uid,job,ApiError)
        handler.reply(202,dict(id=job));return True
    suffix=path[len(root)+1:].split('/')
    if len(suffix)==2 and suffix[1]=='stop' and handler.command=='POST':
        db.execute("UPDATE task_relation_jobs SET state='stopped' WHERE id=? AND user_id=? AND account=? AND state='running'",(suffix[0],uid,account))
        handler.reply(200,dict(stopped=True));return True
    if len(suffix)==2 and suffix[1] in ('apply','ignore','undo') and handler.command=='POST':
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM task_relation_proposals WHERE id=? AND user_id=? AND account=?',(suffix[0],uid,account)).fetchone()
        if not row:raise ApiError(404,'关联记录不存在')
        if suffix[1]=='undo':undo(db,uid,row,ApiError)
        elif row['state']=='review':
            if suffix[1]=='ignore':db.execute("UPDATE task_relation_proposals SET state='ignored' WHERE id=?",(row['id'],))
            else:
                if body!=dict(reviewed=True):raise ApiError(400,'请先核对关联依据')
                apply_group(db,uid,row['id'],json.loads(row['request']),json.loads(row['result']),ApiError,True)
        elif row['state']!=('ignored' if suffix[1]=='ignore' else 'applied'):raise ApiError(409,'关联记录已经处理')
        handler.reply(200,dict(ok=True));return True
    raise ApiError(404,'关联操作不存在')

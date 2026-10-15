"""Authenticated recognition transport and transactional candidate resolution."""
import hashlib
import json
import os
if __package__:
    from . import usage
    from .runtime_environment import model_env_path
else:
    import usage
    from runtime_environment import model_env_path
import secrets
import re
from datetime import datetime, timezone, timedelta, date
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from text_contract import validate_request, validate_result

# dev:api launches server/app.py from frontend/, not from the repository root.
# The same absolute import path is inherited by Windows spawn workers.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.model_queue import ACTIVE as _ACTIVE, WAIT_ERROR
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.extraction import ExtractionAdapter
from ddl_text.pipeline import process_request
from ddl_text.provider import make_client
from ddl_text.policy_version import text_policy_manifest, COMPONENTS, ROOT as POLICY_ROOT

_EXECUTION = threading.local()
_POLICY_COMPONENTS = {name:hashlib.sha256((POLICY_ROOT/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').replace('\r','\n').encode()).hexdigest()
                      for name in COMPONENTS}


def recognize(request, ApiError):
    """Queue model admission across accounts, features and transports."""
    _EXECUTION.policy = None
    if not _ACTIVE.acquire():
        raise ApiError(503, WAIT_ERROR)
    try:
        endpoint = os.environ.get('TEXT_HARNESS_URL')
        if endpoint:
            headers = {'Content-Type': 'application/json'}
            if os.environ.get('TEXT_HARNESS_TOKEN'):
                headers['Authorization'] = 'Bearer ' + os.environ['TEXT_HARNESS_TOKEN']
            try:
                with urlopen(Request(endpoint, json.dumps(request).encode(), headers), timeout=95) as response:
                    return json.loads(response.read(2000000))
            except HTTPError as error:
                busy = error.code == 503
                error.close()
                if busy:
                    raise ApiError(503, '文本识别正在处理其他请求，请稍后重试') from None
                raise ApiError(502, '文本识别服务不可用或超时，请稍后重试') from None
            except Exception:
                raise ApiError(502, '文本识别服务不可用或超时，请稍后重试') from None
        try:
            options = live_options(model_env_path(ROOT))
            if not options['key'].strip():
                raise ValueError('missing credential')
            adapter = ExtractionAdapter(make_client(options))
            settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'],
                                timeout_seconds=90, max_retries=0)
        except (OSError, ValueError, TypeError, KeyError):
            raise ApiError(503, '文本识别配置不可用，请检查当前环境的模型配置文件或环境变量中的模型配置与密钥') from None
        try:
            policy = text_policy_manifest(settings,provider='qiniu' if options['provider']=='qiniu' else 'codex')
            if policy['components'] != _POLICY_COMPONENTS:
                raise ValueError('policy_changed_after_startup')
        except (OSError,ValueError,KeyError,TypeError):
            raise ApiError(503,'识别规则与当前进程不一致，请重启后端后重试') from None
        _EXECUTION.policy = policy
        try:
            return process_request(request, adapter, settings, budget_seconds=90)
        except Exception:
            raise ApiError(502, '文本识别服务不可用或超时，请稍后重试') from None
    finally:
        _ACTIVE.release()


def initialize(db):
    import task_relations
    task_relations.initialize(db)
    usage.initialize(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS recognition_batches(user_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL, demo INTEGER NOT NULL, PRIMARY KEY(user_id,request_id));
    CREATE TABLE IF NOT EXISTS recognition_candidates(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,request_id TEXT NOT NULL,candidate TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',version INTEGER NOT NULL DEFAULT 1,resolution TEXT);
    CREATE TABLE IF NOT EXISTS recognition_import_records(user_id TEXT NOT NULL,request_id TEXT NOT NULL,import_id TEXT NOT NULL,created_at TEXT NOT NULL,source TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,request_id));
    CREATE TABLE IF NOT EXISTS recognition_record_policies(user_id TEXT NOT NULL,request_id TEXT NOT NULL,input_id TEXT NOT NULL,policy TEXT,PRIMARY KEY(user_id,request_id,input_id));
    ''')

def batches(db,uid):
    result=[]
    for row in db.execute('SELECT * FROM recognition_batches WHERE user_id=? ORDER BY rowid DESC',(uid,)):
        metadata=db.execute('SELECT * FROM recognition_import_records WHERE user_id=? AND request_id=?',(uid,row['request_id'])).fetchone()
        if metadata and metadata['archived']: continue
        items=[dict(id=c['id'],candidate=json.loads(c['candidate']),state=c['state'],version=c['version']) for c in db.execute('SELECT * FROM recognition_candidates WHERE user_id=? AND request_id=?',(uid,row['request_id']))]
        request, output = json.loads(row['request']), json.loads(row['result'])
        if request.get('contract_version')=='0.4':
            import teaching
            output = teaching.submission.project_result(db,uid,row['request_id'],teaching.result_view(request,output))
            cards = {c['candidate_id']: c for c in output['tasks']}
            # History-filtered pending proposals leave the active review view;
            # saved model runs and already applied/ignored user decisions remain.
            items=[item for item in items if item['state']!='pending' or item['candidate']['candidate_id'] in cards]
            for item in items:
                # Refresh the read-only presentation of existing results while
                # preserving storage IDs, review state and user resolutions.
                # Stored cards also carry user-decision guards from reprocessing.
                card=dict(cards.get(item['candidate']['candidate_id'],item['candidate']))
                if json.loads(row['result']).get('classification_mode')==teaching.CLASSIFICATION_MODE:
                    for field in ('notice_type','needs_review','review_reasons'):
                        card[field]=item['candidate'][field]
                item['candidate'] = teaching.submission.decorate(db,uid,row['request_id'],card)
            request = teaching.request_view(request)
        result.append(dict(request=request,result=output,demo=bool(row['demo']),items=items,importId=metadata['import_id'] if metadata else row['request_id'],importedAt=metadata['created_at'] if metadata else None,importSource=metadata['source'] if metadata else None))
    return result

def due(value):
    if value is None: return None
    if not isinstance(value,dict) or value.get('precision') not in ['date','datetime']: raise ValueError()
    if not isinstance(value.get('date'),str) or not re.fullmatch(r'\d{4}-\d\d-\d\d',value['date']): raise ValueError()
    date.fromisoformat(value['date'])
    if value['precision']=='datetime':
        if not isinstance(value.get('time'),str) or not re.fullmatch(r'\d\d:\d\d',value['time']): raise ValueError()
        datetime.strptime(value['time'],'%H:%M')
        return dict(date=value['date'],precision='datetime',time=value['time'])
    return dict(date=value['date'],precision='date')

def can_auto_apply(candidate, request, demo=False):
    if request.get('contract_version')=='0.4':
        import teaching
        request=teaching.request_view(request)
    source=next((r for r in request['records'] if r['input_id']==candidate['input_id']),None)
    d=candidate['deadline']
    valid_due=(d['kind']=='deadline' and d['status']=='explicit' and d['precision'] in ('date','datetime') and bool(d['value'])) or (d['status']=='not_provided' and d['value'] is None)
    if valid_due and d['precision']=='datetime':
        stamp=datetime.fromisoformat(d['value'].replace('Z','+00:00'))
        valid_due=stamp.second==0 and stamp.microsecond==0
    return bool(not demo and source and source['platform'] in ('teaching_web','wechat')
                and candidate['notice_type']=='create' and not candidate['needs_review']
                and not candidate['review_reasons'] and valid_due)

def has_unapplied_auto_tasks(db, uid, request, demo=False):
    # Reopened candidates have already been handled by the user and stay manual.
    if request.get('contract_version')=='0.4':
        import teaching_submission
        if teaching_submission.review_reason(teaching_submission.for_request(db,uid,request['request_id'])):
            return False
    return any(can_auto_apply(json.loads(row['candidate']),request,demo)
               for row in db.execute('SELECT candidate FROM recognition_candidates WHERE user_id=? AND request_id=? AND state="pending" AND version=1',
                                     (uid,request['request_id'])))

def route(handler,db,uid,path,body,ApiError):
    if not path.startswith('/api/recognition/'): return False
    if path=='/api/recognition/task-evidence' and handler.command=='POST':
        workspace=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
        task=next((t for t in json.loads(workspace['value'] if workspace else '[]') if t.get('id')==body.get('task_id')),None)
        if task is None: raise ApiError(404,'任务不存在')
        evidence=[]
        for history in task.get('recognitionHistory',[]):
            candidate=db.execute('SELECT candidate,request_id FROM recognition_candidates WHERE id=? AND user_id=?',(history.get('storageId'),uid)).fetchone()
            if not candidate: continue
            batch=db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,candidate['request_id'])).fetchone()
            if not batch: continue
            item=json.loads(candidate['candidate'])
            request=json.loads(batch['request'])
            if request.get('contract_version')=='0.4':
                import teaching
                request=teaching.request_view(request)
            record=next((r for r in request.get('records',[]) if r.get('input_id')==item.get('input_id')),{})
            evidence.append(dict(action=history.get('action'),quotes=item.get('evidence',[]),basis=item.get('deadline',{}).get('basis'),sources=item.get('sources',[]),original=record.get('text',''),sourceUrl=record.get('source_url'),sourceLabel=record.get('source_label'),platform=record.get('platform')))
        import task_relations
        evidence.extend(task_relations.evidence_entries(db,uid,task['id']))
        handler.reply(200,dict(evidence=evidence));return True
    if path=='/api/recognition/imports' and handler.command=='POST':
        ids=body.get('request_ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(r,str) or not 1<=len(r)<=500 for r in ids): raise ApiError(400,'导入批次格式不正确')
        import_id=body.get('import_id');source=body.get('source');created=body.get('created_at')
        if not isinstance(import_id,str) or not 1<=len(import_id)<=200 or not isinstance(source,str) or not 1<=len(source)<=100: raise ApiError(400,'导入信息格式不正确')
        try:
            if not isinstance(created,str) or len(created)>50 or datetime.fromisoformat(created.replace('Z','+00:00')).tzinfo is None: raise ValueError()
        except (ValueError,TypeError): raise ApiError(400,'导入时间格式不正确')
        for request_id in ids:
            db.execute('INSERT INTO recognition_import_records VALUES(?,?,?,?,?,0) ON CONFLICT(user_id,request_id) DO UPDATE SET import_id=excluded.import_id,created_at=excluded.created_at,source=excluded.source,archived=0',(uid,request_id,import_id,created,source))
        handler.reply(200,dict(saved=True));return True
    if path=='/api/recognition/imports/archive' and handler.command=='POST':
        ids=body.get('request_ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(r,str) or not 1<=len(r)<=500 for r in ids): raise ApiError(400,'导入批次格式不正确')
        db.execute('BEGIN IMMEDIATE')
        for request_id in ids:
            if not db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request_id)).fetchone(): raise ApiError(404,'导入记录不存在')
            # Keep deduplication and created tasks intact; hide the archived batch.
            db.execute('INSERT INTO recognition_import_records VALUES(?,?,?,?,?,1) ON CONFLICT(user_id,request_id) DO UPDATE SET archived=1',(uid,request_id,request_id,datetime.now(timezone.utc).isoformat(),''))
            db.execute('UPDATE recognition_candidates SET state="ignored",version=version+1 WHERE user_id=? AND request_id=? AND state="pending"',(uid,request_id))
        handler.reply(200,batches(db,uid));return True
    if path=='/api/recognition/batches' and handler.command=='GET':
        handler.reply(200,batches(db,uid));return True
    if path in ['/api/recognition/batches','/api/recognition/retry'] and handler.command=='POST' and body.get('contract_version')=='teaching-run/0.1':
        import teaching
        teaching.run(db,uid,body,path.endswith('/retry'),ApiError)
        handler.reply(200,batches(db,uid));return True
    if path in ['/api/recognition/batches','/api/recognition/retry','/api/recognition/demo'] and handler.command=='POST':
        demo=path.endswith('/demo')
        retry=path.endswith('/retry')
        previous=None
        if demo:
            fixture=json.loads((Path(__file__).parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text(encoding='utf-8'))
            request=fixture['request'];request['request_id']='demo:text-contract:0.2'
            result=fixture['expected'];result['request_id']=request['request_id']
        else: request=body
        try: validate_request(request)
        except (ValueError,TypeError,KeyError): raise ApiError(400,'识别请求不符合 v0.2 契约')
        fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        row=db.execute('SELECT * FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request['request_id'])).fetchone()
        if row:
            if row['fingerprint']!=fingerprint: raise ApiError(409,'同一 request_id 不能用于不同输入')
            previous=json.loads(row['result'])
            if not retry or row['demo'] or previous['status'] in ('success','no_tasks'):
                handler.reply(200,batches(db,uid));return True
        extraction_request=request
        if previous:
            completed={r['input_id'] for r in previous['record_results'] if r['status']!='failed'}
            extraction_request=dict(request,records=[r for r in request['records'] if r['input_id'] not in completed])
        if not demo:
            if not request['records']:
                result=dict(contract_version='0.2',request_id=request['request_id'],status='no_tasks',record_results=[],tasks=[],errors=[])
            else:
                _EXECUTION.policy = None
                result=recognize(extraction_request,ApiError)
        policy = getattr(_EXECUTION,'policy',None) if not demo and extraction_request['records'] else None
        try:
            result=validate_result(extraction_request,result)
            new_tasks=list(result['tasks'])
            if previous:
                # Keep successful candidates and their confirmation state; only replace failures.
                retried={r['input_id'] for r in extraction_request['records']}
                if not result['record_results']:
                    raise ValueError('retry envelope failed')
                outcomes={r['input_id']:r for r in previous['record_results'] if r['input_id'] not in retried}
                outcomes.update({r['input_id']:r for r in result['record_results']})
                result['record_results']=[outcomes[r['input_id']] for r in request['records']]
                result['tasks']=previous['tasks']+new_tasks
                failures=sum(r['status']=='failed' for r in result['record_results'])
                result['status']=('failed' if failures==len(request['records']) else 'partial_success') if failures else ('success' if result['tasks'] else 'no_tasks')
                result=validate_result(request,result)
        except (ValueError,TypeError,KeyError): raise ApiError(502,'文本识别结果不符合 v0.2 契约，未保存候选')
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT fingerprint,result FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request['request_id'])).fetchone()
        if row:
            if row['fingerprint']!=fingerprint: raise ApiError(409,'请求内容冲突')
            # Another request may have finished during the model call. Never overwrite it.
            if previous is not None and json.loads(row['result'])==previous:
                db.execute('UPDATE recognition_batches SET result=? WHERE user_id=? AND request_id=?',(json.dumps(result),uid,request['request_id']))
                for c in new_tasks: db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',(secrets.token_hex(16),uid,request['request_id'],json.dumps(c)))
                for record in extraction_request['records']:
                    db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?) ON CONFLICT(user_id,request_id,input_id) DO UPDATE SET policy=excluded.policy',
                        (uid,request['request_id'],record['input_id'],json.dumps(policy) if policy is not None else None))
        else:
            db.execute('INSERT OR IGNORE INTO recognition_import_records VALUES(?,?,?,?,?,0)',(uid,request['request_id'],request['request_id'],datetime.now(timezone.utc).isoformat(),''))
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?)',(uid,request['request_id'],fingerprint,json.dumps(request),json.dumps(result),int(demo)))
            for c in result['tasks']: db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',(secrets.token_hex(16),uid,request['request_id'],json.dumps(c)))
            for record in request['records']:
                db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?)',
                    (uid,request['request_id'],record['input_id'],json.dumps(policy) if policy is not None else None))
        handler.reply(200,batches(db,uid));return True
    if path.startswith('/api/recognition/candidates/') and path.endswith('/resolve') and handler.command=='POST':
        from urllib.parse import unquote
        cid=unquote(path.split('/')[-2])
        db.execute('BEGIN IMMEDIATE')
        resolve_candidate(db,uid,cid,body,ApiError)
        handler.reply(200,dict(batches=batches(db,uid)));return True
    raise ApiError(404,'识别接口不存在')


def resolve_candidate(db, uid, cid, body, ApiError):
    """Resolve in the caller transaction for HTTP, background sync and reprocessing."""
    row=db.execute('SELECT * FROM recognition_candidates WHERE id=? AND user_id=?',(cid,uid)).fetchone()
    if not row: raise ApiError(404,'候选不存在')
    canonical=json.dumps(body,sort_keys=True)
    action=body.get('action')
    if action=='reopen':
        if body.get('version')!=row['version']: raise ApiError(409,'候选已变化，请刷新')
        if row['state']!='ignored': raise ApiError(409,'只有不加入任务的候选可以撤回')
        db.execute('UPDATE recognition_candidates SET state="pending",version=version+1,resolution=NULL WHERE id=?',(cid,))
        return
    if row['state']!='pending':
        if row['resolution']!=canonical: raise ApiError(409,'候选已处理，不能用不同操作重复确认')
        return
    if body.get('version')!=row['version']: raise ApiError(409,'候选已变化，请刷新')
    c=json.loads(row['candidate'])
    if action not in ['create','update','postpone','cancel','ignore']: raise ApiError(400,'请选择明确操作')
    auto=body.get('auto_apply') is True
    if auto:
        d=c['deadline']
        batch=db.execute('SELECT demo,request FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,row['request_id'])).fetchone()
        if json.loads(batch['request']).get('contract_version')=='0.4':
            import teaching
            teaching.submission.guard_auto_apply(db,uid,row['request_id'],ApiError)
            teaching.resolve_snapshot(db,uid,row['request_id'],ApiError)
        if action!='create' or not can_auto_apply(c,json.loads(batch['request']),batch['demo']):
            raise ApiError(400,'此候选需要人工处理，不能自动加入日程')
    if action!='ignore':
        if not auto and body.get('reviewed') is not True: raise ApiError(400,'请先核对证据与变更')
        if c['notice_type']!='uncertain' and action!=c['notice_type']: raise ApiError(400,'操作与通知类型不符')
        taskrow=db.execute('SELECT * FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
        version=taskrow['version'] if taskrow else 0
        if not auto and body.get('tasks_version')!=version: raise ApiError(409,'任务已变化，请刷新后重新核对')
        tasks=json.loads(taskrow['value']) if taskrow else []
        target=next((t for t in tasks if t['id']==body.get('target_id')),None)
        if action!='create' and not target: raise ApiError(400,'请选择当前账号的目标任务')
        if action=='create' and body.get('target_id'): raise ApiError(400,'新增任务不能带目标 ID')
        draft=body.get('draft',{})
        if auto:
            automatic_due=None
            if d['status']=='explicit':
                value=d['value']
                if d['precision']=='datetime':
                    local=datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
                    automatic_due=dict(date=local.date().isoformat(),precision='datetime',time=local.strftime('%H:%M'))
                else: automatic_due=dict(date=value,precision='date')
            draft=dict(title=c['title'],description=c['description'],course=c['course_or_project'],due=automatic_due)
        if action!='cancel':
            if not isinstance(draft.get('title'),str) or not draft['title'].strip() or not isinstance(draft.get('description'),str): raise ApiError(400,'请填写有效任务内容')
            course=draft.get('course',c['course_or_project'])
            if course is not None and not isinstance(course,str): raise ApiError(400,'课程或项目格式不正确')
            course=course.strip() or None if isinstance(course,str) else None
            try: confirmed_due=due(draft.get('due'))
            except (ValueError,KeyError,TypeError): raise ApiError(400,'截止日期格式不正确')
            if action=='postpone' and confirmed_due is None: raise ApiError(400,'延期必须明确新的截止日期')
            content_type=draft.get('content_type', c.get('content_type', 'task'))
            if content_type not in ('task','notice'): raise ApiError(400,'内容类型必须是任务或通知')
            if action=='create':
                platform=c['sources'][0]['platform']
                source={'teaching_web':'教学网','wechat':'微信'}.get(platform,c['sources'][0]['label'] or '文本识别')
                target=dict(id=secrets.token_hex(16),title=draft['title'].strip(),description=draft['description'],due=confirmed_due,category='个人事务',course=c['course_or_project'],priority='medium',status='not_started',source=source,subtasks=[],plans=[],createdAt=datetime.now(timezone.utc).isoformat())
                tasks.append(target)
                target['course']=course
                target['category']='课程作业' if course else '个人事务'
                target['content_type']=content_type
                if content_type=='notice': target['planningExcluded']=True
            elif action=='postpone': target['due']=confirmed_due
            else:
                target.update(title=draft['title'].strip(),description=draft['description'],due=confirmed_due)
                target['content_type']=content_type
                if 'course' in draft: target['course']=course
            if action=='create' or (action=='update' and c['prerequisites']):
                target['sourceRequirements']=c['prerequisites']
        else: target['status']='cancelled'
        target.setdefault('recognitionHistory',[]).append(dict(storageId=cid,requestId=row['request_id'],candidateId=c['candidate_id'],action=action))
        import task_relations
        if auto: task_relations.remember(db,uid,target)
        else: task_relations.manual_write(db,uid,tasks)
        db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',(uid,'tasks',json.dumps(tasks),version+1))
        usage.workspace_changes(db,uid,json.loads(taskrow['value']) if taskrow else [],tasks)
    db.execute('UPDATE recognition_candidates SET state=?,version=version+1,resolution=? WHERE id=?',('ignored' if action=='ignore' else 'applied',canonical,cid))
    return

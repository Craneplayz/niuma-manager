"""Authenticated recognition transport and transactional candidate resolution."""
import hashlib
import json
import os
import secrets
import re
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.request import Request, urlopen
from text_contract import validate_request, validate_result

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS recognition_batches(user_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL, demo INTEGER NOT NULL, PRIMARY KEY(user_id,request_id));
    CREATE TABLE IF NOT EXISTS recognition_candidates(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,request_id TEXT NOT NULL,candidate TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',version INTEGER NOT NULL DEFAULT 1,resolution TEXT);
    ''')

def batches(db,uid):
    result=[]
    for row in db.execute('SELECT * FROM recognition_batches WHERE user_id=? ORDER BY rowid DESC',(uid,)):
        items=[dict(id=c['id'],candidate=json.loads(c['candidate']),state=c['state'],version=c['version']) for c in db.execute('SELECT * FROM recognition_candidates WHERE user_id=? AND request_id=?',(uid,row['request_id']))]
        result.append(dict(request=json.loads(row['request']),result=json.loads(row['result']),demo=bool(row['demo']),items=items))
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

def route(handler,db,uid,path,body,ApiError):
    if not path.startswith('/api/recognition/'): return False
    if path=='/api/recognition/batches' and handler.command=='GET':
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
                endpoint=os.environ.get('TEXT_HARNESS_URL')
                if not endpoint: raise ApiError(503,'文本识别服务尚未接入；可查看协议演示，或手动创建任务')
                headers={'Content-Type':'application/json'}
                if os.environ.get('TEXT_HARNESS_TOKEN'): headers['Authorization']='Bearer '+os.environ['TEXT_HARNESS_TOKEN']
                try:
                    with urlopen(Request(endpoint,json.dumps(extraction_request).encode(),headers),timeout=95) as response: result=json.loads(response.read(2000000))
                except Exception: raise ApiError(502,'文本识别服务不可用或超时，请稍后重试')
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
        else:
            db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,?)',(uid,request['request_id'],fingerprint,json.dumps(request),json.dumps(result),int(demo)))
            for c in result['tasks']: db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',(secrets.token_hex(16),uid,request['request_id'],json.dumps(c)))
        handler.reply(200,batches(db,uid));return True
    if path.startswith('/api/recognition/candidates/') and path.endswith('/resolve') and handler.command=='POST':
        cid=path.split('/')[-2]
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM recognition_candidates WHERE id=? AND user_id=?',(cid,uid)).fetchone()
        if not row: raise ApiError(404,'候选不存在')
        canonical=json.dumps(body,sort_keys=True)
        if row['state']!='pending':
            if row['resolution']!=canonical: raise ApiError(409,'候选已处理，不能用不同操作重复确认')
            handler.reply(200,dict(batches=batches(db,uid)));return True
        if body.get('version')!=row['version']: raise ApiError(409,'候选已变化，请刷新')
        action=body.get('action');c=json.loads(row['candidate'])
        if action not in ['create','update','postpone','cancel','ignore']: raise ApiError(400,'请选择明确操作')
        if action!='ignore':
            if body.get('reviewed') is not True: raise ApiError(400,'请先核对证据与变更')
            if c['notice_type']!='uncertain' and action!=c['notice_type']: raise ApiError(400,'操作与通知类型不符')
            taskrow=db.execute('SELECT * FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
            version=taskrow['version'] if taskrow else 0
            if body.get('tasks_version')!=version: raise ApiError(409,'任务已变化，请刷新后重新核对')
            tasks=json.loads(taskrow['value']) if taskrow else []
            target=next((t for t in tasks if t['id']==body.get('target_id')),None)
            if action!='create' and not target: raise ApiError(400,'请选择当前账号的目标任务')
            if action=='create' and body.get('target_id'): raise ApiError(400,'新增任务不能带目标 ID')
            draft=body.get('draft',{})
            if action!='cancel':
                if not isinstance(draft.get('title'),str) or not draft['title'].strip() or not isinstance(draft.get('description'),str): raise ApiError(400,'请填写有效任务内容')
                course=draft.get('course',c['course_or_project'])
                if course is not None and not isinstance(course,str): raise ApiError(400,'课程或项目格式不正确')
                course=course.strip() or None if isinstance(course,str) else None
                try: confirmed_due=due(draft.get('due'))
                except (ValueError,KeyError,TypeError): raise ApiError(400,'截止日期格式不正确')
                if action=='postpone' and confirmed_due is None: raise ApiError(400,'延期必须明确新的截止日期')
                if action=='create':
                    target=dict(id=secrets.token_hex(16),title=draft['title'].strip(),description=draft['description'],due=confirmed_due,category='个人事务',course=c['course_or_project'],priority='medium',status='not_started',source=c['sources'][0]['label'] or '文本识别',subtasks=[],plans=[],createdAt=datetime.now(timezone.utc).isoformat())
                    tasks.append(target)
                    target['course']=course
                    target['category']='课程作业' if course else '个人事务'
                elif action=='postpone': target['due']=confirmed_due
                else:
                    target.update(title=draft['title'].strip(),description=draft['description'],due=confirmed_due)
                    if 'course' in draft: target['course']=course
                if action=='create' or (action=='update' and c['prerequisites']):
                    target['sourceRequirements']=c['prerequisites']
            else: target['status']='cancelled'
            target.setdefault('recognitionHistory',[]).append(dict(storageId=cid,requestId=row['request_id'],candidateId=c['candidate_id'],action=action))
            db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',(uid,'tasks',json.dumps(tasks),version+1))
        db.execute('UPDATE recognition_candidates SET state=?,version=version+1,resolution=? WHERE id=?',('ignored' if action=='ignore' else 'applied',canonical,cid))
        handler.reply(200,dict(batches=batches(db,uid)));return True
    raise ApiError(404,'识别接口不存在')

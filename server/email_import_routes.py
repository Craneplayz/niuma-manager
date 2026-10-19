"""HTTP validation and response handling for the email import service.

Loaded lazily by email_import.route after the orchestration module initializes.
Database storage is owned by email_import_store; runtime policy checks stay in
email_import so every transport uses the same consent and cache decisions.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, parse_qs

import email_import as runtime
import email_source
import email_import_store as store
import email_telemetry

def route(handler,db,uid,path,body,ApiError,key_path,database):
    if path in ('/api/sources/email/credentials','/api/sources/email/credentials/remove'):
        return False
    if path=='/api/sources/email/preferences' and handler.command=='GET':
        handler.reply(200,runtime._prefs(db,uid)); return True
    if path=='/api/sources/email/preferences' and handler.command=='POST':
        consent=body.get('consent'); process_all=body.get('process_all')
        if type(consent)!=bool or type(process_all)!=bool: raise ApiError(400,'邮箱处理偏好格式不正确')
        if consent:
            mailbox=db.execute('SELECT address FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
            if not mailbox: raise ApiError(400,'请先保存北大邮箱客户端凭据')
            db.execute('INSERT INTO email_import_preferences(user_id,mailbox,consent_version,consented_at,process_all) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET mailbox=excluded.mailbox,consent_version=excluded.consent_version,consented_at=excluded.consented_at,process_all=excluded.process_all',(uid,mailbox['address'],runtime.CONSENT_VERSION,datetime.now().astimezone().isoformat(),int(process_all)))
        else:
            db.execute('INSERT INTO email_import_preferences(user_id,mailbox,consent_version,consented_at,process_all) VALUES(?,NULL,NULL,NULL,0) ON CONFLICT(user_id) DO UPDATE SET mailbox=NULL,consent_version=NULL,consented_at=NULL,process_all=0',(uid,))
            db.execute('DELETE FROM email_import_body_snapshots WHERE user_id=?',(uid,))
        handler.reply(200,runtime._prefs(db,uid)); return True
    if path=='/api/sources/email/exclusions' and handler.command=='POST':
        ids=body.get('input_ids'); excluded=body.get('excluded')
        if not isinstance(ids,list) or len(ids)>1000 or any(not isinstance(x,str) or not x or len(x)>120 for x in ids) or len(ids)!=len(set(ids)) or type(excluded)!=bool: raise ApiError(400,'邮件排除列表格式不正确')
        store.begin(db)
        for input_id in ids:
            if excluded:
                db.execute('INSERT INTO email_import_exclusions VALUES(?,?,1) ON CONFLICT(user_id,input_id) DO UPDATE SET excluded=1',(uid,input_id))
                db.execute('DELETE FROM email_import_body_snapshots WHERE user_id=? AND input_id=?',(uid,input_id))
            else: db.execute('DELETE FROM email_import_exclusions WHERE user_id=? AND input_id=?',(uid,input_id))
        handler.reply(200,{'saved':len(ids)}); return True
    if path=='/api/sources/email/preview' and handler.command=='POST':
        if not body: body={}
        limit=body.get('limit',50); start=body.get('start_date'); offset=body.get('offset',0); page=body.get('page_size',50)
        filter_name=body.get('filter','all'); sort_name=body.get('sort','attention')
        if not isinstance(filter_name,str) or not isinstance(sort_name,str) or filter_name not in runtime.EMAIL_LIST_FILTERS or sort_name not in runtime.EMAIL_LIST_SORTS: raise ApiError(400,'邮件筛选或排序条件无效')
        if type(offset)!=int or offset<0 or type(page)!=int or not 1<=page<=100: raise ApiError(400,'邮件分页条件无效')
        if start is None and (type(limit)!=int or limit<1): raise ApiError(400,'邮件数量范围无效')
        if start is not None and (not isinstance(start,str) or not re.fullmatch(r'\d{4}-\d\d-\d\d',start)): raise ApiError(400,'开始日期格式应为 YYYY-MM-DD')
        from source_sync import cipher
        from cryptography.fernet import InvalidToken
        cred=db.execute('SELECT address,username,secret FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
        if not cred: raise ApiError(400,'请先保存北大邮箱客户端凭据')
        refresh=body.get('refresh',False); cache_only=body.get('cache_only',False); snapshot_id=body.get('snapshot_id'); load_all=body.get('load_all',False)
        if (type(refresh) is not bool or type(cache_only) is not bool or type(load_all) is not bool or (load_all and offset!=0) or (refresh and cache_only)
                or (snapshot_id is not None and (not isinstance(snapshot_id,str) or not 1<=len(snapshot_id)<=120))):
            raise ApiError(400,'邮件缓存条件无效')
        def load_password():
            try: password=cipher(key_path,ApiError).decrypt(cred['secret']).decode()
            except (InvalidToken,UnicodeDecodeError): raise ApiError(503,'无法解密邮箱凭据；请重新保存')
            return cred['username'],password
        credential_key=runtime._credential_key(cred)
        def same_connection(check_db):
            current=check_db.execute('SELECT address,username,secret FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
            return bool(current and runtime._credential_key(current)==credential_key)
        try:
            with email_telemetry.measure(database,uid,'headers'):
                result=email_source.preview_cached(database,uid,cred['address'],limit=limit,start_date=start,offset=offset,page_size=page,
                    refresh=refresh,cache_only=cache_only,snapshot_id=snapshot_id,password_loader=load_password,
                    guard=same_connection,select_uids=runtime._email_list_selection(db,uid,filter_name,sort_name),load_all=load_all)
        except email_source.EmailSourceError as error: raise ApiError(error.status,error.message)
        if not same_connection(db): raise ApiError(409,'邮箱凭据在预览期间发生变化，请重新预览')
        ids=[m['input_id'] for m in result['messages']]
        current_policy=runtime.current_policy_version()
        policy_metadata=runtime.policy_cache_metadata(db,uid,ids,current=current_policy)
        states=runtime._message_states(db,uid,ids)
        messages=[dict(message,**states[message['input_id']],**policy_metadata[message['input_id']])
                  for message in result['messages']]
        result['messages']=messages
        result['summary'].update({state:sum(m['state']==state for m in messages) for state in
            ('unprocessed','excluded','queued','processing','has_tasks','no_tasks','failed','interrupted')})
        result['summary']['page_count']=len(ids)
        if result.get('cache',{}).get('snapshot_id'):
            try:
                result['recognition_updates']=runtime._recognition_updates(db,uid,dict(limit=limit,start_date=start),result['cache']['snapshot_id'],current=current_policy)[0]
            except email_source.EmailSourceError as error: raise ApiError(error.status,error.message) from None
        handler.reply(200,result); return True
    if path=='/api/sources/email/reprocess' and handler.command=='POST':
        op=body.get('operation_id'); ids=body.get('input_ids'); confirmed=body.get('consent')
        if not isinstance(op,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',op): raise ApiError(400,'操作编号无效')
        if confirmed is not True: raise ApiError(400,'请确认本次重新识别邮件正文')
        try: runtime._require_consent(db,uid)
        except ValueError as error: raise ApiError(400,str(error)) from None
        range_spec=body.get('range')
        range_update=range_spec is not None
        if range_update:
            if ids is not None or not isinstance(range_spec,dict) or set(range_spec)-{'limit','start_date'}:
                raise ApiError(400,'邮件处理范围无效')
            if type(body.get('include_unknown',False)) is not bool:
                raise ApiError(400,'未知版本选择无效')
            if any(not isinstance(body.get(key),str) or not 1<=len(body[key])<=120
                   for key in ('snapshot_id','policy_version','selection_token')):
                raise ApiError(400,'请刷新邮件列表后重试')
            selection=dict(kind='reprocess',range=range_spec,snapshot_id=body['snapshot_id'],
                policy_version=body['policy_version'],selection_token=body['selection_token'],include_unknown=body.get('include_unknown',False))
        else:
            if not isinstance(ids,list) or not ids or len(ids)>1000 or any(not isinstance(x,str) or len(x)>120 for x in ids) or len(ids)!=len(set(ids)):
                raise ApiError(400,'邮件处理范围无效')
            selection=dict(kind='reprocess',input_ids=ids)
        store.begin(db)
        try: runtime._require_consent(db,uid)
        except ValueError as error: raise ApiError(400,str(error)) from None
        cred=db.execute('SELECT address,username,secret FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
        signature=hashlib.sha256(json.dumps(selection,sort_keys=True).encode()).hexdigest()
        prior=db.execute('SELECT job_id,snapshot FROM email_import_jobs WHERE user_id=? AND operation_id=?',(uid,op)).fetchone()
        if prior:
            previous=store.load_job(db, uid, prior['job_id'], prior['snapshot'])
            if previous.get('selection_hash')!=signature or previous.get('kind')!='reprocess': raise ApiError(409,'操作编号已用于其他邮件范围')
            handler.reply(200,runtime._public_job(previous)); return True
        if db.execute("SELECT 1 FROM email_import_jobs WHERE user_id=? AND state IN ('queued','running')",(uid,)).fetchone():
            raise ApiError(409,'已有邮件处理任务正在运行')
        runtime._check_admission(db, ApiError, database)
        if range_update:
            try: updates,outdated,unknown=runtime._recognition_updates(db,uid,range_spec,body['snapshot_id'])
            except email_source.EmailSourceError as error: raise ApiError(error.status,error.message) from None
            if not updates['available']: raise ApiError(503,'当前识别规则不可用，请检查识别服务配置')
            if body['policy_version']!=updates['policy_version'] or body['selection_token']!=updates['selection_token']:
                raise ApiError(409,'识别规则或邮件状态已变化，请刷新列表后重新确认')
            ids=outdated+(unknown if body.get('include_unknown',False) else [])
            if not ids: raise ApiError(409,'当前范围没有需要更新的识别结果')
        prefix=email_source._identity(cred['address'],'x',1).rsplit(':',2)[0]+':'
        if any(not re.fullmatch(re.escape(prefix)+r'\d+:\d+',x) for x in ids): raise ApiError(400,'邮件选择不属于当前邮箱')
        items=[]
        for input_id in ids:
            state,excluded,count,request_id,error=runtime._item_state(db,uid,input_id)
            if state not in ('has_tasks','no_tasks') or excluded:
                raise ApiError(409,'只能重新识别当前邮箱中已有成功缓存且未排除的邮件')
            stored=db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?',(uid,request_id)).fetchone()
            if not stored:
                raise ApiError(409,'邮件正文快照不可用，请重新导入该邮件')
            try: runtime._single_email_record(json.loads(stored['request']),input_id)
            except (ValueError,TypeError): raise ApiError(409,'邮件正文快照不可用，请重新导入该邮件') from None
            items.append(dict(input_id=input_id,state='queued',task_count=None,request_id=None,error=None,cached=False))
        job_id='email-job:'+secrets.token_hex(16)
        job=dict(kind='reprocess',confirmed=True,job_id=job_id,state='queued',mailbox=cred['address'],credential=runtime._credential_key(cred),queued_at=datetime.now().astimezone().isoformat(),selection_hash=signature,range_spec=None,counts={'selected':len(items),'processed':0,'cached':0,'excluded':0,'failed':0,'pending':len(items)},items=items,error=None,stop=False)
        store.insert_job(db, uid, job_id, op, 'queued', job)
        database_path=database if isinstance(database,(str,Path)) else getattr(database,'name',None)
        if not database_path or str(database_path)==':memory:': raise ApiError(503,'持久化邮件处理任务不可用')
        db.commit(); runtime._launch(database_path,uid,job_id,ApiError,key_path)
        handler.reply(202,runtime._public_job(job)); return True
    if path=='/api/sources/email/jobs' and handler.command=='POST':
        try: runtime._require_consent(db,uid)
        except ValueError as e: raise ApiError(400,str(e))
        op=body.get('operation_id'); ids=body.get('input_ids'); rng=body.get('range')
        if not isinstance(op,str) or not re.fullmatch(r'[\w:.-]{1,120}',op): raise ApiError(400,'操作编号无效')
        if ids is None:
            if not isinstance(rng,dict) or set(rng)-{'limit','start_date'}: raise ApiError(400,'邮件处理范围无效')
            start=rng.get('start_date'); limit=rng.get('limit',50)
            if start is None and (type(limit) is not int or limit<1): raise ApiError(400,'邮件数量范围无效')
            if start is not None:
                try: email_source._start_day(start)
                except email_source.EmailSourceError: raise ApiError(400,'开始日期格式应为 YYYY-MM-DD') from None
            range_spec={'limit':limit,'start_date':start}
            signature=hashlib.sha256(json.dumps({'range':range_spec},sort_keys=True).encode()).hexdigest()
        else:
            if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or len(x)>120 for x in ids) or len(ids)!=len(set(ids)): raise ApiError(400,'邮件处理范围无效')
            range_spec=None
            signature=hashlib.sha256(json.dumps({'input_ids':ids},sort_keys=True).encode()).hexdigest()
        store.begin(db); runtime._require_consent(db,uid)
        prior=db.execute('SELECT job_id,snapshot FROM email_import_jobs WHERE user_id=? AND operation_id=?',(uid,op)).fetchone()
        if prior:
            previous=store.load_job(db, uid, prior['job_id'], prior['snapshot'])
            if previous.get('selection_hash') and previous['selection_hash']!=signature:
                raise ApiError(409,'操作编号已用于其他邮件范围')
            handler.reply(200,runtime._public_job(previous)); return True
        active=db.execute("SELECT 1 FROM email_import_jobs WHERE user_id=? AND state IN ('queued','running')",(uid,)).fetchone()
        if active: raise ApiError(409,'已有邮件处理任务正在运行')
        runtime._check_admission(db, ApiError, database)
        job_id='email-job:'+secrets.token_hex(16)
        items=[]
        for x in ids or []:
            state,excluded,count,request_id,error=runtime._item_state(db,uid,x)
            items.append(dict(input_id=x,state='excluded' if excluded else ('has_tasks' if state=='has_tasks' else ('no_tasks' if state=='no_tasks' else ('failed' if state=='failed' else 'queued'))),task_count=count,request_id=request_id,error=error,cached=state in ('has_tasks','no_tasks')))
        credential=db.execute('SELECT address,username,secret FROM email_source_credentials WHERE user_id=?',(uid,)).fetchone()
        if ids:
            prefix=email_source._identity(credential['address'],'x',1).rsplit(':',2)[0]+':'
            if any(not re.fullmatch(re.escape(prefix)+r'\d+:\d+', x) for x in ids):
                raise ApiError(400,'邮件选择不属于当前邮箱')
        job=dict(job_id=job_id,state='queued',mailbox=credential['address'],credential=runtime._credential_key(credential),queued_at=datetime.now().astimezone().isoformat(),selection_hash=signature,range_spec=range_spec,counts={'selected':len(items),'processed':sum(i['state'] in ('has_tasks','no_tasks') for i in items),'cached':sum(i.get('cached',False) for i in items),'excluded':sum(i['state']=='excluded' for i in items),'failed':sum(i['state']=='failed' for i in items),'pending':sum(i['state']=='queued' for i in items)},items=items,error=None,stop=False)
        store.insert_job(db, uid, job_id, op, 'queued', job)
        path=database if isinstance(database,(str,Path)) else getattr(database,'name',None)
        if not path or str(path)==':memory:': raise ApiError(503,'持久化邮件处理任务不可用')
        db.commit()
        runtime._launch(path,uid,job_id,ApiError,key_path)
        handler.reply(202,runtime._public_job(job)); return True
    if path=='/api/sources/email/jobs' and handler.command=='GET':
        rows=db.execute('SELECT job_id,snapshot FROM email_import_jobs WHERE user_id=? ORDER BY rowid DESC LIMIT 50',(uid,)).fetchall()
        summary = parse_qs(getattr(handler, 'path', '') .partition('?')[2]).get('summary') == ['1']
        if summary:
            jobs = [dict(json.loads(r['snapshot']), job_id=r['job_id']) for r in rows]
            revision_row = db.execute('SELECT revision FROM email_import_user_revision WHERE user_id=?', (uid,)).fetchone()
            revision = int(revision_row['revision']) if revision_row else 0
            handler.reply(200, {'revision': revision, 'jobs': [runtime._public_job_summary(job) for job in jobs]})
        else:
            jobs = [store.load_job(db, uid, r['job_id'], r['snapshot']) for r in rows]
            handler.reply(200, {'jobs': [runtime._public_job(job) for job in jobs]})
        return True
    m=re.fullmatch(r'/api/sources/email/jobs/([^/]+)(?:/(stop|retry))?',path)
    if m:
        job_id,action=m.groups()
        job_id=unquote(job_id)
        row=db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',(uid,job_id)).fetchone()
        if not row: raise ApiError(404,'邮件处理任务不存在')
        if action is None and handler.command=='GET':
            if parse_qs(getattr(handler, 'path', '').partition('?')[2]).get('summary') == ['1']:
                handler.reply(200, runtime._public_job_summary(dict(json.loads(row['snapshot']), job_id=job_id)))
            else:
                handler.reply(200,runtime._public_job(store.load_job(db, uid, job_id, row['snapshot'])))
            return True
        job=store.load_job(db, uid, job_id, row['snapshot'])
        if action in ('stop', 'retry') and handler.command == 'POST':
            store.begin(db)
            # Read again under the write lock; never overwrite concurrent progress.
            row = db.execute('SELECT snapshot FROM email_import_jobs WHERE user_id=? AND job_id=?',
                             (uid, job_id)).fetchone()
            job = store.load_job(db, uid, job_id, row['snapshot'])
            if action == 'stop':
                if job['state'] in ('queued', 'running'):
                    job.update(stop=True, state='stopped')
                    for item in job['items']:
                        if item['state'] in ('queued', 'processing'):
                            item.update(state='interrupted', error=None)
                    store.save(db, uid, job_id, job)
                handler.reply(200, runtime._public_job(job))
                return True
            if job.get('kind')=='reprocess':
                raise ApiError(400,'请通过重新识别接口重新确认本次邮件处理')
            try:
                runtime._require_consent(db, uid)
            except ValueError as error:
                raise ApiError(400, str(error)) from None
            operation = body.get('operation_id')
            if not isinstance(operation, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', operation):
                raise ApiError(400, '操作编号无效')
            prior = db.execute('SELECT job_id,snapshot FROM email_import_jobs WHERE user_id=? AND operation_id=?',
                               (uid, operation)).fetchone()
            if prior:
                previous = store.load_job(db, uid, prior['job_id'], prior['snapshot'])
                if previous.get('parent_job_id') != job_id:
                    raise ApiError(409, '操作编号已用于其他邮件任务')
                handler.reply(200, runtime._public_job(previous))
                return True
            if db.execute("SELECT 1 FROM email_import_jobs WHERE user_id=? AND state IN ('queued','running')",
                          (uid,)).fetchone():
                raise ApiError(409, '已有邮件处理任务正在运行')
            runtime._check_admission(db, ApiError, database)
            credential = db.execute('SELECT address,username,secret FROM email_source_credentials WHERE user_id=?',
                                    (uid,)).fetchone()
            if job.get('mailbox') != credential['address']:
                raise ApiError(409, '邮箱连接已变化，请重新选择邮件')
            for item in job['items']:
                state, excluded, count, request_id, error = runtime._item_state(db, uid, item['input_id'])
                item.update(state=state if state in ('has_tasks','no_tasks','excluded') else 'queued',
                            task_count=count, request_id=request_id, error=None,
                            cached=state in ('has_tasks','no_tasks'))
            next_id = 'email-job:' + secrets.token_hex(16)
            job.update(job_id=next_id, parent_job_id=job_id, state='queued', stop=False, error=None,
                       queued_at=datetime.now().astimezone().isoformat(), credential=runtime._credential_key(credential))
            job['counts'] = store.counts(job)
            store.insert_job(db, uid, next_id, operation, 'queued', job)
            db.commit()
            runtime._launch(database, uid, next_id, ApiError, key_path)
            handler.reply(202, runtime._public_job(job))
            return True
        return False
    return False

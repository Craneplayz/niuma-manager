"""Account-scoped, explicit reprocessing of saved v0.2 source records."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from copy import deepcopy
import json
import secrets
import re
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
from jsonschema.exceptions import ValidationError

import recognition
from contracts import reprocessing as contract
from text_contract import validate_result

_lock = threading.Lock()
_workers = set()


def stamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(db, recover=True):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS reprocessing_sources(user_id TEXT NOT NULL, account TEXT NOT NULL, source_id TEXT NOT NULL UNIQUE, PRIMARY KEY(user_id,account));
    CREATE TABLE IF NOT EXISTS reprocessing_runs(user_id TEXT NOT NULL, run_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, selection TEXT NOT NULL, snapshot TEXT NOT NULL, inputs TEXT NOT NULL, results TEXT NOT NULL, UNIQUE(user_id,operation_id));
    CREATE TABLE IF NOT EXISTS reprocessing_latest(user_id TEXT NOT NULL, source_id TEXT NOT NULL, input_id TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(user_id,source_id,input_id));
    ''')
    if recover:
        for row in db.execute('SELECT run_id,snapshot FROM reprocessing_runs').fetchall():
            run = json.loads(row['snapshot'])
            if run['state'] not in contract.FINAL:
                for record in run['records']:
                    if record['state'] in ('queued', 'running'):
                        record.update(state='interrupted', error=dict(code='interrupted', message='服务重启，未自动重复调用'))
                save(db, run, 'interrupted')


def source_ids(db, uid):
    for row in db.execute('SELECT DISTINCT account FROM source_versions WHERE user_id=?', (uid,)).fetchall():
        db.execute('INSERT OR IGNORE INTO reprocessing_sources VALUES(?,?,?)', (uid, row['account'], secrets.token_hex(16)))
    return db.execute('SELECT * FROM reprocessing_sources WHERE user_id=? ORDER BY source_id', (uid,)).fetchall()


def catalogue(db, uid, cursor):
    # One flat record cursor also paginates a source containing over 100 records.
    try:
        offset = int(cursor or '0')
        if offset < 0:
            raise ValueError()
    except ValueError:
        raise ValueError('invalid cursor')
    sources = source_ids(db, uid)
    rows = db.execute('''SELECT v.*,s.source_id FROM source_versions v JOIN reprocessing_sources s
        ON v.user_id=s.user_id AND v.account=s.account WHERE v.user_id=? ORDER BY s.source_id,v.input_id LIMIT 101 OFFSET ?''', (uid, offset)).fetchall()
    accounts = {}
    consumed=0
    for row in rows[:100]:
        if row['source_id'] not in accounts and len(accounts)>=50:
            break
        consumed+=1
        request = json.loads(row['request'])
        record = request['records'][0]
        stored = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request['request_id'])).fetchone()
        status = 'unknown'
        if stored:
            outcome = next((r for r in json.loads(stored['result'])['record_results'] if r['input_id'] == row['input_id']), None)
            status = outcome['status'] if outcome else 'failed'
        policy = db.execute('SELECT policy FROM recognition_record_policies WHERE user_id=? AND request_id=? AND input_id=?', (uid, request['request_id'], row['input_id'])).fetchone()
        version = json.loads(policy['policy'])['version'] if policy and policy['policy'] else None
        account = accounts.setdefault(row['source_id'], dict(source_account_id=row['source_id'], label='教学网来源 ' + str(next(i for i, s in enumerate(sources, 1) if s['source_id'] == row['source_id'])), records=[]))
        label=re.sub(r'[\x00-\x1f\x7f]',' ',record.get('source_label') or row['input_id'])[:512]
        course=record.get('course_or_project')
        course=re.sub(r'[\x00-\x1f\x7f]',' ',course)[:512] if course else None
        account['records'].append(dict(input_id=row['input_id'],label=label,course_or_project=course,result_status=status,policy_version=version))
    return contract.check('sources', dict(contract_version=contract.VERSION, accounts=list(accounts.values()), next_cursor=str(offset+consumed) if len(rows)>consumed else None))


def save(db, run, state=None):
    before = json.loads(db.execute('SELECT snapshot FROM reprocessing_runs WHERE run_id=?', (run['run_id'],)).fetchone()[0])
    run.update(state=state or run['state'], revision=before['revision']+1, updated_at=stamp())
    run['counts'] = dict(total=len(run['records']), **{s:sum(r['state']==s for r in run['records']) for s in contract.ROW_STATES})
    contract.check_transition(before, run)
    db.execute('UPDATE reprocessing_runs SET snapshot=? WHERE run_id=?', (json.dumps(run), run['run_id']))


def terminal(run):
    states = [r['state'] for r in run['records']]
    if 'running' in states or 'queued' in states:
        return 'stopping' if run['state']=='stopping' else 'running'
    if 'cancelled' in states or run['state']=='stopping':
        return 'stopped'
    if all(s=='succeeded' for s in states):
        return 'succeeded'
    if all(s=='failed' for s in states):
        return 'failed'
    return 'partial_success' if 'succeeded' in states else 'interrupted'


def start(db, uid, body, ApiError):
    contract.check('start', body)
    selection = json.dumps(body, sort_keys=True)
    old = db.execute('SELECT * FROM reprocessing_runs WHERE user_id=? AND operation_id=?', (uid, body['operation_id'])).fetchone()
    if old:
        if not contract.same_operation(json.loads(old['selection']), body):
            raise ApiError(409, '同一操作编号不能更改来源或记录')
        return json.loads(old['snapshot']), False
    active=db.execute("SELECT COUNT(*) FROM reprocessing_runs WHERE json_extract(snapshot,'$.state') IN ('queued','running','stopping')").fetchone()[0]
    if active>=4:
        raise ApiError(503,'重识别队列已满，请稍后重试')
    source = db.execute('SELECT account FROM reprocessing_sources WHERE user_id=? AND source_id=?', (uid, body['source_account_id'])).fetchone()
    if not source:
        raise ApiError(404, '来源不存在')
    inputs = []
    records = []
    for identity in body['input_ids']:
        row = db.execute('SELECT * FROM source_versions WHERE user_id=? AND account=? AND input_id=?', (uid, source['account'], identity)).fetchone()
        if not row:
            raise ApiError(404, '来源记录不存在')
        request = json.loads(row['request'])
        old_batch = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?', (uid, request['request_id'])).fetchone()
        previous = bool(old_batch and json.loads(old_batch['result'])['status'] in ('success','no_tasks'))
        inputs.append(dict(request=request, digest=row['digest'], account=source['account']))
        records.append(dict(input_id=identity, state='queued', attempted=False, policy_version=None, previous_available=previous, old_result_retained=previous, outcome=None, decision_summary=dict(preserved=0,replaced=0,new_pending=0,unresolved=0), error=None))
    run = dict(contract_version=contract.VERSION, run_id=secrets.token_hex(16), operation_id=body['operation_id'], source_account_id=body['source_account_id'], revision=1, state='queued', created_at=stamp(), updated_at=stamp(), records=records, counts=dict(total=len(records), **{s:len(records) if s=='queued' else 0 for s in contract.ROW_STATES}))
    contract.check_start_run(body, run)
    db.execute('INSERT INTO reprocessing_runs VALUES(?,?,?,?,?,?,?)', (uid, run['run_id'], body['operation_id'], selection, json.dumps(run), json.dumps(inputs), '{}'))
    for record in records:
        db.execute('INSERT INTO reprocessing_latest VALUES(?,?,?,?) ON CONFLICT(user_id,source_id,input_id) DO UPDATE SET run_id=excluded.run_id', (uid,run['source_account_id'],record['input_id'],run['run_id']))
    return run, True


def process(path, uid, run_id, ApiError):
    try:
        while True:
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                stored = db.execute('SELECT * FROM reprocessing_runs WHERE user_id=? AND run_id=?', (uid,run_id)).fetchone()
                run = json.loads(stored['snapshot'])
                index = next((i for i,r in enumerate(run['records']) if r['state']=='queued'), None)
                if run['state'] in contract.FINAL or index is None:
                    return
                record = run['records'][index]
                record.update(state='running', attempted=True)
                save(db, run, 'running')
                item = json.loads(stored['inputs'])[index]
            request = dict(item['request'], request_id='reprocessing:'+run_id+':'+str(index))
            result = None
            policy = None
            try:
                result = validate_result(request, recognition.recognize(request, ApiError))
                policy = getattr(recognition._EXECUTION, 'policy', None)
                if not policy or result['status'] not in ('success','no_tasks'):
                    raise ValueError('invalid or unversioned result')
            except Exception:
                result = None
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                stored = db.execute('SELECT * FROM reprocessing_runs WHERE user_id=? AND run_id=?', (uid,run_id)).fetchone()
                run = json.loads(stored['snapshot'])
                if run['state'] in contract.FINAL:
                    return
                record = run['records'][index]
                if policy:
                    record['policy_version'] = policy['version']
                latest = db.execute('SELECT run_id FROM reprocessing_latest WHERE user_id=? AND source_id=? AND input_id=?', (uid,run['source_account_id'],record['input_id'])).fetchone()
                current = db.execute('SELECT digest FROM source_versions WHERE user_id=? AND account=? AND input_id=?', (uid,item['account'],record['input_id'])).fetchone()
                if run['state']=='stopping':
                    record.update(state='interrupted', error=dict(code='interrupted',message='已停止，未接纳迟到结果'))
                elif not latest or latest['run_id']!=run_id or not current or current['digest']!=item['digest']:
                    record['state']='superseded'
                elif result is None:
                    record.update(state='failed',error=dict(code='agent_unavailable',message='识别未成功，已有数据未变更'))
                else:
                    # Legacy records have no verified cross-version action links.
                    # Protect every handled decision; do not match titles or model IDs.
                    old = []
                    for candidate in db.execute('SELECT * FROM recognition_candidates WHERE user_id=?', (uid,)):
                        if json.loads(candidate['candidate'])['input_id']==record['input_id']:
                            old.append(candidate)
                    protected = [c for c in old if c['state']!='pending' or c['version']!=1]
                    candidate_ids=[secrets.token_hex(16) for _ in result['tasks']]
                    plan=contract.association_plan(dict(contract_version=contract.VERSION,
                        previous=[dict(action_id=c['id'],decision='ignored' if c['state']=='ignored' else 'confirmed') for c in protected],
                        candidates=candidate_ids,links=[],confirmed_new=[]))
                    hold=plan['hold_record']
                    summary = record['decision_summary']
                    summary['preserved']=len(protected)
                    summary['unresolved']=len(result['tasks']) if hold else 0
                    fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                    db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0)',(uid,request['request_id'],fingerprint,json.dumps(request),json.dumps(result)))
                    for candidate,identity in zip(result['tasks'],candidate_ids):
                        candidate=deepcopy(candidate)
                        if hold:
                            candidate.update(notice_type='uncertain',needs_review=True,review_reasons=candidate['review_reasons']+['旧任务已有用户决定；请明确选择新增、更新或忽略，不会自动替换旧任务'])
                        db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',(identity,uid,request['request_id'],json.dumps(candidate)))
                    db.execute('INSERT INTO recognition_record_policies VALUES(?,?,?,?)',(uid,request['request_id'],record['input_id'],json.dumps(policy)))
                    if not hold:
                        pending = [c for c in old if c['request_id']==item['request']['request_id'] and c['state']=='pending' and c['version']==1]
                        for candidate in pending:
                            db.execute('DELETE FROM recognition_candidates WHERE id=? AND user_id=?',(candidate['id'],uid))
                        summary.update(replaced=len(pending),new_pending=len(result['tasks']))
                        db.execute('UPDATE source_versions SET request=? WHERE user_id=? AND account=? AND input_id=?',(json.dumps(request),uid,item['account'],record['input_id']))
                    results=json.loads(stored['results']);results[record['input_id']]=result
                    db.execute('UPDATE reprocessing_runs SET results=? WHERE run_id=?',(json.dumps(results),run_id))
                    record.update(state='succeeded',outcome='tasks' if result['tasks'] else 'no_tasks',old_result_retained=record['previous_available'] if protected else False)
                save(db,run,terminal(run))
    except Exception:
        with connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            stored=db.execute('SELECT snapshot FROM reprocessing_runs WHERE user_id=? AND run_id=?',(uid,run_id)).fetchone()
            if stored:
                run=json.loads(stored[0])
                if run['state'] not in contract.FINAL:
                    for record in run['records']:
                        if record['state'] in ('queued','running'):
                            record.update(state='interrupted',error=dict(code='interrupted',message='处理已中断，旧结果未变更'))
                    save(db,run,'stopped' if run['state']=='stopping' else 'interrupted')
    finally:
        with _lock:
            _workers.discard((str(path),threading.current_thread()))


def launch(path, uid, run_id, ApiError):
    worker=threading.Thread(target=process,args=(path,uid,run_id,ApiError),daemon=True)
    with _lock:
        _workers.add((str(path),worker))
        try:
            worker.start()
        except Exception:
            _workers.discard((str(path),worker))
            raise


def wait_for_jobs(path, timeout=100):
    with _lock:
        workers=[worker for db,worker in _workers if db==str(path)]
    for worker in workers:
        worker.join(timeout)
    return not any(worker.is_alive() for worker in workers)


def route(handler, db, uid, path, body, ApiError):
    prefix='/api/recognition/reprocessing/'
    if not path.startswith(prefix):
        return False
    try:
        method=handler.command
        if path==prefix+'sources' and method=='GET':
            cursor=parse_qs(urlsplit(handler.path).query).get('cursor',[''])[0]
            handler.reply(200,catalogue(db,uid,cursor));return True
        if path==prefix+'runs' and method=='POST':
            db.execute('BEGIN IMMEDIATE')
            run,created=start(db,uid,body,ApiError)
            db.commit()
            if created:
                launch(handler.media_db_path,uid,run['run_id'],ApiError)
            handler.reply(202 if created else 200,run);return True
        if path==prefix+'runs' and method=='GET':
            cursor=parse_qs(urlsplit(handler.path).query).get('cursor',['0'])[0]
            offset=int(cursor)
            if offset<0:raise ValueError()
            rows=db.execute('SELECT snapshot FROM reprocessing_runs WHERE user_id=? ORDER BY rowid DESC LIMIT 21 OFFSET ?',(uid,offset)).fetchall()
            handler.reply(200,contract.check('runs',dict(contract_version=contract.VERSION,runs=[json.loads(r[0]) for r in rows[:20]],next_cursor=str(offset+20) if len(rows)>20 else None)));return True
        parts=path[len(prefix):].split('/')
        if len(parts) in (2,3) and parts[0]=='runs':
            if method=='POST':db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT snapshot FROM reprocessing_runs WHERE user_id=? AND run_id=?',(uid,parts[1])).fetchone()
            if not row:raise ApiError(404,'运行不存在')
            run=json.loads(row[0])
            if len(parts)==2 and method=='GET':
                handler.reply(200,run);return True
            if len(parts)==3 and parts[2]=='stop' and method=='POST':
                contract.check('stop',body)
                if run['state'] not in contract.FINAL:
                    for record in run['records']:
                        if record['state']=='queued':record['state']='cancelled'
                    save(db,run,'stopping' if any(r['state']=='running' for r in run['records']) else 'stopped')
                handler.reply(200,run);return True
        raise ApiError(404,'重识别接口不存在')
    except ApiError as error:
        db.rollback()
        handler.reply(error.status,dict(contract_version=contract.VERSION,code={404:'not_found',409:'conflict',503:'unavailable'}.get(error.status,'invalid_request'),message=error.message))
        return True
    except (ValueError,TypeError,KeyError,ValidationError):
        db.rollback()
        handler.reply(400,dict(contract_version=contract.VERSION,code='invalid_request',message='重识别请求不符合契约'))
        return True

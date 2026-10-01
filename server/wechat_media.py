"""Account-scoped v0.3 WeChat asset and asynchronous import transport."""
import hashlib
import io
import hmac
import json
import copy
import secrets
import sqlite3
import threading
import time
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zipfile import ZipFile, is_zipfile

from contracts.check_v03_examples import check_pair, check_request, schema

MAX_ASSET = 20 * 1024 * 1024
_slot = threading.Semaphore(1)


class MediaError(Exception):
    def __init__(self, status, code, message, retryable=False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS wechat_credentials(user_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_assets(ref TEXT PRIMARY KEY, user_id TEXT NOT NULL, file_name TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_jobs(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, result TEXT, previous_result TEXT, retry_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id,request_id));
    ''')
    db.execute("UPDATE wechat_jobs SET status='queued' WHERE status='running'")


def bearer_user(db, headers):
    value = headers.get('Authorization', '')
    if not value.startswith('Bearer '):
        return None
    token = value[7:]
    if not token or len(token) > 256:
        raise MediaError(401, 'unauthorized', '导入凭证无效')
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute('SELECT user_id,token_hash FROM wechat_credentials WHERE token_hash=?', (digest,)).fetchone()
    if not row or not hmac.compare_digest(row['token_hash'], digest):
        raise MediaError(401, 'unauthorized', '导入凭证无效')
    return row['user_id']


def _asset_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'%PDF-'):
        return 'application/pdf'
    if data.startswith(b'PK') and is_zipfile(io.BytesIO(data)):
        with ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
        if '[Content_Types].xml' in names and 'word/document.xml' in names:
            return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    raise MediaError(400, 'invalid_request', '仅支持 JPG、PNG、PDF 或 DOCX 原件')


def _upload(handler, db, uid, data_dir):
    size = int(handler.headers.get('Content-Length', '0'))
    if size <= 0 or size > MAX_ASSET + 8192:
        raise MediaError(413, 'limit_exceeded', '附件不得超过 20 MiB')
    content_type = handler.headers.get('Content-Type', '')
    if not content_type.startswith('multipart/form-data;'):
        raise MediaError(400, 'invalid_request', '请选择附件文件')
    raw = handler.rfile.read(size)
    message = BytesParser(policy=default).parsebytes(b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + raw)
    parts = list(message.iter_parts()) if message.is_multipart() else []
    if len(parts) != 1 or parts[0].get_param('name', header='content-disposition') != 'file':
        raise MediaError(400, 'invalid_request', '仅允许上传一个 file 附件')
    data = parts[0].get_payload(decode=True)
    if not data or len(data) > MAX_ASSET:
        raise MediaError(413, 'limit_exceeded', '附件为空或超过 20 MiB')
    name = Path(parts[0].get_filename() or '').name
    if not name or name in ('.', '..') or len(name) > 255 or '/' in name or '\\' in name:
        raise MediaError(400, 'invalid_request', '文件名无效')
    mime, digest = _asset_type(data), hashlib.sha256(data).hexdigest()
    row = db.execute('SELECT * FROM wechat_assets WHERE user_id=? AND sha256=?', (uid, digest)).fetchone()
    if row:
        status, ref = 200, row['ref']
        name, mime = row['file_name'], row['mime_type']
    else:
        used = db.execute('SELECT COALESCE(SUM(size_bytes),0) FROM wechat_assets WHERE user_id=?', (uid,)).fetchone()[0]
        if used + len(data) > 200 * 1024 * 1024:
            raise MediaError(413, 'limit_exceeded', '当前账号附件总量超过 200 MiB')
        status, ref = 201, 'asset:' + secrets.token_urlsafe(24)
        folder = data_dir / 'wechat-assets' / uid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ref[6:]
        with path.open('xb') as target:
            target.write(data)
        db.execute('INSERT INTO wechat_assets VALUES(?,?,?,?,?,?,?)', (ref, uid, name, mime, len(data), digest, str(path)))
    ack = dict(contract_version='0.3', file_ref=ref, file_name=name, mime_type=mime, size_bytes=len(data), sha256=digest)
    schema('wechat-import', ack, 'asset_ack')
    handler.reply(status, ack)
    return True


def _validate_assets(db, uid, request):
    for record in request['records']:
        for asset in record['attachments']:
            if asset.get('content_mode') != 'file' or asset['status'] != 'available':
                continue
            row = db.execute('SELECT * FROM wechat_assets WHERE ref=? AND user_id=?', (asset['file_ref'], uid)).fetchone()
            if not row or not Path(row['path']).is_file():
                raise MediaError(404, 'not_found', '附件不存在或不属于当前账号')
            if any(asset[key] != row[key] for key in ('file_name', 'mime_type', 'size_bytes', 'sha256')):
                raise MediaError(400, 'invalid_request', '附件回执与原件不一致')


def _fingerprint(request):
    stable = copy.deepcopy(request)
    stable.pop('request_id', None)
    for record in stable['records']:
        record.pop('collected_at', None)
        for asset in record['attachments']:
            if asset.get('file_ref'):
                asset['file_ref'] = asset['sha256']
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _job(row):
    total = len(json.loads(row['request'])['records'])
    result = json.loads(row['result']) if row['result'] else None
    finished = total if row['status'] in ('succeeded', 'partial_failed', 'failed') else 0
    failed = total if row['status'] == 'failed' else sum(r['status'] in ('failed', 'partial_success') for r in result['record_results']) if result else 0
    return dict(contract_version='0.3', job_id=row['id'], request_id=row['request_id'], status=row['status'], progress=dict(total_records=total, finished_records=finished, failed_records=failed), result=result)


def _sources(request):
    sources = {}
    for record in request['records']:
        sources[record['input_id']] = dict(label=record['source_label'], assets={asset['asset_id']:asset['file_name'] for asset in record['attachments']})
        for context in record['context']:
            sources[context['input_id']] = dict(label=context['source_label'], assets={})
    return sources


def recognize_v03(request, assets):
    """#94 supplies the real adapter; never treat unread files as no_tasks."""
    raise NotImplementedError('v0.3 附件读取适配器尚未接入')


def _failed_result(request, code='agent_unavailable', message='附件识别暂不可用；没有将其当作无任务'):
    return dict(contract_version='0.3', request_id=request['request_id'], status='failed', record_results=[], tasks=[], errors=[dict(input_id=None, asset_id=None, block_id=None, stage='request', code=code, message=message, retryable=code=='agent_unavailable')])


def _apply_clear_tasks(db, row, result):
    records = {item['input_id']: item for item in json.loads(row['request'])['records']}
    outcomes = {item['input_id']: item for item in result['record_results']}
    existing = db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"', (row['user_id'],)).fetchone()
    tasks = json.loads(existing['value']) if existing else []
    changed = False
    for candidate in result['tasks']:
        record = records[candidate['input_id']]
        outcome = outcomes[candidate['input_id']]
        deadline = candidate['deadline']
        storage_id = row['id'] + ':' + candidate['candidate_id']
        if (record['platform'] != 'wechat' or outcome['status'] != 'success' or not outcome['coverage']['complete']
                or outcome['coverage']['uncertain'] or candidate['notice_type'] != 'create' or candidate['needs_review']
                or candidate['review_reasons'] or any(storage_id == h.get('storageId') for t in tasks for h in t.get('recognitionHistory', []))):
            continue
        if deadline['status'] == 'not_provided' and deadline['value'] is None:
            due = None
        elif deadline['kind'] == 'deadline' and deadline['status'] == 'explicit' and deadline['precision'] in ('date', 'datetime'):
            if deadline['precision'] == 'date':
                due = dict(date=deadline['value'], precision='date')
            else:
                stamp = datetime.fromisoformat(deadline['value'].replace('Z', '+00:00'))
                if stamp.second or stamp.microsecond: continue
                local = stamp.astimezone(timezone(timedelta(hours=8)))
                due = dict(date=local.date().isoformat(), precision='datetime', time=local.strftime('%H:%M'))
        else:
            continue
        tasks.append(dict(id=secrets.token_hex(16), title=candidate['title'], description=candidate['description'],
                          category='课程作业' if candidate['course_or_project'] else '个人事务', course=candidate['course_or_project'],
                          priority='medium', status='not_started', source='微信', due=due, subtasks=[], plans=[],
                          createdAt=datetime.now(timezone.utc).isoformat(),
                          recognitionHistory=[dict(storageId=storage_id, requestId=row['request_id'], candidateId=candidate['candidate_id'], action='create')]))
        changed = True
    if changed:
        db.execute('INSERT INTO workspace(user_id,name,value,version) VALUES(?,?,?,1) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=workspace.version+1', (row['user_id'], 'tasks', json.dumps(tasks, ensure_ascii=False)))


def _process(db_path, job_id):
    with _slot:
        db = sqlite3.connect(db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute('SELECT * FROM wechat_jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['status'] != 'queued':
                return
            request = json.loads(row['request'])
            previous = json.loads(row['previous_result']) if row['previous_result'] else None
            done = {item['input_id'] for item in previous['record_results'] if item['status'] in ('success','no_tasks')} if previous else set()
            extraction = dict(request, records=[item for item in request['records'] if item['input_id'] not in done])
            db.execute("UPDATE wechat_jobs SET status='running' WHERE id=?", (job_id,))
            db.commit()
            try:
                assets = {a['file_ref']: Path(db.execute('SELECT path FROM wechat_assets WHERE ref=? AND user_id=?', (a['file_ref'], row['user_id'])).fetchone()['path']) for r in request['records'] for a in r['attachments'] if a.get('file_ref')}
                result = recognize_v03(extraction, assets)
                check_pair(extraction, result)
                if previous and result['record_results']:
                    outcomes = {item['input_id']:item for item in previous['record_results'] if item['input_id'] in done}
                    outcomes.update({item['input_id']:item for item in result['record_results']})
                    prior = {item['candidate_id']: item for item in previous['tasks']}
                    tasks = list(prior.values()) + [item for item in result['tasks'] if item['candidate_id'] not in prior]
                    for outcome in outcomes.values():
                        outcome['task_ids'] = [item['candidate_id'] for item in tasks if item['input_id'] == outcome['input_id']]
                    errors = [item for item in previous['errors'] if item['input_id'] in done] + result['errors']
                    failures = sum(item['status']=='failed' for item in outcomes.values())
                    partial = any(item['status']=='partial_success' for item in outcomes.values())
                    status = ('failed' if failures==len(request['records']) else 'partial_success') if failures else 'partial_success' if partial else 'success' if tasks else 'no_tasks'
                    result = dict(contract_version='0.3', request_id=request['request_id'], status=status,
                                  record_results=[outcomes[item['input_id']] for item in request['records']], tasks=tasks, errors=errors)
                    check_pair(request, result)
            except NotImplementedError:
                result = previous if previous else _failed_result(request, message='附件读取适配器尚未接入；没有将其当作无任务')
            except (ValueError, KeyError, TypeError):
                result = previous if previous else _failed_result(request, 'invalid_agent_output', '识别结果未通过 v0.3 校验；任务未新增')
            except Exception:
                result = previous if previous else _failed_result(request)
            status = {'success':'succeeded', 'no_tasks':'succeeded', 'partial_success':'partial_failed', 'failed':'failed'}[result['status']]
            try:
                db.execute('BEGIN IMMEDIATE')
                if status in ('succeeded', 'partial_failed'):
                    _apply_clear_tasks(db, row, result)
                db.execute('UPDATE wechat_jobs SET status=?,result=?,previous_result=NULL WHERE id=?', (status, json.dumps(result, ensure_ascii=False), job_id))
                db.commit()
            except Exception:
                db.rollback()
                failure = previous if previous else _failed_result(request)
                db.execute('UPDATE wechat_jobs SET status=?,result=?,previous_result=NULL WHERE id=?',
                           ('partial_failed' if previous else 'failed', json.dumps(failure, ensure_ascii=False), job_id))
                db.commit()
        finally:
            db.close()


def resume(db_path):
    db = sqlite3.connect(db_path)
    try:
        ids = [row[0] for row in db.execute("SELECT id FROM wechat_jobs WHERE status='queued'")]
    finally:
        db.close()
    for job_id in ids:
        threading.Thread(target=_process, args=(db_path, job_id), daemon=True).start()


def route(handler, db, uid, path, body, data_dir):
    if not path.startswith('/api/sources/wechat/'):
        return False
    if path == '/api/sources/wechat/credential':
        if handler.headers.get('Authorization'):
            raise MediaError(403, 'forbidden', '请在登录页面管理导入凭证')
        row = db.execute('SELECT 1 FROM wechat_credentials WHERE user_id=?', (uid,)).fetchone()
        if handler.command == 'GET':
            handler.reply(200, dict(configured=bool(row)))
        elif handler.command == 'POST':
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO wechat_credentials VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash,created_at=excluded.created_at', (uid, hashlib.sha256(token.encode()).hexdigest(), time.time()))
            handler.reply(200, dict(configured=True, token=token))
        else:
            raise MediaError(405, 'invalid_request', '请求方式不支持')
        return True
    if path == '/api/sources/wechat/credential/revoke' and handler.command == 'POST':
        if handler.headers.get('Authorization'):
            raise MediaError(403, 'forbidden', '请在登录页面管理导入凭证')
        db.execute('DELETE FROM wechat_credentials WHERE user_id=?', (uid,))
        handler.reply(200, dict(configured=False))
        return True
    if path == '/api/sources/wechat/assets' and handler.command == 'POST':
        return _upload(handler, db, uid, data_dir)
    if path == '/api/sources/wechat/imports' and handler.command == 'GET':
        rows = db.execute('SELECT * FROM wechat_jobs WHERE user_id=? ORDER BY rowid DESC LIMIT 20', (uid,)).fetchall()
        handler.reply(200, dict(jobs=[dict(_job(row), sources=_sources(json.loads(row['request']))) for row in rows]))
        return True
    if path == '/api/sources/wechat/imports' and handler.command == 'POST':
        if body.get('contract_version') != '0.3':
            raise MediaError(400, 'unsupported_version', '仅支持微信附件 v0.3 导入')
        try:
            check_request(body)
        except Exception:
            raise MediaError(400, 'invalid_request', 'v0.3 导入请求格式不正确') from None
        _validate_assets(db, uid, body)
        fingerprint = _fingerprint(body)
        row = db.execute('SELECT * FROM wechat_jobs WHERE user_id=? AND request_id=?', (uid, body['request_id'])).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise MediaError(409, 'request_conflict', '同一 request_id 对应不同内容')
            handler.reply(200, dict(contract_version='0.3', job_id=row['id'], request_id=row['request_id'], status=row['status']))
            return True
        row = db.execute('SELECT * FROM wechat_jobs WHERE user_id=? AND fingerprint=? ORDER BY rowid DESC LIMIT 1', (uid, fingerprint)).fetchone()
        if row:
            handler.reply(200, dict(contract_version='0.3', job_id=row['id'], request_id=row['request_id'], status=row['status']))
            return True
        if db.execute("SELECT COUNT(*) FROM wechat_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
            raise MediaError(503, 'queue_busy', '导入队列已满，请稍后重试', True)
        job_id = 'job-' + secrets.token_urlsafe(18)
        db.execute('INSERT INTO wechat_jobs(id,user_id,request_id,fingerprint,request,status,result,previous_result,retry_id,attempts) VALUES(?,?,?,?,?,?,?,?,?,0)', (job_id, uid, body['request_id'], fingerprint, json.dumps(body, ensure_ascii=False), 'queued', None, None, None))
        handler.reply(202, dict(contract_version='0.3', job_id=job_id, request_id=body['request_id'], status='queued'))
        threading.Thread(target=_process, args=(handler.media_db_path, job_id), daemon=True).start()
        return True
    parts = path[len('/api/sources/wechat/imports/'):].split('/') if path.startswith('/api/sources/wechat/imports/') else []
    if parts:
        row = db.execute('SELECT * FROM wechat_jobs WHERE id=? AND user_id=?', (parts[0], uid)).fetchone()
        if not row:
            raise MediaError(404, 'not_found', '导入任务不存在')
        if len(parts) == 1 and handler.command == 'GET':
            result = _job(row)
            schema('wechat-import', result, 'job')
            handler.reply(200, result)
            return True
        if len(parts) == 2 and parts[1] == 'retry' and handler.command == 'POST':
            retry_id = body.get('retry_id')
            try:
                schema('wechat-import', body, 'retry')
            except Exception:
                raise MediaError(400, 'invalid_request', '重试编号无效') from None
            if row['retry_id'] == retry_id:
                handler.reply(202, dict(contract_version='0.3', job_id=row['id'], request_id=row['request_id'], status=row['status']))
                return True
            previous = json.loads(row['result']) if row['result'] else None
            if (row['status'] not in ('failed','partial_failed') or row['attempts'] >= 3 or not previous
                    or not any(error['retryable'] and error['stage'] in ('recognize','request') for error in previous['errors'])):
                raise MediaError(409, 'invalid_job_state', '当前任务不能重试')
            db.execute("UPDATE wechat_jobs SET status='queued',result=NULL,previous_result=?,retry_id=?,attempts=attempts+1 WHERE id=?", (row['result'], retry_id, row['id']))
            handler.reply(202, dict(contract_version='0.3', job_id=row['id'], request_id=row['request_id'], status='queued'))
            threading.Thread(target=_process, args=(handler.media_db_path, row['id']), daemon=True).start()
            return True
    raise MediaError(404, 'not_found', '接口不存在')

"""Account-scoped v0.3.1 WeChat asset and asynchronous import transport."""
import hashlib
import copy
import io
import hmac
import json
import math
import os
if __package__:
    from .runtime_environment import model_env_path
else:
    from runtime_environment import model_env_path
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing
from importlib.metadata import PackageNotFoundError, version
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zipfile import ZipFile, is_zipfile
from urllib.parse import unquote
from jsonschema import ValidationError

from contracts.check_v031_examples import check_pair, check_request, check_job, check_retry_result, schema, request_fingerprint, retryable_records, check_asset_metadata
from server.model_queue import CONCURRENCY, QUEUE_LIMIT

MAX_ASSET = 20 * 1024 * 1024
BODY_SECONDS = 30
_slot = threading.Semaphore(CONCURRENCY)
MAX_JOBS = CONCURRENCY + QUEUE_LIMIT
_workers = set()
_workers_lock = threading.Lock()


def _start_job(db_path, job_id):
    def run():
        try:
            _process(db_path, job_id)
        finally:
            with _workers_lock:
                _workers.discard((db_path, thread))
    thread = threading.Thread(target=run, daemon=True)
    with _workers_lock:
        _workers.add((db_path, thread))
        try:
            thread.start()
        except Exception:
            _workers.discard((db_path, thread))
            raise


def wait_for_jobs(db_path, timeout=30):
    """Drain this database's workers before closing its server or test fixture."""
    expires = time.monotonic() + timeout
    while True:
        with _workers_lock:
            threads = [thread for path, thread in _workers if path == db_path]
        if not threads:
            return True
        remaining = expires - time.monotonic()
        if remaining <= 0:
            return False
        threads[0].join(remaining)


class MediaError(Exception):
    def __init__(self, status, code, message, retryable=False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def read_body(handler, maximum):
    lengths = handler.headers.get_all('Content-Length', [])
    if handler.headers.get('Transfer-Encoding') or len(lengths)>1 or (lengths and not lengths[0].isascii()) or (lengths and not lengths[0].isdigit()):
        raise MediaError(400,'invalid_request','请求长度无效')
    if lengths and len(lengths[0])>10:
        raise MediaError(413,'limit_exceeded','请求超过大小限制')
    size = int(lengths[0]) if lengths else 0
    if size > maximum:
        raise MediaError(413,'limit_exceeded','请求超过大小限制')
    expires, chunks, remaining = time.monotonic()+BODY_SECONDS, [], size
    timeout = handler.connection.gettimeout()
    try:
        while remaining:
            budget = expires-time.monotonic()
            if budget<=0:
                raise TimeoutError()
            handler.connection.settimeout(budget)
            chunk = handler.rfile.read1(min(65536,remaining))
            if not chunk:
                raise MediaError(400,'invalid_request','请求内容未完整传输')
            chunks.append(chunk);remaining-=len(chunk)
    except (TimeoutError,OSError):
        raise MediaError(400,'invalid_request','请求传输未完成，请重新发送') from None
    finally:
        handler.connection.settimeout(timeout)
    return b''.join(chunks)


def initialize(db, recover=True):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS wechat_credentials(user_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_assets(ref TEXT PRIMARY KEY, user_id TEXT NOT NULL, file_name TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_jobs(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, result TEXT, previous_result TEXT, retry_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id,request_id));
    CREATE TABLE IF NOT EXISTS wechat_retries(job_id TEXT NOT NULL, retry_id TEXT NOT NULL, targets TEXT NOT NULL, status TEXT NOT NULL, errors TEXT, PRIMARY KEY(job_id,retry_id));
    CREATE TABLE IF NOT EXISTS wechat_asset_lifetime(ref TEXT PRIMARY KEY, uploaded_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_job_assets(job_id TEXT NOT NULL, ref TEXT NOT NULL, PRIMARY KEY(job_id,ref));
    CREATE TABLE IF NOT EXISTS wechat_content_cache(user_id TEXT NOT NULL, content_key TEXT NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY(user_id,content_key));
    CREATE TABLE IF NOT EXISTS wechat_job_origins(job_id TEXT PRIMARY KEY, origin_job_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_pending_deletes(ref TEXT PRIMARY KEY, user_id TEXT NOT NULL, path TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_revoked_assets(ref TEXT PRIMARY KEY, user_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wechat_record_runs(job_id TEXT NOT NULL, retry_id TEXT NOT NULL, input_id TEXT NOT NULL, result TEXT, PRIMARY KEY(job_id,retry_id,input_id));
    ''')
    if 'errors' not in {row[1] for row in db.execute('PRAGMA table_info(wechat_retries)')}:
        db.execute('ALTER TABLE wechat_retries ADD COLUMN errors TEXT')
    if recover:
        db.execute("UPDATE wechat_jobs SET status='queued' WHERE status='running'")
    db.execute('INSERT OR IGNORE INTO wechat_asset_lifetime SELECT ref,? FROM wechat_assets',(time.time(),))
    for row in db.execute('SELECT id,request FROM wechat_jobs'):
        _retain_assets(db,row['id'],json.loads(row['request']))
    orphaned = {row[0] for row in db.execute('''SELECT user_id FROM wechat_assets WHERE user_id NOT IN(SELECT id FROM users)
        UNION SELECT user_id FROM wechat_jobs WHERE user_id NOT IN(SELECT id FROM users)
        UNION SELECT user_id FROM wechat_credentials WHERE user_id NOT IN(SELECT id FROM users)
        UNION SELECT user_id FROM wechat_revoked_assets WHERE user_id NOT IN(SELECT id FROM users)''')}
    for uid in orphaned:
        remove_source(db,uid)
    if recover:
        _recover_orphan_files(db)
    _purge_deleted_files(db)
    _expire_unreferenced(db)


def _recover_orphan_files(db):
    """Recover managed files left between writing bytes and committing a receipt."""
    root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent.resolve()/'wechat-assets'
    if not root.is_dir() or root.is_symlink():
        return
    # initialize already holds SQLite's write lock, excluding active uploads.
    with os.scandir(root) as folders:
        for folder in folders:
            if not re.fullmatch(r'[0-9a-f]{32}',folder.name) or not folder.is_dir(follow_symlinks=False):
                continue
            with os.scandir(folder.path) as files:
                for entry in files:
                    if not re.fullmatch(r'[A-Za-z0-9_-]{32}',entry.name) or not entry.is_file(follow_symlinks=False):
                        continue
                    ref = 'asset:'+entry.name
                    if db.execute('SELECT 1 FROM wechat_assets WHERE ref=? AND user_id=?',(ref,folder.name)).fetchone():
                        continue
                    db.execute('INSERT OR IGNORE INTO wechat_pending_deletes VALUES(?,?,?)',(ref,folder.name,entry.path))


def _purge_deleted_files(db):
    root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent.resolve()
    for asset in db.execute('SELECT * FROM wechat_pending_deletes').fetchall():
        path = Path(asset['path'])
        if path.parent.resolve() != root/'wechat-assets'/asset['user_id']:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        db.execute('DELETE FROM wechat_pending_deletes WHERE ref=?',(asset['ref'],))


def remove_source(db, uid):
    """Invalidate handles and jobs before cleanup; preserve user-owned tasks."""
    db.execute('INSERT OR IGNORE INTO wechat_pending_deletes SELECT ref,user_id,path FROM wechat_assets WHERE user_id=?',(uid,))
    db.execute('INSERT OR IGNORE INTO wechat_revoked_assets SELECT ref,user_id FROM wechat_assets WHERE user_id=?',(uid,))
    for table in ('recognition_candidates','recognition_batches'):
        db.execute(f'DELETE FROM {table} WHERE user_id=? AND request_id IN(SELECT request_id FROM wechat_jobs WHERE user_id=?)',(uid,uid))
    for table in ('wechat_retries','wechat_job_assets','wechat_job_origins','wechat_record_runs'):
        db.execute(f'DELETE FROM {table} WHERE job_id IN(SELECT id FROM wechat_jobs WHERE user_id=?)',(uid,))
    db.execute('DELETE FROM wechat_asset_lifetime WHERE ref IN(SELECT ref FROM wechat_assets WHERE user_id=?)',(uid,))
    for table in ('wechat_content_cache','wechat_assets','wechat_credentials','wechat_jobs'):
        db.execute(f'DELETE FROM {table} WHERE user_id=?',(uid,))
    if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
        db.execute('DELETE FROM wechat_revoked_assets WHERE user_id=?',(uid,))
    _purge_deleted_files(db)


def _retain_assets(db, job_id, request):
    for record in request['records']:
        for asset in record['attachments']:
            if asset.get('file_ref'):
                db.execute('INSERT OR IGNORE INTO wechat_job_assets VALUES(?,?)',(job_id,asset['file_ref']))


def _asset_ttl():
    ttl = float(os.environ.get('WECHAT_UNREFERENCED_TTL_SECONDS','86400'))
    if not math.isfinite(ttl) or ttl < 300:
        raise ValueError('invalid_asset_ttl')
    return ttl


def _expire_unreferenced(db, now=None):
    _purge_deleted_files(db)
    expired = db.execute('''SELECT a.* FROM wechat_assets a JOIN wechat_asset_lifetime l ON l.ref=a.ref
        WHERE l.uploaded_at<? AND NOT EXISTS(SELECT 1 FROM wechat_job_assets j WHERE j.ref=a.ref)''',
        ((time.time() if now is None else now)-_asset_ttl(),)).fetchall()
    root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent.resolve()
    for asset in expired:
        path = Path(asset['path'])
        # The recorded leaf is removed, never a directory or an input-provided path.
        if path.parent.resolve() != root / 'wechat-assets' / asset['user_id']:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        db.execute('DELETE FROM wechat_assets WHERE ref=?',(asset['ref'],))
        db.execute('DELETE FROM wechat_asset_lifetime WHERE ref=?',(asset['ref'],))
        db.execute('INSERT OR IGNORE INTO wechat_revoked_assets VALUES(?,?)',(asset['ref'],asset['user_id']))


def bearer_user(db, headers):
    value = headers.get('Authorization', '')
    if not value.startswith('Bearer '):
        return None
    token = value[7:]
    if not token or len(token) > 256:
        raise MediaError(401, 'unauthorized', '导入凭证无效')
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute('SELECT user_id,token_hash FROM wechat_credentials c JOIN users u ON u.id=c.user_id WHERE token_hash=?', (digest,)).fetchone()
    if not row or not hmac.compare_digest(row['token_hash'], digest):
        raise MediaError(401, 'unauthorized', '导入凭证无效')
    return row['user_id']


def _asset_type(data,allow_unsupported=False):
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
    if allow_unsupported:return 'application/octet-stream'
    raise MediaError(400, 'invalid_request', '仅支持 JPG、PNG、PDF 或 DOCX 原件')


def _pdf_encrypted(data):
    import pypdfium2 as pdfium
    try:
        with pdfium.PdfDocument(data) as document:
            return pdfium.raw.FPDF_GetSecurityHandlerRevision(document)!=-1
    except pdfium.PdfiumError as error:
        return error.err_code==pdfium.raw.FPDF_ERR_PASSWORD


def _reject_encrypted(data):
    encrypted=data.startswith((b'-----BEGIN PGP MESSAGE-----',b'age-encryption.org/v1',b'Salted__'))
    if data.startswith(b'%PDF-'):
        from ddl_text.media_worker import bounded
        encrypted=bounded(_pdf_encrypted,(data,),10)
    elif data.startswith(b'PK') and is_zipfile(io.BytesIO(data)):
        with ZipFile(io.BytesIO(data)) as archive:
            encrypted=any(part.flag_bits&1 for part in archive.infolist())
    elif data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        encrypted=all(name.encode('utf-16le') in data for name in ('EncryptedPackage','EncryptionInfo'))
    else:
        try:
            value=json.loads(data)
            encrypted=encrypted or isinstance(value,dict) and (value.get('encrypted') is True or value.get('is_encrypted') is True)
        except (ValueError,UnicodeError,RecursionError):pass
    if encrypted:raise MediaError(400,'invalid_request','不接受加密数据，请自行合法导出未加密的材料；本站不提供解密功能')


def _upload(handler, db, uid, data_dir):
    content_type = handler.headers.get('Content-Type', '')
    if not content_type.startswith('multipart/form-data;'):
        raise MediaError(400, 'invalid_request', '请选择附件文件')
    raw = read_body(handler,MAX_ASSET+8192)
    message = BytesParser(policy=default).parsebytes(b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + raw)
    parts = list(message.iter_parts()) if message.is_multipart() else []
    if (message.defects or len(parts) != 1 or parts[0].defects or parts[0].is_multipart()
            or parts[0].get_content_disposition() != 'form-data'
            or parts[0].get('Content-Transfer-Encoding','binary').lower() not in ('binary','8bit')
            or parts[0].get_param('name',header='content-disposition') != 'file'
            or parts[0].get_filename() is None):
        raise MediaError(400, 'invalid_request', '仅允许上传一个 file 附件')
    data = parts[0].get_payload(decode=True)
    if not data or len(data) > MAX_ASSET:
        raise MediaError(413, 'limit_exceeded', '附件为空或超过 20 MiB')
    name = parts[0].get_filename() or ''
    if not name or name in ('.', '..') or len(name) > 255 or '/' in name or '\\' in name:
        raise MediaError(400, 'invalid_request', '文件名无效')
    manual=getattr(handler,'user_materials',False)
    if manual:_reject_encrypted(data)
    mime, digest = _asset_type(data,allow_unsupported=manual), hashlib.sha256(data).hexdigest()
    try:
        from contracts.check_v031_examples import check_file_name
        check_file_name(name)
    except Exception:
        raise MediaError(400, 'invalid_request', '文件名无效') from None
    db.execute('BEGIN IMMEDIATE')
    _expire_unreferenced(db)
    row = db.execute('SELECT * FROM wechat_assets WHERE user_id=? AND sha256=?', (uid, digest)).fetchone()
    if row:
        try:
            account_asset_loader(handler.media_db_path,uid)(row['ref'],max_bytes=MAX_ASSET,timeout_seconds=10)
        except (OSError,ValueError):
            db.execute('INSERT OR IGNORE INTO wechat_revoked_assets VALUES(?,?)',(row['ref'],uid))
            db.execute('INSERT OR IGNORE INTO wechat_pending_deletes VALUES(?,?,?)',(row['ref'],uid,row['path']))
            db.execute('DELETE FROM wechat_assets WHERE ref=?',(row['ref'],))
            db.execute('DELETE FROM wechat_asset_lifetime WHERE ref=?',(row['ref'],))
            _purge_deleted_files(db)
            row=None
    if row:
        status, ref = 200, row['ref']
        name, mime = row['file_name'], row['mime_type']
    else:
        used = db.execute('SELECT COALESCE(SUM(size_bytes),0) FROM wechat_assets WHERE user_id=?', (uid,)).fetchone()[0]
        for pending in db.execute('SELECT path FROM wechat_pending_deletes WHERE user_id=?',(uid,)):
            pending_path=Path(pending['path'])
            try:
                expected=data_dir.resolve()/'wechat-assets'/uid
                used += pending_path.lstat().st_size if pending_path.parent.resolve()==expected and not pending_path.is_symlink() else MAX_ASSET
            except OSError:
                used += MAX_ASSET
        if used + len(data) > 200 * 1024 * 1024:
            raise MediaError(413, 'limit_exceeded', '当前账号附件总量超过 200 MiB')
        status, ref = 201, 'asset:' + secrets.token_urlsafe(24)
        folder = data_dir / 'wechat-assets' / uid
        folder.mkdir(mode=0o700,parents=True, exist_ok=True)
        if folder.resolve()!=data_dir.resolve()/'wechat-assets'/uid:
            raise MediaError(503,'queue_busy','附件存储目录不可用')
        path = folder / ref[6:]
        with path.open('xb') as target:
            if os.name == 'posix':
                os.fchmod(target.fileno(),0o600)
            target.write(data)
        try:
            db.execute('INSERT INTO wechat_assets VALUES(?,?,?,?,?,?,?)', (ref, uid, name, mime, len(data), digest, str(path)))
            db.execute('INSERT INTO wechat_asset_lifetime VALUES(?,?)',(ref,time.time()))
        except Exception:
            path.unlink()
            raise
    ack = dict(contract_version='manual-materials/0.1' if manual else '0.3.1', file_ref=ref, file_name=name, mime_type=mime, size_bytes=len(data), sha256=digest)
    if not manual:schema('wechat-import', ack, 'asset_ack')
    handler.reply(status, ack)
    return True


def _validate_assets(db, uid, request):
    _expire_unreferenced(db)
    for record in request['records']:
        for asset in record['attachments']:
            if asset.get('content_mode') != 'file' or asset['status'] != 'available':
                continue
            row = db.execute('SELECT * FROM wechat_assets WHERE ref=? AND user_id=?', (asset['file_ref'], uid)).fetchone()
            if not row or not Path(row['path']).is_file():
                raise MediaError(404, 'not_found', '附件不存在或不属于当前账号')
            receipt = dict(contract_version='0.3.1', file_ref=row['ref'], **{key:row[key] for key in ('file_name','mime_type','size_bytes','sha256')})
            try:
                check_asset_metadata(asset, receipt)
            except Exception:
                raise MediaError(400, 'asset_metadata_mismatch', '附件回执与原件不一致') from None


def _fingerprint(request):
    return request_fingerprint(request)


def _content_key(request):
    """Semantic content is separate from the immutable request identity."""
    from recognition import ROOT
    from ddl_text.codex_client import live_options
    try:
        options = live_options(model_env_path(ROOT))
        configuration = {key:options[key] for key in ('provider','model','endpoint')}
    except (OSError,ValueError,KeyError,TypeError):
        return None
    files = ['ddl_text/media_pipeline.py','ddl_text/image_reader.py','ddl_text/media_schema.py',
             'ddl_text/media_worker.py','ddl_text/skills/image-task-extraction/SKILL.md',
             'ddl_text/time_validation.py','ddl_text/chat_client.py','ddl_text/config.py','server/wechat_media.py',
             'contracts/check_v031_examples.py','contracts/check_v03_examples.py',
             'contracts/schemas/bundle-v0.3.1.schema.json']
    if (ROOT/'ddl_text/pdf_reader.py').is_file():
        files.append('ddl_text/pdf_reader.py')
        try:
            from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO
        except ImportError:
            return None
        configuration['pdf_runtime'] = [str(PYPDFIUM_INFO),str(PDFIUM_INFO)]
    if (ROOT/'ddl_text/docx_reader.py').is_file():
        files.append('ddl_text/docx_reader.py')
        try:
            configuration['docx_runtime'] = [version('defusedxml'), version('Pillow')]
        except PackageNotFoundError:
            return None
    policy = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    content = copy.deepcopy(request)
    del content['request_id']
    for record in content['records']:
        record.pop('collected_at',None)
        for context in record['context']:
            context.pop('collected_at',None)
        for asset in record['attachments']:
            asset.pop('file_ref',None)
    encoded = json.dumps(dict(version=1,content=content,configuration=configuration,policy=policy),sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _origin_job(db, job_id):
    origin = db.execute('SELECT origin_job_id FROM wechat_job_origins WHERE job_id=?',(job_id,)).fetchone()
    return origin['origin_job_id'] if origin else job_id


def _job(row, db=None):
    total = len(json.loads(row['request'])['records'])
    result = json.loads(row['result']) if row['result'] else None
    finished = total if row['status'] in ('succeeded', 'partial_failed', 'failed') else 0
    failed = total if row['status'] == 'failed' else sum(r['status'] in ('failed', 'partial_success') for r in result['record_results']) if result else 0
    if row['previous_result'] and row['status'] in ('queued','running'):
        previous = json.loads(row['previous_result'])
        attempt = db.execute('SELECT targets FROM wechat_retries WHERE job_id=? AND retry_id=?', (row['id'],row['retry_id'])).fetchone() if db else None
        targets = set(json.loads(attempt['targets'])) if attempt else retryable_records(previous)
        finished = total - len(targets)
        failed = sum(r['status'] in ('failed','partial_success') and r['input_id'] not in targets for r in previous['record_results'])
    if db and row['status'] in ('queued','running'):
        completed = [json.loads(item['result']) for item in db.execute(
            'SELECT result FROM wechat_record_runs WHERE job_id=? AND retry_id=? AND result IS NOT NULL',
            (row['id'],row['retry_id'] or ''))]
        finished += len(completed)
        failed += sum(item['status']=='failed' or item['status']=='partial_success' for part in completed for item in part['record_results'])
    return dict(contract_version='0.3.1', job_id=row['id'], request_id=row['request_id'], status=row['status'], progress=dict(total_records=total, finished_records=finished, failed_records=failed), result=result)


def _sources(request):
    sources = {}
    for record in request['records']:
        sources[record['input_id']] = dict(label=record['source_label'], assets={asset['asset_id']:asset['file_name'] for asset in record['attachments']})
        for context in record['context']:
            sources.setdefault(context['input_id'],dict(label=context['source_label'],assets={}))
    return sources


def account_asset_loader(db_path, uid):
    """Resolve opaque handles afresh; never give a model a filesystem path."""
    def load(file_ref, *, max_bytes, timeout_seconds):
        if type(max_bytes) is not int or max_bytes <= 0 or not isinstance(timeout_seconds,(int,float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('invalid_read_budget')
        expires = time.monotonic() + timeout_seconds
        with closing(sqlite3.connect(db_path, timeout=min(10, timeout_seconds))) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT a.* FROM wechat_assets a JOIN users u ON u.id=a.user_id WHERE a.ref=? AND a.user_id=?', (file_ref,uid)).fetchone()
            if not row:
                raise FileNotFoundError('file_ref_unavailable')
            lifetime = db.execute('SELECT uploaded_at FROM wechat_asset_lifetime WHERE ref=?',(file_ref,)).fetchone()
            retained = db.execute('SELECT 1 FROM wechat_job_assets WHERE ref=?',(file_ref,)).fetchone()
            if not retained and (not lifetime or lifetime['uploaded_at'] < time.time()-_asset_ttl()):
                raise FileNotFoundError('file_ref_unavailable')
            path = Path(row['path'])
            folder = Path(db_path).parent / 'wechat-assets' / uid
            expected = Path(db_path).parent.resolve() / 'wechat-assets' / uid
            if folder.resolve() != expected or path.parent.resolve() != expected or path.is_symlink():
                raise FileNotFoundError('file_ref_unavailable')
            cap = min(max_bytes, MAX_ASSET)
            if row['size_bytes'] > cap:
                raise ValueError('limit_exceeded')
            flags = os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0)
            try:
                descriptor = os.open(path, flags)
            except OSError:
                raise FileNotFoundError('file_ref_unavailable') from None
            with os.fdopen(descriptor,'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != row['size_bytes']:
                    raise ValueError('asset_metadata_mismatch')
                if (info.st_dev,info.st_ino) != (path.stat().st_dev,path.stat().st_ino):
                    raise FileNotFoundError('file_ref_unavailable')
                chunks, size = [], 0
                while True:
                    if time.monotonic() >= expires:
                        raise TimeoutError('file_read_failed')
                    chunk = source.read(min(65536, cap + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk); size += len(chunk)
                    if size > cap:
                        raise ValueError('limit_exceeded')
            data = b''.join(chunks)
            if len(data) != row['size_bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('asset_metadata_mismatch')
            if not db.execute('SELECT 1 FROM wechat_assets WHERE ref=? AND user_id=?', (file_ref,uid)).fetchone():
                raise FileNotFoundError('file_ref_unavailable')
            if time.monotonic() >= expires:
                raise TimeoutError('file_read_failed')
            return data
    return load


def _interrupted_record(request, record, message=None, code='agent_unavailable'):
    from contracts.check_v03_examples import coverage
    summaries, errors = [], []
    for asset in record['attachments']:
        errors.extend(dict(input_id=record['input_id'],asset_id=asset['asset_id'],block_id=None,**error) for error in asset['errors'])
        if asset.get('content_mode') == 'file':
            summaries.append(dict(asset_id=asset['asset_id'],content_mode='file',status='failed' if asset['status']=='available' else asset['status'],
                reader_version='server-unavailable-v1' if message else 'server-interrupted-v1',coverage_basis='unverified',
                coverage=dict(complete=False,unread_locations=[message or '处理进程中断，读取结果未保存'],truncated=False),
                quality='normal',quality_notes=[]))
            if not any(error['asset_id']==asset['asset_id'] and error['stage'] in ('collect','upload','read') for error in errors):
                errors.append(dict(input_id=record['input_id'],asset_id=asset['asset_id'],block_id=None,stage='read',
                    code='file_ref_unavailable' if code=='file_ref_unavailable' else 'file_read_failed',
                    message=message or '处理进程中断，读取结果未保存。',retryable=asset['status']=='available' and code not in ('file_ref_unavailable','limit_exceeded')))
        else:
            summaries.append(dict(asset_id=asset['asset_id'],status=asset['status'],block_count=len(asset['blocks']),
                uncertain_block_ids=[block['block_id'] for block in asset['blocks'] if block['quality']=='uncertain']))
    cov = coverage(record,summaries)
    cov['complete'] = False
    outcome = dict(input_id=record['input_id'],status='failed',task_ids=[],warnings=[message or '处理进程中断，请重试此记录。'],coverage=cov,attachments=summaries)
    error = dict(input_id=record['input_id'],asset_id=None,block_id=None,stage='recognize',code=code,
        message=message or '处理进程中断，未自动重复模型请求；可定向重试。',retryable=code=='agent_unavailable')
    result = dict(contract_version='0.3.1',request_id=request['request_id'],status='failed',record_results=[outcome],tasks=[],errors=errors+[error])
    check_pair(dict(request,records=[record]),result)
    return result


def _combine_records(request, parts):
    outcomes = [item for part in parts for item in part['record_results']]
    tasks = [item for part in parts for item in part['tasks']]
    statuses = {item['status'] for item in outcomes}
    status = ('failed' if statuses=={'failed'} else 'partial_success') if 'failed' in statuses else (
        'partial_success' if 'partial_success' in statuses else 'success' if tasks else 'no_tasks')
    result = dict(contract_version='0.3.1',request_id=request['request_id'],status=status,record_results=outcomes,
        tasks=tasks,errors=[item for part in parts for item in part['errors']])
    check_pair(request,result)
    return result


def recognize_v03(request, assets, *, checkpoint=None, completed=None):
    """Use the shared image adapter with a trusted, account-bound loader."""
    from recognition import ROOT
    from server.model_queue import slot
    from ddl_text.codex_client import live_options
    from ddl_text.chat_client import ChatClient
    from ddl_text.config import Settings
    from ddl_text.media_pipeline import process_media_request, MediaLimits
    if checkpoint is not None and completed and all(completed.get(record['input_id']) is not None for record in request['records']):
        return _combine_records(request,[completed[record['input_id']] for record in request['records']])
    with slot(lambda status,message: MediaError(status,'queue_busy',message,True)):
        try:
            options = live_options(model_env_path(ROOT))
            # The image adapter requires a multimodal Chat Completions provider.
            if options['provider'] != 'qiniu' or not options['key'].strip():
                raise ValueError('image_provider_unavailable')
        except (OSError,ValueError,KeyError,TypeError):
            if checkpoint is not None:
                parts = []
                for record in request['records']:
                    part = (completed or {}).get(record['input_id'])
                    if part is None:
                        part = _interrupted_record(request,record,'模型配置不可用；已保存的记录保留，请配置后重试此记录。')
                        checkpoint(record,part)
                    parts.append(part)
                return _combine_records(request,parts)
            raise MediaError(503,'agent_unavailable','图片识别配置不可用，请检查模型配置') from None
        settings = Settings(mode='real',model=options['model'],endpoint=options['endpoint'],timeout_seconds=45,max_retries=0)
        if checkpoint is None:
            return process_media_request(request,assets,ChatClient(options['key']),settings)
        check_request(request)
        potential_errors = 3 * len(request['records']) + sum(
            len(asset['errors']) + (2 if asset.get('content_mode')=='file' and asset['status']=='available' else 0)
            for record in request['records'] for asset in record['attachments'])
        if potential_errors > 200:
            raise MediaError(413,'limit_exceeded','本次处理记录过多，请拆分导入')
        saved, parts = completed or {}, []
        client = ChatClient(options['key'])
        expires = time.monotonic() + 180
        for index,record in enumerate(request['records']):
            single = dict(request,records=[record])
            if record['input_id'] in saved:
                part = saved[record['input_id']]
                if part is None:
                    part = _interrupted_record(request,record)
                    checkpoint(record,part)
            else:
                checkpoint(record,None)
                remaining = max(0.000001,expires-time.monotonic())
                try:
                    part = process_media_request(single,assets,client,settings,
                        MediaLimits(record_seconds=min(70,remaining),batch_seconds=remaining))
                    check_pair(single,part)
                except (ValueError, ValidationError, KeyError, TypeError):
                    part = _interrupted_record(request,record,'此记录的识别结果未通过校验；其他记录保留。','invalid_agent_output')
                except Exception:
                    part = _interrupted_record(request,record,'此记录处理失败；其他记录保留，可定向重试。')
                # Reserve a failure envelope for every remaining record before saving a result.
                reserved = [saved.get(item['input_id']) or _interrupted_record(request,item,
                    '本批结果容量不足，请拆分导入。','limit_exceeded') for item in request['records'][index+1:]]
                try:
                    _combine_records(request,parts+[part]+reserved)
                except (ValueError,ValidationError) as error:
                    limited = str(error)=='result_too_large' or isinstance(error,ValidationError) and error.validator=='maxItems'
                    part = _interrupted_record(request,record,
                        '本批结果容量不足，请拆分导入；此前结果保留。' if limited else '此记录与批次结果不一致；此前结果保留。',
                        'limit_exceeded' if limited else 'invalid_agent_output')
                    _combine_records(request,parts+[part]+reserved)
                checkpoint(record,part)
            check_pair(single,part)
            parts.append(part)
        return _combine_records(request,parts)


def _failed_result(request, code='agent_unavailable', message='附件识别暂不可用；没有将其当作无任务'):
    return dict(contract_version='0.3.1', request_id=request['request_id'], status='failed', record_results=[], tasks=[], errors=[dict(input_id=None, asset_id=None, block_id=None, stage='request', code=code, message=message, retryable=code=='agent_unavailable')])


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


def _store_candidates(db, row, result, origin_job_id=None):
    request = json.loads(row['request'])
    task_row = db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"', (row['user_id'],)).fetchone()
    applied = {history.get('storageId') for task in json.loads(task_row['value']) for history in task.get('recognitionHistory', [])} if task_row else set()
    db.execute('INSERT INTO recognition_batches VALUES(?,?,?,?,?,0,NULL) ON CONFLICT(user_id,request_id) DO UPDATE SET result=excluded.result',
               (row['user_id'], row['request_id'], row['fingerprint'], row['request'], json.dumps(result, ensure_ascii=False)))
    if origin_job_id and origin_job_id != row['id']:
        return
    for candidate in result['tasks']:
        storage_id = row['id'] + ':' + candidate['candidate_id']
        state = 'applied' if storage_id in applied else 'pending'
        db.execute('INSERT OR IGNORE INTO recognition_candidates(id,user_id,request_id,candidate,state) VALUES(?,?,?,?,?)',
                   (storage_id,row['user_id'],request['request_id'],json.dumps(candidate,ensure_ascii=False),state))


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
            attempt = db.execute('SELECT targets FROM wechat_retries WHERE job_id=? AND retry_id=?', (job_id,row['retry_id'])).fetchone() if row['retry_id'] else None
            selected = set(json.loads(attempt['targets'])) if attempt else {item['input_id'] for item in request['records']}
            done = {item['input_id'] for item in request['records']} - selected
            extraction = dict(request, records=[item for item in request['records'] if item['input_id'] in selected])
            db.execute("UPDATE wechat_jobs SET status='running' WHERE id=?", (job_id,))
            db.commit()
            attempt_errors = []
            origin_job_id, content_key = job_id, None
            try:
                assets = account_asset_loader(db_path, row['user_id'])
                content_key = _content_key(request)
                cached = db.execute('''SELECT j.* FROM wechat_content_cache c JOIN wechat_jobs j ON j.id=c.job_id
                    WHERE c.user_id=? AND c.content_key=? AND j.user_id=? AND j.status='succeeded' ''',
                    (row['user_id'],content_key,row['user_id'])).fetchone() if content_key and not previous else None
                if cached:
                    # A cached semantic result does not grant access to revoked/corrupt files.
                    expires = time.monotonic() + 180
                    for record in request['records']:
                        for asset in record['attachments']:
                            if asset.get('content_mode')=='file' and asset['status']=='available':
                                remaining = expires-time.monotonic()
                                if remaining<=0:
                                    raise MediaError(503,'file_read_failed','缓存原件核验超时，请拆分导入',True)
                                assets(asset['file_ref'],max_bytes=asset['size_bytes'],timeout_seconds=min(10,remaining))
                    result = dict(json.loads(cached['result']),request_id=request['request_id'])
                    origin_job_id = _origin_job(db,cached['id'])
                else:
                    def checkpoint(record, part):
                        db.execute('BEGIN IMMEDIATE')
                        current = db.execute('SELECT status,retry_id FROM wechat_jobs WHERE id=? AND user_id=?',(job_id,row['user_id'])).fetchone()
                        if not current or current['status']!='running' or current['retry_id']!=row['retry_id']:
                            db.rollback()
                            raise FileNotFoundError('job_removed')
                        db.execute('INSERT INTO wechat_record_runs VALUES(?,?,?,?) ON CONFLICT(job_id,retry_id,input_id) DO UPDATE SET result=excluded.result',
                            (job_id,row['retry_id'] or '',record['input_id'],json.dumps(part,ensure_ascii=False) if part is not None else None))
                        db.commit()
                    completed = {item['input_id']:json.loads(item['result']) if item['result'] is not None else None
                        for item in db.execute('SELECT input_id,result FROM wechat_record_runs WHERE job_id=? AND retry_id=?',(job_id,row['retry_id'] or ''))}
                    # Recovered results may belong to a previous deployment/configuration.
                    if completed:
                        content_key = None
                    result = recognize_v03(extraction, assets,checkpoint=checkpoint,completed=completed)
                check_pair(extraction, result)
                attempt_errors = result['errors']
                if previous and not result['record_results']:
                    result = previous
                elif previous:
                    outcomes = {item['input_id']:copy.deepcopy(item) for item in previous['record_results']}
                    outcomes.update({item['input_id']:item for item in result['record_results']})
                    prior = {item['candidate_id']: item for item in previous['tasks']}
                    tasks = list(prior.values())
                    for item in result['tasks']:
                        if item['candidate_id'] in prior:
                            if item == prior[item['candidate_id']]:
                                continue
                            item = dict(item,candidate_id='retry:'+hashlib.sha256((row['retry_id']+':'+item['candidate_id']).encode()).hexdigest())
                        tasks.append(item)
                    for outcome in outcomes.values():
                        outcome['task_ids'] = [item['candidate_id'] for item in tasks if item['input_id'] == outcome['input_id']]
                    errors = [item for item in previous['errors'] if item['input_id'] in done] + result['errors']
                    failures = sum(item['status']=='failed' for item in outcomes.values())
                    partial = any(item['status']=='partial_success' for item in outcomes.values())
                    status = ('failed' if failures==len(request['records']) else 'partial_success') if failures else 'partial_success' if partial else 'success' if tasks else 'no_tasks'
                    result = dict(contract_version='0.3.1', request_id=request['request_id'], status=status,
                                  record_results=[outcomes[item['input_id']] for item in request['records']], tasks=tasks, errors=errors)
                    check_pair(request, result)
                if previous:
                    check_retry_result(request,previous,result,sorted(selected))
            except NotImplementedError:
                attempt_errors = _failed_result(request)['errors']
                result = previous if previous else _failed_result(request, message='附件读取适配器尚未接入；没有将其当作无任务')
            except MediaError as error:
                attempt_errors = _failed_result(request,error.code,error.message)['errors']
                result = previous if previous else _failed_result(request,error.code,error.message)
            except FileNotFoundError:
                attempt_errors = _failed_result(request,'file_ref_unavailable','附件已删除或失效，请重新上传')['errors']
                result = previous if previous else _failed_result(request,'file_ref_unavailable','附件已删除或失效，请重新上传')
            except (ValueError, ValidationError, KeyError, TypeError) as error:
                limited = str(error)=='result_too_large' or isinstance(error,ValidationError) and error.validator=='maxItems'
                code = 'limit_exceeded' if limited else 'invalid_agent_output'
                message = '合并结果超过容量限制，请拆分导入；已有结果保留。' if limited else '识别结果未通过 v0.3.1 校验；任务未新增'
                attempt_errors = _failed_result(request,code,message)['errors']
                result = previous if previous else _failed_result(request,code,message)
            except Exception:
                attempt_errors = _failed_result(request)['errors']
                result = previous if previous else _failed_result(request)
            status = {'success':'succeeded', 'no_tasks':'succeeded', 'partial_success':'partial_failed', 'failed':'failed'}[result['status']]
            try:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT status FROM wechat_jobs WHERE id=? AND user_id=?',(job_id,row['user_id'])).fetchone()
                if not current or current['status']!='running':
                    db.rollback()
                    return
                revoked = set()
                for record in extraction['records']:
                    try:
                        _validate_assets(db,row['user_id'],dict(extraction,records=[record]))
                    except MediaError as error:
                        if error.status!=404:raise
                        revoked.add(record['input_id'])
                if revoked:
                    message='附件已撤销或失效；未发布本次识别结果，请重新上传。'
                    attempt_errors = _failed_result(request,'file_ref_unavailable',message)['errors']
                    content_key = None
                    if previous:
                        result = previous
                    elif not result['record_results']:
                        result = _failed_result(request,'file_ref_unavailable',message)
                    else:
                        parts = []
                        outcomes = {item['input_id']:item for item in result['record_results']}
                        for index,record in enumerate(request['records']):
                            rid = record['input_id']
                            if rid in revoked:
                                parts.append(_interrupted_record(request,record,message,'file_ref_unavailable'))
                            else:
                                parts.append(dict(record_results=[outcomes[rid]],tasks=[item for item in result['tasks'] if item['input_id']==rid],
                                    errors=[item for item in result['errors'] if item['input_id']==rid or (index==0 and item['input_id'] is None)]))
                        result = _combine_records(request,parts)
                    status = {'success':'succeeded','no_tasks':'succeeded','partial_success':'partial_failed','failed':'failed'}[result['status']]
                if status in ('succeeded', 'partial_failed'):
                    if origin_job_id == job_id:
                        _apply_clear_tasks(db, row, result)
                        _store_candidates(db, row, result)
                    else:
                        db.execute('INSERT INTO wechat_job_origins VALUES(?,?)',(job_id,origin_job_id))
                        _store_candidates(db,row,result,origin_job_id)
                db.execute('UPDATE wechat_jobs SET status=?,result=?,previous_result=NULL WHERE id=?', (status, json.dumps(result, ensure_ascii=False), job_id))
                if content_key and status=='succeeded' and not attempt_errors:
                    db.execute('INSERT INTO wechat_content_cache VALUES(?,?,?) ON CONFLICT(user_id,content_key) DO UPDATE SET job_id=excluded.job_id',
                               (row['user_id'],content_key,origin_job_id))
                if row['retry_id']:
                    db.execute('UPDATE wechat_retries SET status=?,errors=? WHERE job_id=? AND retry_id=?', (status,json.dumps(attempt_errors,ensure_ascii=False),job_id,row['retry_id']))
                db.commit()
            except Exception:
                db.rollback()
                failure = previous if previous else _failed_result(request)
                fallback_status = {'success':'succeeded','no_tasks':'succeeded','partial_success':'partial_failed','failed':'failed'}[failure['status']]
                db.execute('UPDATE wechat_jobs SET status=?,result=?,previous_result=NULL WHERE id=?',
                           (fallback_status, json.dumps(failure, ensure_ascii=False), job_id))
                if row['retry_id']:
                    db.execute('UPDATE wechat_retries SET status=?,errors=? WHERE job_id=? AND retry_id=?',
                               (fallback_status,json.dumps(_failed_result(request)['errors']),job_id,row['retry_id']))
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
        _start_job(db_path, job_id)


def route(handler, db, uid, path, body, data_dir):
    if not path.startswith('/api/sources/wechat/'):
        return False
    if path == '/api/sources/wechat/remove' and handler.command == 'POST':
        if handler.headers.get('Authorization'):
            raise MediaError(403,'forbidden','请在登录页面删除来源')
        if body != {'confirmed':True}:
            raise MediaError(400,'invalid_request','请确认删除原件和导入记录')
        db.execute('BEGIN IMMEDIATE')
        remove_source(db,uid)
        pending = db.execute('SELECT COUNT(*) FROM wechat_pending_deletes WHERE user_id=?',(uid,)).fetchone()[0]
        handler.reply(200,dict(configured=False,cleanup_pending=pending))
        return True
    if path == '/api/sources/wechat/credential':
        if handler.headers.get('Authorization'):
            raise MediaError(403, 'forbidden', '请在登录页面管理导入凭证')
        row = db.execute('SELECT 1 FROM wechat_credentials WHERE user_id=?', (uid,)).fetchone()
        if handler.command == 'GET':
            handler.reply(200, dict(configured=bool(row),limits=dict(asset_bytes=MAX_ASSET,account_bytes=200*1024*1024,
                queued_jobs=MAX_JOBS,unreferenced_ttl_seconds=_asset_ttl())))
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
    if path.startswith('/api/sources/wechat/assets/') and handler.command == 'DELETE':
        if handler.headers.get('Authorization'):
            raise MediaError(403,'forbidden','请在登录页面撤销原件')
        ref = unquote(path[len('/api/sources/wechat/assets/'):])
        db.execute('BEGIN IMMEDIATE')
        asset = db.execute('SELECT * FROM wechat_assets WHERE ref=? AND user_id=?',(ref,uid)).fetchone()
        if not asset:
            if not db.execute('SELECT 1 FROM wechat_revoked_assets WHERE ref=? AND user_id=?',(ref,uid)).fetchone():
                raise MediaError(404,'not_found','附件不存在或不属于当前账号')
        else:
            db.execute('INSERT INTO wechat_revoked_assets VALUES(?,?)',(ref,uid))
            db.execute('INSERT INTO wechat_pending_deletes VALUES(?,?,?)',(ref,uid,asset['path']))
            db.execute('DELETE FROM wechat_assets WHERE ref=?',(ref,))
            db.execute('DELETE FROM wechat_asset_lifetime WHERE ref=?',(ref,))
            _purge_deleted_files(db)
        handler.reply(204,{})
        return True
    if path == '/api/sources/wechat/imports' and handler.command == 'GET':
        _expire_unreferenced(db)
        scope=' AND id IN(SELECT job_id FROM manual_material_jobs)' if getattr(handler,'user_materials',False) else ''
        rows = db.execute('SELECT * FROM wechat_jobs WHERE user_id=?'+scope+' ORDER BY rowid DESC LIMIT 20', (uid,)).fetchall()
        jobs = []
        for row in rows:
            attempt = db.execute('SELECT errors FROM wechat_retries WHERE job_id=? AND retry_id=?', (row['id'],row['retry_id'])).fetchone()
            origin = db.execute('SELECT request_id FROM wechat_jobs WHERE id=? AND user_id=?',(_origin_job(db,row['id']),uid)).fetchone()
            items = [dict(id=item['id'],candidate_id=json.loads(item['candidate'])['candidate_id'],state=item['state'],version=item['version']) for item in db.execute('SELECT * FROM recognition_candidates WHERE user_id=? AND request_id=?', (uid,origin['request_id']))]
            jobs.append(dict(_job(row,db),sources=_sources(json.loads(row['request'])),items=items,last_attempt_errors=json.loads(attempt['errors']) if attempt and attempt['errors'] else []))
            if getattr(handler,'user_materials',False):
                jobs[-1]['retry_remaining']=max(0,3-row['attempts'])
        handler.reply(200, dict(jobs=jobs))
        return True
    if path == '/api/sources/wechat/imports' and handler.command == 'POST':
        if body.get('contract_version') != '0.3.1':
            raise MediaError(400, 'unsupported_version', '仅支持微信附件 v0.3.1 导入')
        try:
            check_request(body)
        except ValidationError as error:
            if error.validator in ('maxItems','maxLength','maxProperties','maximum'):
                raise MediaError(413,'limit_exceeded','导入内容超过字段或数量限制') from None
            raise MediaError(400,'invalid_request','v0.3.1 导入请求格式不正确') from None
        except (ValueError,KeyError,TypeError,RecursionError) as error:
            if str(error) in ('request_too_large','hints_too_large','too_many_hint_nodes','hints_too_deep_or_wide','hint_text_too_long','record_text_too_large'):
                raise MediaError(413,'limit_exceeded','导入内容超过限制') from None
            raise MediaError(400, 'invalid_request', 'v0.3.1 导入请求格式不正确') from None
        db.execute('BEGIN IMMEDIATE')
        _validate_assets(db, uid, body)
        fingerprint = _fingerprint(body)
        row = db.execute('SELECT * FROM wechat_jobs WHERE user_id=? AND request_id=?', (uid, body['request_id'])).fetchone()
        if row:
            if getattr(handler,'user_materials',False) and not db.execute('SELECT 1 FROM manual_material_jobs WHERE job_id=?',(row['id'],)).fetchone():
                raise MediaError(409,'request_conflict','请求编号已用于其他导入')
            if row['fingerprint'] != fingerprint:
                raise MediaError(409, 'request_conflict', '同一 request_id 对应不同内容')
            handler.reply(200, dict(contract_version='0.3.1', job_id=row['id'], request_id=row['request_id'], status=row['status']))
            return True
        if db.execute('SELECT 1 FROM recognition_batches WHERE user_id=? AND request_id=?', (uid,body['request_id'])).fetchone():
            raise MediaError(409, 'request_conflict', '请求编号已用于其他识别批次')
        if db.execute("SELECT COUNT(*) FROM wechat_jobs WHERE status IN ('queued','running')").fetchone()[0] >= MAX_JOBS:
            raise MediaError(503, 'queue_busy', '导入队列已满，请稍后重试', True)
        job_id = 'job-' + secrets.token_urlsafe(18)
        db.execute('INSERT INTO wechat_jobs(id,user_id,request_id,fingerprint,request,status,result,previous_result,retry_id,attempts) VALUES(?,?,?,?,?,?,?,?,?,0)', (job_id, uid, body['request_id'], fingerprint, json.dumps(body, ensure_ascii=False), 'queued', None, None, None))
        _retain_assets(db,job_id,body)
        if getattr(handler,'user_materials',False):
            db.execute('INSERT INTO manual_material_jobs VALUES(?)',(job_id,))
        db.commit()
        _start_job(handler.media_db_path, job_id)
        handler.reply(202, dict(contract_version='0.3.1', job_id=job_id, request_id=body['request_id'], status='queued'))
        return True
    parts = path[len('/api/sources/wechat/imports/'):].split('/') if path.startswith('/api/sources/wechat/imports/') else []
    if parts:
        if handler.command == 'POST':
            db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM wechat_jobs WHERE id=? AND user_id=?', (parts[0], uid)).fetchone()
        if not row:
            raise MediaError(404, 'not_found', '导入任务不存在')
        if len(parts) == 1 and handler.command == 'GET':
            result = _job(row, db)
            check_job(json.loads(row['request']), result)
            handler.reply(200, result)
            return True
        if len(parts) == 2 and parts[1] == 'retry' and handler.command == 'POST':
            retry_id = body.get('retry_id')
            try:
                schema('wechat-import', body, 'retry')
            except Exception:
                raise MediaError(400, 'invalid_request', '重试编号无效') from None
            accepted = db.execute('SELECT status FROM wechat_retries WHERE job_id=? AND retry_id=?', (row['id'],retry_id)).fetchone()
            if accepted:
                handler.reply(200, dict(contract_version='0.3.1', job_id=row['id'], request_id=row['request_id'], status=accepted['status']))
                return True
            previous = json.loads(row['result']) if row['result'] else None
            if (previous and not previous['record_results'] and not previous['tasks'] and previous['errors']
                    and all(e['input_id'] is None and e['code']=='agent_unavailable' and e['retryable'] for e in previous['errors'])):
                # No record ever succeeded: materialize bounded identities for a
                # recoverable request failure. Partial results retain their history.
                request=json.loads(row['request'])
                try: previous=_combine_records(request,[_interrupted_record(request,r,'上次处理未完成，可重试此记录。') for r in request['records']])
                except (ValueError,ValidationError,KeyError,TypeError):
                    raise MediaError(413,'limit_exceeded','失败内容超过恢复容量，请拆分重新导入') from None
            selected = retryable_records(previous) if previous else set()
            if (row['status'] not in ('failed','partial_failed') or row['attempts'] >= 3 or not previous
                    or not selected):
                raise MediaError(409, 'invalid_job_state', '当前任务不能重试')
            request=json.loads(row['request'])
            _validate_assets(db,uid,dict(request,records=[record for record in request['records'] if record['input_id'] in selected]))
            if db.execute("SELECT COUNT(*) FROM wechat_jobs WHERE status IN ('queued','running')").fetchone()[0] >= MAX_JOBS:
                raise MediaError(503,'queue_busy','导入队列已满，请稍后重试',True)
            db.execute('INSERT INTO wechat_retries(job_id,retry_id,targets,status) VALUES(?,?,?,?)', (row['id'],retry_id,json.dumps(sorted(selected)),'queued'))
            db.execute("UPDATE wechat_jobs SET status='queued',result=NULL,previous_result=?,retry_id=?,attempts=attempts+1 WHERE id=?", (json.dumps(previous), retry_id, row['id']))
            db.commit()
            _start_job(handler.media_db_path, row['id'])
            handler.reply(202, dict(contract_version='0.3.1', job_id=row['id'], request_id=row['request_id'], status='queued'))
            return True
    raise MediaError(404, 'not_found', '接口不存在')

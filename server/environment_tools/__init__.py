"""Source-only account tools. Excluded from every release image."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time

import runtime_environment
import usage

MODULE_URL = '/api/environment-tools.js'
RESET_URL = '/api/environment-tools/reset-account'
# Bound memory while serializing foreground requests with an account reset.
_locks = [threading.RLock() for _ in range(64)]
OWNER_TABLES = (
    'proposals', 'decomposition_proposals', 'recognition_candidates',
    'recognition_batches', 'recognition_import_records', 'recognition_record_policies',
    'recognition_attempt_diagnostics', 'source_versions', 'teaching_sources',
    'teaching_asset_refs', 'teaching_asset_files', 'teaching_asset_inputs',
    'teaching_submission_recoveries', 'teaching_submission_requests', 'teaching_submission_sources',
    'submission_controls', 'submission_operations', 'submission_events',
    'source_sync_operations', 'source_sync_quiet_runs', 'source_sync_checks', 'source_sync_runs',
    'reprocessing_latest', 'reprocessing_request_sources', 'reprocessing_sources',
    'reprocessing_teaching_sources', 'reprocessing_runs', 'public_source_imports', 'public_source_runs',
    'wechat_content_cache', 'wechat_assets', 'wechat_jobs',
)
WORKSPACE_DEFAULTS = {name: [] for name in (
    'tasks', 'candidates', 'feedback', 'courses', 'materials', 'sources')}
WORKSPACE_DEFAULTS.update(availability=dict(startTime='19:00', endTime='22:00', weekdays=[1,2,3,4,5]),
                          preferences=dict(askCompletionMinutes=False), focus={})


def enabled():
    return runtime_environment.managed() and runtime_environment.environment() in ('development', 'test')


@contextmanager
def account_request(handler, connection, ApiError):
    # Re-authenticate inside the lock in the normal route. A waiting old request
    # cannot write after reset rotates all of this account's sessions.
    lock = nullcontext()
    if enabled():
        with connection() as db:
            try:
                user, _ = handler.user(db)
            except ApiError as error:
                if error.status != 401: raise
            else:
                lock = _locks[int(hashlib.sha256(user['id'].encode()).hexdigest()[:8], 16) % len(_locks)]
    with lock:
        yield


def reset(db, uid, path, ApiError):
    if not enabled(): raise ApiError(404, '接口不存在')
    # Never infer a safe database from the process's default environment.
    marker = Path(path).parent / '.niuma-environment.json'
    try:
        identity = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ApiError(409, '请使用统一环境入口启动本地或测试环境后再清空') from None
    if not isinstance(identity, dict) or identity.get('environment') != runtime_environment.environment():
        raise ApiError(409, '数据目录环境标记不匹配，已拒绝清空')
    db.execute('BEGIN IMMEDIATE')
    # A cancelled model/crawler can still flush its diagnostic finally block.
    # Keep the reset boundary in the test database and ignore only older attempts.
    db.execute('CREATE TABLE IF NOT EXISTS environment_account_resets(user_id TEXT PRIMARY KEY, reset_at TEXT NOT NULL)')
    db.execute('INSERT INTO environment_account_resets VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET reset_at=excluded.reset_at',
               (uid, datetime.now(timezone.utc).isoformat()))
    db.execute('''CREATE TRIGGER IF NOT EXISTS environment_reset_diagnostics
        BEFORE INSERT ON recognition_attempt_diagnostics
        WHEN EXISTS(SELECT 1 FROM environment_account_resets r WHERE r.user_id=NEW.user_id AND (
            NEW.created_at <= r.reset_at OR (substr(NEW.request_id,1,8)='collect:' AND NOT EXISTS(
                SELECT 1 FROM source_sync_runs s WHERE s.user_id=NEW.user_id AND s.run_id=substr(NEW.request_id,9)))))
        BEGIN SELECT RAISE(IGNORE); END''')
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'wechat_assets' in tables:
        db.execute('INSERT OR IGNORE INTO wechat_pending_deletes SELECT ref,user_id,path FROM wechat_assets WHERE user_id=?', (uid,))
        db.execute('INSERT OR IGNORE INTO wechat_revoked_assets SELECT ref,user_id FROM wechat_assets WHERE user_id=?', (uid,))
        for table in ('wechat_retries', 'wechat_job_assets', 'wechat_job_origins', 'wechat_record_runs', 'manual_material_jobs'):
            if table in tables:
                db.execute(f'DELETE FROM {table} WHERE job_id IN(SELECT id FROM wechat_jobs WHERE user_id=?)', (uid,))
        db.execute('DELETE FROM wechat_asset_lifetime WHERE ref IN(SELECT ref FROM wechat_assets WHERE user_id=?)', (uid,))
    for table in OWNER_TABLES:
        if table in tables: db.execute(f'DELETE FROM {table} WHERE user_id=?', (uid,))
    # Retain increasing versions, including for previously empty collections.
    # Old browser tabs cannot restore a pre-reset workspace with version zero.
    for name, value in WORKSPACE_DEFAULTS.items():
        db.execute('''INSERT INTO workspace VALUES(?,?,?,1) ON CONFLICT(user_id,name)
            DO UPDATE SET value=excluded.value,version=workspace.version+1''', (uid, name, json.dumps(value)))
    db.execute('''INSERT INTO source_sync_schedules VALUES(?,0,180,'',1,NULL,NULL,NULL)
        ON CONFLICT(user_id) DO UPDATE SET enabled=0,next_run_at=NULL,last_run_id=NULL,
        error=NULL,revision=source_sync_schedules.revision+1''', (uid,))
    key = usage.actor(uid)
    for table in ('usage_events', 'usage_visits', 'usage_feedback'):
        db.execute(f'DELETE FROM {table} WHERE actor=?', (key,))
    db.execute('DELETE FROM error_reports WHERE actor=?', (hashlib.sha256(('error-report:' + uid).encode()).hexdigest(),))
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
    db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, time.time()+30*86400))
    db.commit()
    # Physical files are removed only after the invalidation transaction commits.
    # Failed deletions remain in the durable cleanup queue, scoped to this user.
    if 'wechat_pending_deletes' in tables:
        root = Path(path).parent.resolve() / 'wechat-assets' / uid
        for asset in db.execute('SELECT * FROM wechat_pending_deletes WHERE user_id=?', (uid,)).fetchall():
            target = Path(asset['path'])
            if target.parent.resolve() != root: continue
            try: target.unlink(missing_ok=True)
            except OSError: continue
            db.execute('DELETE FROM wechat_pending_deletes WHERE ref=? AND user_id=?', (asset['ref'], uid))
    pending = db.execute('SELECT COUNT(*) FROM wechat_pending_deletes WHERE user_id=?', (uid,)).fetchone()[0] if 'wechat_pending_deletes' in tables else 0
    return token, pending


def route(handler, db, path, body, ApiError, database, cookie_factory):
    if not enabled(): return False
    if path == MODULE_URL and handler.command == 'GET':
        data = Path(__file__).with_name('account-tools.js').read_bytes()
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/javascript; charset=utf-8')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Content-Length', str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True
    if path != RESET_URL: return False
    if handler.command != 'POST': raise ApiError(405, '操作不支持')
    user, _ = handler.user(db)
    if set(body) != {'confirm', 'accountId'} or body['confirm'] is not True:
        raise ApiError(400, '请确认清空当前账号的测试数据')
    if body['accountId'] != user['id']:
        raise ApiError(409, '账号已切换，请刷新后重新确认')
    token, pending = reset(db, user['id'], database, ApiError)
    handler.reply(200, dict(reset=True, pendingFiles=pending), cookie_factory(token, 30*86400))
    return True

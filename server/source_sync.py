"""Run the teaching-web crawler with one request's credentials and return v0.2 requests."""
import json
import hashlib
import os
import subprocess
import tempfile
import secrets
from pathlib import Path

from text_contract import validate_request
from recognition import has_unapplied_auto_tasks
import teaching
from contracts.teaching_capture import check_capture

CRAWLER = Path(__file__).resolve().parent.parent / 'crawler'


def initialize(db):
    teaching.initialize(db)
    db.execute('CREATE TABLE IF NOT EXISTS source_credentials(user_id TEXT PRIMARY KEY, username TEXT NOT NULL, secret BLOB NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS source_versions(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        digest TEXT NOT NULL, request TEXT NOT NULL,
        PRIMARY KEY(user_id,account,input_id))''')


def incremental_requests(db, uid, username, requests):
    """Keep the first observation of each content version, including its time anchor."""
    account = hashlib.sha256(username.encode()).hexdigest()
    pending, seen = [], set()
    total = skipped = 0
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    for batch in requests:
        for record in batch['records']:
            # Crawler input_id is stable per course/item; collection time is not content.
            content = {k: v for k, v in record.items() if k != 'collected_at'}
            digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            identity = (record['input_id'], digest)
            if identity in seen:
                continue
            seen.add(identity)
            total += 1
            row = db.execute('SELECT digest,request FROM source_versions WHERE user_id=? AND account=? AND input_id=?',
                             (uid, account, record['input_id'])).fetchone()
            if row and row['digest'] == digest:
                request = json.loads(row['request'])
            else:
                request = dict(contract_version='0.2', request_id='teaching_web:sync:' + secrets.token_hex(16), records=[record])
                db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?) ON CONFLICT(user_id,account,input_id) DO UPDATE SET digest=excluded.digest,request=excluded.request',
                           (uid, account, record['input_id'], digest, json.dumps(request)))
            stored = db.execute('SELECT result,demo FROM recognition_batches WHERE user_id=? AND request_id=?',
                                (uid, request['request_id'])).fetchone()
            if (stored and json.loads(stored['result'])['status'] in ('success', 'no_tasks')
                    and not has_unapplied_auto_tasks(db, uid, request, stored['demo'])):
                skipped += 1
            else:
                pending.append(request)
    return dict(requests=pending, summary=dict(total=total, skipped=skipped, pending=len(pending)))


def cipher(key_path, ApiError, create=False):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise ApiError(503, '凭据加密依赖未安装；请安装 server/requirements.txt')
    try:
        key = key_path.read_bytes()
    except FileNotFoundError:
        if not create:
            raise ApiError(503, '凭据加密密钥缺失；请联系服务器管理员或重新保存凭据')
        key = Fernet.generate_key()
        try:
            with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as file:
                file.write(key)
        except FileExistsError:
            key = key_path.read_bytes()
    try:
        return Fernet(key)
    except ValueError:
        raise ApiError(503, '凭据加密密钥无效；请联系服务器管理员')


def route(handler, db, uid, path, body, ApiError, key_path):
    if path == '/api/sources/credentials' and handler.command == 'GET':
        row = db.execute('SELECT username FROM source_credentials WHERE user_id=?', (uid,)).fetchone()
        handler.reply(200, {'configured': bool(row), 'username': row['username'] if row else None})
        return True
    if path == '/api/sources/credentials' and handler.command == 'POST':
        username, password = body.get('username'), body.get('password')
        if (not isinstance(username, str) or not isinstance(password, str)
                or not username.strip() or not password or len(username) > 200 or len(password) > 200
                or '\n' in username or '\r' in username or '\n' in password or '\r' in password):
            raise ApiError(400, '请填写有效的教学网账号和密码')
        encrypted = cipher(key_path, ApiError, create=True).encrypt(password.encode())
        db.execute('INSERT INTO source_credentials VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,secret=excluded.secret', (uid, username.strip(), encrypted))
        handler.reply(200, {'configured': True, 'username': username.strip()})
        return True
    if path == '/api/sources/credentials/remove' and handler.command == 'POST':
        db.execute('DELETE FROM source_credentials WHERE user_id=?', (uid,))
        handler.reply(200, {'configured': False, 'username': None})
        return True
    if path != '/api/sources/sync' or handler.command != 'POST':
        return False
    if body.get('sources') != ['teaching_web']:
        raise ApiError(400, '当前仅支持同步教学网；微信请使用会话文件导入')
    handler.reply(200, collect(db, uid, ApiError, key_path))
    return True


def collect(db, uid, ApiError, key_path, *, guard=None):
    """Collect outside a write transaction, then validate ownership before saving."""
    if guard: guard(db)
    row = db.execute('SELECT username,secret FROM source_credentials WHERE user_id=?', (uid,)).fetchone()
    if not row:
        raise ApiError(400, '请先在同步信息中保存教学网账号和密码')
    try:
        from cryptography.fernet import InvalidToken
        password = cipher(key_path, ApiError).decrypt(row['secret']).decode()
    except (InvalidToken, UnicodeDecodeError):
        raise ApiError(503, '无法解密教学网凭据；请重新保存账号和密码')
    if not (CRAWLER / 'node_modules' / 'tsx').exists() and not (CRAWLER / 'node_modules' / '.bin' / 'tsx').exists():
        raise ApiError(503, '教学网采集依赖未安装；请先在 crawler/ 执行 pnpm install')
    try:
        with tempfile.TemporaryDirectory(prefix='niuma-course-sync-') as folder:
            secret = Path(folder) / 'secret'
            with os.fdopen(os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as file:
                file.write(row['username'] + '\n' + password)
            result = subprocess.run(
                ['node', '--import', 'tsx', 'src/pku-homework-cli.ts', '--format=teaching-capture'],
                cwd=CRAWLER, env={**os.environ, 'PKU_SECRET_PATH': str(secret)},
                capture_output=True, text=True, encoding='utf-8', timeout=300, check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise ApiError(503, '教学网采集无法启动或超时；请检查 Node、浏览器及校园网连接')
    if result.returncode:
        raise ApiError(502, '教学网采集失败；请检查账号、密码、浏览器及校园网连接')
    try:
        requests = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if len(requests) > 1000 or any(len(json.dumps(item, ensure_ascii=False).encode()) > 1900000 for item in requests):
            raise ValueError()
        for item in requests:
            if item.get('contract_version')=='teaching-capture/0.1': check_capture(item)
            else: teaching.check_request(item)
    except Exception:
        raise ApiError(502, '教学网采集结果不符合 v0.4 协议或批次数量限制') from None
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    if guard: guard(db)
    current = db.execute('SELECT username,secret FROM source_credentials WHERE user_id=?',(uid,)).fetchone()
    if not current or dict(current)!=dict(row):
        raise ApiError(409, '教学网账号在采集期间发生变化，请重新同步')
    return teaching.register(db, uid, row['username'], requests)

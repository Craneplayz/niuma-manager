"""Run the teaching-web crawler with one request's credentials and return v0.2 requests."""
import json
import hashlib
import logging
import os
import subprocess
import tempfile
import secrets
import time
from pathlib import Path

from text_contract import validate_request

CRAWLER = Path(__file__).resolve().parent.parent / 'crawler'
LOGGER = logging.getLogger(__name__)
TEACHING_SECTION_ORDER = {'课程作业': 0, '课程通知': 1, '教学内容': 2}


def teaching_section(record):
    """Read routing metadata only; never infer a section from the title or body."""
    hints = record.get('upstream_hints')
    section = hints.get('section') if isinstance(hints, dict) else None
    return section.strip() if isinstance(section, str) else section


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS source_credentials(user_id TEXT PRIMARY KEY, username TEXT NOT NULL, secret BLOB NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS source_versions(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        digest TEXT NOT NULL, request TEXT NOT NULL,
        PRIMARY KEY(user_id,account,input_id))''')


def incremental_requests(db, uid, username, requests):
    """Select sections, preserve content versions, then order the pending records."""
    started = time.monotonic()
    account = hashlib.sha256(username.encode()).hexdigest()
    pending, seen = [], set()
    total = skipped = 0
    counts = {section: dict(pending=0, unchanged=0) for section in TEACHING_SECTION_ORDER}
    filtered = dict(unsupported_section=0, missing_section=0, invalid_section=0)
    collected = other_sources = 0
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
            collected += 1
            section = None
            if record.get('platform') == 'teaching_web':
                section = teaching_section(record)
                if section is None or section == '':
                    filtered['missing_section'] += 1
                    continue
                if not isinstance(section, str):
                    filtered['invalid_section'] += 1
                    continue
                if section not in TEACHING_SECTION_ORDER:
                    filtered['unsupported_section'] += 1
                    continue
            else:
                other_sources += 1
            total += 1
            row = db.execute('SELECT digest,request FROM source_versions WHERE user_id=? AND account=? AND input_id=?',
                             (uid, account, record['input_id'])).fetchone()
            if row and row['digest'] == digest:
                request = json.loads(row['request'])
            else:
                request = dict(contract_version='0.2', request_id='teaching_web:sync:' + secrets.token_hex(16), records=[record])
                db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?) ON CONFLICT(user_id,account,input_id) DO UPDATE SET digest=excluded.digest,request=excluded.request',
                           (uid, account, record['input_id'], digest, json.dumps(request)))
            stored = db.execute('SELECT result FROM recognition_batches WHERE user_id=? AND request_id=?',
                                (uid, request['request_id'])).fetchone()
            if stored and json.loads(stored['result'])['status'] in ('success', 'no_tasks'):
                skipped += 1
                if section is not None:
                    counts[section]['unchanged'] += 1
            else:
                pending.append(request)
                if section is not None:
                    counts[section]['pending'] += 1
    # Reorder only teaching-web slots; other sources retain their relative positions.
    teaching = iter(sorted((r for r in pending if r['records'][0]['platform'] == 'teaching_web'),
                           key=lambda r: TEACHING_SECTION_ORDER[teaching_section(r['records'][0])]))
    pending = [next(teaching) if r['records'][0]['platform'] == 'teaching_web' else r for r in pending]
    # No message text, input IDs, user IDs, campus accounts or arbitrary section labels.
    LOGGER.info('source_sync_selection %s', json.dumps(dict(
        collected=collected, eligible=total, sections=counts, filtered=filtered,
        other_sources=other_sources, pending=len(pending), unchanged=skipped,
        selection_ms=round((time.monotonic() - started) * 1000)), ensure_ascii=False))
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
                ['node', '--import', 'tsx', 'src/pku-homework-cli.ts', '--format=recognition'],
                cwd=CRAWLER, env={**os.environ, 'PKU_SECRET_PATH': str(secret)},
                capture_output=True, text=True, encoding='utf-8', timeout=300, check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise ApiError(503, '教学网采集无法启动或超时；请检查 Node、浏览器及校园网连接')
    if result.returncode:
        raise ApiError(502, '教学网采集失败；请检查账号、密码、浏览器及校园网连接')
    try:
        requests = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if len(requests) > 20 or any(len(json.dumps(item, ensure_ascii=False).encode()) > 1900000 for item in requests):
            raise ValueError()
        for item in requests:
            validate_request(item)
    except (ValueError, TypeError, KeyError):
        raise ApiError(502, '教学网采集结果不符合 v0.2 协议或批次数量限制')
    handler.reply(200, incremental_requests(db, uid, row['username'], requests))
    return True

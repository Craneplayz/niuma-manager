"""Opt-in account-level model comparison. Persist metadata, never task content."""
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from contextvars import ContextVar

from server import operations

_current = ContextVar('model_experiment', default=None)
MODULES = {'text', 'teaching', 'decomposition', 'estimation'}
B_MODELS = {'google/gemini-3.1-flash-lite'}
ENDPOINTS = {'https://api.qnaigc.com/v1', 'https://api.modelink.ai/v1'}
RETENTION = 30 * 86400
GENERATED = ('success', 'no_tasks', 'partial_success', 'ready_to_plan', 'no_split', 'needs_clarification')


def actor(uid, name):
    salt = os.environ.get('NIUMA_EXPERIMENT_SALT', '')
    if len(salt) < 32:
        return None
    return hmac.new(salt.encode(), f'{name}:{uid}'.encode(), hashlib.sha256).hexdigest()


def assignment(uid):
    name = os.environ.get('NIUMA_EXPERIMENT_ID', '')
    model = os.environ.get('NIUMA_EXPERIMENT_B_MODEL', '')
    endpoint = os.environ.get('NIUMA_EXPERIMENT_B_ENDPOINT', '')
    if (not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', name)
            or model not in B_MODELS or endpoint not in ENDPOINTS
            or uid not in os.environ.get('NIUMA_EXPERIMENT_ACCOUNTS', '').split()):
        return None
    identity = actor(uid, name)
    if identity:
        return dict(experiment=name, actor=identity, arm='A' if int(identity[-1], 16) % 2 == 0 else 'B',
                    alternative=dict(model=model, endpoint=endpoint))


def options(base):
    run = _current.get()
    if not run or base['provider'] != 'qiniu' or not base['key'].strip():
        return base
    selected = dict(base, **run['alternative']) if run['arm'] == 'B' else base
    run['model'] = selected['model']
    return selected


def path_for(db):
    return db.execute('PRAGMA database_list').fetchone()[2]


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS model_exposures (
        id TEXT PRIMARY KEY, experiment TEXT NOT NULL, actor TEXT NOT NULL, arm TEXT NOT NULL,
        module TEXT NOT NULL, model TEXT NOT NULL, at REAL NOT NULL, duration_ms INTEGER NOT NULL,
        status TEXT NOT NULL, calls INTEGER NOT NULL, tokens INTEGER, score INTEGER CHECK (score BETWEEN 1 AND 5),
        ordinal INTEGER, prompted INTEGER NOT NULL DEFAULT 0)''')
    db.execute('CREATE INDEX IF NOT EXISTS model_exposure_actor ON model_exposures(experiment,actor,at)')


def persist(path, entry, status, started):
    if not entry.get('model'):
        return
    try:
        with operations.connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            initialize(db)
            supplied = db.execute('''SELECT total_tokens FROM producer_events
                WHERE request_id=? AND stage='model_call' ''', (entry['id'],)).fetchall()
            markers = db.execute('''SELECT count(*) FROM events
                WHERE request_id=? AND stage='model_call' ''', (entry['id'],)).fetchone()[0]
            calls = len(supplied) or markers
            if not calls:
                return
            tokens = sum(row[0] for row in supplied) if supplied and all(row[0] is not None for row in supplied) else None
            now = time.time()
            ordinal = None
            if status in GENERATED:
                ordinal = db.execute('''SELECT coalesce(max(ordinal),0)+1 FROM model_exposures
                    WHERE experiment=? AND actor=?''',(entry['experiment'],entry['actor'])).fetchone()[0]
            db.execute('DELETE FROM model_exposures WHERE at<?', (now-RETENTION,))
            db.execute('''INSERT INTO model_exposures
                (id,experiment,actor,arm,module,model,at,duration_ms,status,calls,tokens,ordinal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (entry['id'], entry['experiment'], entry['actor'], entry['arm'], entry['module'],
                        entry['model'], now, round((time.monotonic()-started)*1000), status, calls, tokens, ordinal))
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).warning('model_experiment_write_failed')


def run(db, uid, module, function, *args, **kwargs):
    entry = assignment(uid)
    path = path_for(db) if entry and module in MODULES else None
    if not path:
        return function(*args, **kwargs)
    entry.update(id=secrets.token_hex(16), module=module)
    token = _current.set(entry)
    started = time.monotonic()
    status = 'failed'
    try:
        with operations.scope(path, request_id=entry['id']):
            result = function(*args, **kwargs)
        output = result[0] if isinstance(result, tuple) else result
        status = output.get('status', 'success') if isinstance(output, dict) else 'success'
        return result
    finally:
        _current.reset(token)
        persist(path, entry, status, started)


def route(handler, business_db, uid, path, body, error):
    if path not in ('/api/usage/model-experiment', '/api/usage/model-experiment/prompt', '/api/admin/model-experiment'):
        return False
    name = os.environ.get('NIUMA_EXPERIMENT_ID', '')
    identity = actor(uid, name)
    cutoff = time.time()-RETENTION
    with operations.connection(path_for(business_db)) as db:
        initialize(db)
        if path.startswith('/api/admin/') and handler.command == 'GET':
            rows = db.execute('''SELECT experiment,arm,module,model,count(*) AS attempts,
                count(DISTINCT actor) AS accounts, count(ordinal) AS generated,
                sum(status IN ('success','no_tasks','ready_to_plan','no_split','needs_clarification')) AS usable,
                sum(calls) AS calls, sum(tokens) AS tokens, count(tokens) AS measuredTokens,
                round(avg(duration_ms)) AS meanMs, count(score) AS feedback,
                round(avg(score),2) AS meanScore,
                sum(score=1) AS score1, sum(score=2) AS score2, sum(score=3) AS score3,
                sum(score=4) AS score4, sum(score=5) AS score5
                FROM model_exposures WHERE at>=?
                GROUP BY experiment,arm,module,model ORDER BY experiment,module,arm''', (cutoff,))
            return handler.reply(200, {'rows':[dict(row) for row in rows]}) or True
        if path == '/api/usage/model-experiment' and handler.command == 'GET':
            from urllib.parse import parse_qs, urlsplit
            before = parse_qs(urlsplit(handler.path).query).get('before', [''])[0]
            if before and (not before.isascii() or not before.isdecimal() or len(before)>12):
                raise error(400, '反馈分页编号无效')
            rows = db.execute('''SELECT id,module,status,at,score,ordinal FROM model_exposures
                WHERE experiment=? AND actor=? AND at>=? AND ordinal<?
                ORDER BY ordinal DESC LIMIT 21''', (name,identity,cutoff,int(before) if before else 10**12)).fetchall()
            prompt = db.execute('''SELECT 1 FROM model_exposures WHERE experiment=? AND actor=? AND at>=?
                AND ordinal%2=0 AND prompted=0 AND score IS NULL LIMIT 1''', (name,identity,cutoff)).fetchone()
            return handler.reply(200, {'results':[dict(row) for row in rows[:20]],'next':rows[19]['ordinal'] if len(rows)>20 else None,'promptDue':bool(prompt)}) or True
        if path == '/api/usage/model-experiment/prompt' and handler.command == 'POST':
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT id,module,status,at FROM model_exposures WHERE experiment=? AND actor=? AND at>=?
                AND ordinal%2=0 AND prompted=0 AND score IS NULL ORDER BY ordinal LIMIT 1''', (name,identity,cutoff)).fetchone()
            if row:
                db.execute('UPDATE model_exposures SET prompted=1 WHERE id=?', (row['id'],))
            db.commit()
            return handler.reply(200, {'result':dict(row) if row else None}) or True
        if path == '/api/usage/model-experiment' and handler.command == 'POST':
            receipt, score = body.get('id'), body.get('score')
            if (set(body) != {'id', 'score'} or not isinstance(receipt, str)
                    or not re.fullmatch(r'[0-9a-f]{32}',receipt) or type(score) is not int or not 1<=score<=5):
                raise error(400, '请选择 1–5 分')
            saved = db.execute('''UPDATE model_exposures SET score=?
                WHERE id=? AND experiment=? AND actor=? AND at>=? AND ordinal IS NOT NULL''', (score,receipt,name,identity,cutoff))
            if saved.rowcount != 1:
                raise error(404, '未找到这次模型结果')
            db.commit()
            return handler.reply(200, {'saved':True}) or True
    raise error(405, '此操作不支持当前请求方式')

"""Persistent, body-free cache for PKU mailbox headers and range snapshots."""
import datetime
import json
import sqlite3
import threading
import time
import uuid
import hashlib
from contextlib import contextmanager
from pathlib import Path

_lock = threading.RLock()
_sync_locks = {}
_sync_locks_guard = threading.Lock()


def _sync_lock(key):
    with _sync_locks_guard:
        entry = _sync_locks.get(key)
        if entry is None:
            entry = [threading.RLock(), 0]
            _sync_locks[key] = entry
        entry[1] += 1
        return entry


def _release_sync_lock(key, entry):
    entry[0].release()
    _drop_sync_reference(key, entry)


def _drop_sync_reference(key, entry):
    with _sync_locks_guard:
        entry[1] -= 1
        if entry[1] == 0 and _sync_locks.get(key) is entry:
            del _sync_locks[key]


def initialize(db):
    """Create cache tables on an application-owned connection, without committing it."""
    db.execute('''CREATE TABLE IF NOT EXISTS email_header_snapshots (
        user_id TEXT NOT NULL, address TEXT NOT NULL, range_key TEXT NOT NULL,
        uidvalidity TEXT NOT NULL, snapshot_id TEXT NOT NULL, revision INTEGER NOT NULL,
        uids TEXT NOT NULL, synced_at TEXT, PRIMARY KEY(user_id,address,range_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS email_mailbox_epochs (
        user_id TEXT NOT NULL, address TEXT NOT NULL, uidvalidity TEXT NOT NULL,
        generation INTEGER NOT NULL, PRIMARY KEY(user_id,address))''')
    db.execute('''CREATE TABLE IF NOT EXISTS email_cached_headers (
        user_id TEXT NOT NULL, address TEXT NOT NULL, uidvalidity TEXT NOT NULL,
        uid INTEGER NOT NULL, metadata BLOB NOT NULL, headers BLOB NOT NULL,
        PRIMARY KEY(user_id,address,uidvalidity,uid))''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(email_header_snapshots)')}
    if 'synced_at' not in columns:
        db.execute('ALTER TABLE email_header_snapshots ADD COLUMN synced_at TEXT')


@contextmanager
def _connection(path):
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        initialize(db)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _range_key(limit, start_date):
    return json.dumps({'limit': limit if start_date is None else None,
                       'start_date': start_date}, sort_keys=True, separators=(',', ':'))


def _decode_row(source, address, validity, uid, metadata, raw_headers, identity_namespace=None):
    from email.parser import BytesParser
    from email.policy import default
    received = source._received_at(metadata)
    headers = BytesParser(policy=default).parsebytes(raw_headers)
    sender = source._decode_header(headers.get('From', ''))[:300]
    subject = source._decode_header(headers.get('Subject', ''))[:300] or '(无主题)'
    return {'input_id': source._identity(address, validity, uid, identity_namespace), 'subject': subject,
            'sender': sender, 'received_at': received.isoformat() if received else None}


def _response(uids, records, offset, page_size, total, matched_total=None, snapshot_id=None,
              complete=True, cache_only=False, synced_at=None, load_all=False):
    selected = uids if load_all else uids[offset:offset + page_size]
    messages = [records[uid] for uid in selected if uid in records]
    summary = {'total': total}
    if matched_total is not None:
        summary['matched_total'] = matched_total
    boundary = matched_total if matched_total is not None else total
    return {'messages': messages, 'summary': summary,
            'next_offset': offset + page_size if not load_all and offset + page_size < boundary else None,
            'cache': {'snapshot_id': snapshot_id, 'complete': complete, 'miss': not complete,
                      'cache_only': cache_only, 'synced_at': synced_at}}


def _read_row(db, user_id, address, range_key):
    return db.execute('SELECT * FROM email_header_snapshots WHERE user_id=? AND address=? AND range_key=?',
                      (str(user_id), address, range_key)).fetchone()


def _guarded_commit(db, guard, error_type):
    db.execute('BEGIN IMMEDIATE')
    if guard is not None and not guard(db):
        raise error_type(409, '邮箱配置已更改，请重新读取')


def preview(cache_path, user_id, address, limit=50, start_date=None, offset=0, page_size=50,
            *, refresh=False, cache_only=False, snapshot_id=None, password_loader=None,
            guard=None, select_uids=None, load_all=False, identity_namespace=None, _coalesced=False,
            _coalesced_result=False):
    """Read a cached page or sync its range. Credentials load only when IMAP is needed."""
    try:
        from . import email_source as source
    except ImportError:
        import email_source as source
    address = source.normalize_address(address)
    source._start_day(start_date)
    if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise source.EmailSourceError(400, '邮件分页条件无效')
    if start_date is None and (type(limit) is not int or limit < 1):
        raise source.EmailSourceError(400, '邮件数量范围无效')
    if type(load_all) is not bool or (load_all and offset!=0):
        raise source.EmailSourceError(400,'邮件列表获取条件无效')
    range_key = _range_key(limit, start_date)
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)

    with _connection(cache_path) as db:
        initial = _read_row(db, user_id, address, range_key)
        epoch = db.execute('SELECT uidvalidity,generation FROM email_mailbox_epochs WHERE user_id=? AND address=?',
                           (str(user_id), address)).fetchone()
        if epoch is None:
            legacy = db.execute('SELECT uidvalidity FROM email_header_snapshots WHERE user_id=? AND address=? LIMIT 1',
                                (str(user_id), address)).fetchone()
            epoch_state = (legacy['uidvalidity'], 0) if legacy else (None, 0)
        else:
            epoch_state = (epoch['uidvalidity'], epoch['generation'])
        if snapshot_id and (initial is None or initial['snapshot_id'] != snapshot_id):
            raise source.EmailSourceError(409, '邮件列表已更新，请刷新后重试')
        if initial is not None and not refresh:
            indexed = json.loads(initial['uids'])
            selected = list(select_uids(initial['uidvalidity'], tuple(indexed))) if select_uids else indexed
            page = selected if load_all else selected[offset:offset + page_size]
            records = {}
            for uid in page:
                hit = db.execute('SELECT metadata,headers FROM email_cached_headers WHERE user_id=? AND address=? AND uidvalidity=? AND uid=?',
                                 (str(user_id), address, initial['uidvalidity'], uid)).fetchone()
                if hit:
                    records[uid] = _decode_row(source, address, initial['uidvalidity'], uid, hit['metadata'], hit['headers'], identity_namespace)
            matched = len(selected) if select_uids else None
            if len(records) == len(page):
                return _response(selected, records, offset, page_size, len(indexed), matched,
                                 initial['snapshot_id'], True, cache_only, initial['synced_at'], load_all=load_all)
            if _coalesced_result:
                return _response(selected, records, offset, page_size, len(indexed), matched,
                                 initial['snapshot_id'], False, cache_only, initial['synced_at'], load_all=load_all)
            if cache_only:
                return _response(selected, records, offset, page_size, len(indexed), matched,
                                 initial['snapshot_id'], False, True, initial['synced_at'], load_all=load_all)
        elif cache_only:
            return {'messages': [], 'summary': {'total': 0}, 'next_offset': None,
                    'cache': {'snapshot_id': None, 'complete': False, 'miss': True,
                              'cache_only': True, 'synced_at': None}}

    if password_loader is None:
        raise source.EmailSourceError(503, '邮件缓存缺少标题，请刷新后重试')
    username, password = password_loader()
    def check_live():
        if guard is None:
            return
        with _connection(cache_path) as db:
            if not guard(db):
                raise source.EmailSourceError(409, '邮箱配置已更改，请重新读取')

    if not _coalesced:
        # Serialize only same-account, same-range, same-credential syncs. The
        # second caller rechecks the SQLite cache after acquiring the lock and
        # returns without opening an IMAP session when the first filled it.
        credential_key = hashlib.sha256(
            str(username).encode('utf-8') + b'\0' + str(password).encode('utf-8')).hexdigest()
        key = (str(Path(cache_path).resolve()), str(user_id), address, range_key, credential_key)
        entry = _sync_lock(key)
        sync_lock = entry[0]
        acquired_now = sync_lock.acquire(blocking=False)
        waited = not acquired_now
        started_wait = time.monotonic()
        try:
            while not acquired_now:
                check_live()
                if time.monotonic() - started_wait >= 80:
                    raise source.EmailSourceError(503, '邮箱同步繁忙，请稍后重试')
                acquired_now = sync_lock.acquire(timeout=0.1)
        except BaseException:
            _drop_sync_reference(key, entry)
            raise
        try:
            sync_succeeded = False
            if waited:
                with _connection(cache_path) as db:
                    current = _read_row(db, user_id, address, range_key)
                observed = ((initial['snapshot_id'], initial['revision']) if initial is not None else None)
                current_version = ((current['snapshot_id'], current['revision']) if current is not None else None)
                sync_succeeded = current_version is not None and current_version != observed
            return preview(cache_path, user_id, address, limit, start_date, offset, page_size,
                refresh=(refresh and not sync_succeeded), cache_only=cache_only, snapshot_id=snapshot_id,
                password_loader=lambda: (username, password), guard=guard,
                select_uids=select_uids, load_all=load_all, identity_namespace=identity_namespace, _coalesced=True,
                _coalesced_result=sync_succeeded)
        finally:
            _release_sync_lock(key, entry)

    with source._mailbox(address, username, password,
                         cancel_check=check_live if guard is not None else None) as client:
        client._niuma_deadline = __import__('time').monotonic() + 80
        validity = source._select_inbox(client)
        if initial is not None and not refresh:
            if initial['uidvalidity'] != validity:
                raise source.EmailSourceError(409, '收件箱已变化，请显式刷新邮件列表')
            all_uids = json.loads(initial['uids'])  # lazy page uses the original UID index; no SEARCH
        else:
            all_uids = source._search_uids(client, start_date)
            if start_date is None:
                all_uids = all_uids[:limit]
        selected = list(select_uids(validity, tuple(all_uids))) if select_uids else list(all_uids)
        matched_total = len(selected) if select_uids else None
        page = selected if load_all else selected[offset:offset + page_size]
        with _connection(cache_path) as db:
            missing = [uid for uid in page if not db.execute(
                'SELECT 1 FROM email_cached_headers WHERE user_id=? AND address=? AND uidvalidity=? AND uid=?',
                (str(user_id), address, validity, uid)).fetchone()]
        fresh = source._fetch_headers(client, missing)

    synced_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _lock, _connection(cache_path) as db:
        _guarded_commit(db, guard, source.EmailSourceError)
        current = _read_row(db, user_id, address, range_key)
        live_epoch = db.execute('SELECT uidvalidity,generation FROM email_mailbox_epochs WHERE user_id=? AND address=?',
                                (str(user_id), address)).fetchone()
        if live_epoch is None:
            live_legacy = db.execute('SELECT uidvalidity FROM email_header_snapshots WHERE user_id=? AND address=? LIMIT 1',
                                     (str(user_id), address)).fetchone()
            live_epoch_state = (live_legacy['uidvalidity'], 0) if live_legacy else (None, 0)
        else:
            live_epoch_state = (live_epoch['uidvalidity'], live_epoch['generation'])
        # Epoch compares across all ranges while allowing concurrent same-validity ranges.
        same_uninitialized_epoch = (epoch_state == (None, 0) and
                                    live_epoch_state == (validity, 0))
        if live_epoch_state != epoch_state and not same_uninitialized_epoch:
            raise source.EmailSourceError(409, '收件箱已变化，请刷新邮件列表')
        # Compare the state observed before any network request, including absence.
        if ((initial is None) != (current is None) or
                (initial is not None and current is not None and
                 (initial['snapshot_id'] != current['snapshot_id'] or initial['revision'] != current['revision']))):
            raise source.EmailSourceError(409, '邮件列表已更新，请刷新后重试')
        if snapshot_id and (current is None or current['snapshot_id'] != snapshot_id):
            raise source.EmailSourceError(409, '邮件列表已更新，请刷新后重试')
        if current is not None and current['uidvalidity'] != validity and not refresh:
            raise source.EmailSourceError(409, '收件箱已变化，请显式刷新邮件列表')
        # A mailbox UID reset invalidates every range and its headers atomically.
        prior_validity, prior_generation = epoch_state
        if prior_validity is not None and prior_validity != validity:
            db.execute('DELETE FROM email_header_snapshots WHERE user_id=? AND address=?', (str(user_id), address))
            db.execute('DELETE FROM email_cached_headers WHERE user_id=? AND address=?', (str(user_id), address))
            prior_generation += 1
        db.execute('INSERT OR REPLACE INTO email_mailbox_epochs(user_id,address,uidvalidity,generation) VALUES(?,?,?,?)',
                   (str(user_id), address, validity, prior_generation))
        snap_id = str(uuid.uuid4()) if refresh or current is None or current['uidvalidity'] != validity else current['snapshot_id']
        revision = current['revision'] + 1 if current is not None else 1
        snapshot_synced_at = (current['synced_at'] if current is not None and initial is not None and not refresh
                              else synced_at)
        db.execute('INSERT OR REPLACE INTO email_header_snapshots(user_id,address,range_key,uidvalidity,snapshot_id,revision,uids,synced_at) VALUES(?,?,?,?,?,?,?,?)',
                   (str(user_id), address, range_key, validity, snap_id, revision, json.dumps(all_uids), snapshot_synced_at))
        for uid, (meta, headers) in fresh.items():
            db.execute('INSERT OR REPLACE INTO email_cached_headers VALUES(?,?,?,?,?,?)',
                       (str(user_id), address, validity, uid, meta, headers))
        records = {}
        for uid in page:
            hit = db.execute('SELECT metadata,headers FROM email_cached_headers WHERE user_id=? AND address=? AND uidvalidity=? AND uid=?',
                             (str(user_id), address, validity, uid)).fetchone()
            if hit:
                records[uid] = _decode_row(source, address, validity, uid, hit['metadata'], hit['headers'], identity_namespace)
    complete = len(records) == len(page)
    response_synced_at = snapshot_synced_at
    return _response(selected, records, offset, page_size, len(all_uids), matched_total, snap_id,
                     complete, False, response_synced_at, load_all=load_all)


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def delete_mailbox(db, user_id, address=None):
    """Delete cached state on an app connection; no-op before initialize(db)."""
    if not {'email_header_snapshots', 'email_cached_headers', 'email_mailbox_epochs'}.issubset(_tables(db)):
        return
    if address is None:
        db.execute('DELETE FROM email_header_snapshots WHERE user_id=?', (str(user_id),))
        db.execute('DELETE FROM email_cached_headers WHERE user_id=?', (str(user_id),))
        db.execute('DELETE FROM email_mailbox_epochs WHERE user_id=?', (str(user_id),))
    else:
        normalized = address.strip().lower()
        db.execute('DELETE FROM email_header_snapshots WHERE user_id=? AND address=?', (str(user_id), normalized))
        db.execute('DELETE FROM email_cached_headers WHERE user_id=? AND address=?', (str(user_id), normalized))
        db.execute('DELETE FROM email_mailbox_epochs WHERE user_id=? AND address=?', (str(user_id), normalized))


def clear(cache_path, user_id, address):
    address = address.strip().lower()
    with _connection(cache_path) as db:
        delete_mailbox(db, user_id, address)


def snapshot_inputs(db, user_id, address, range_spec, snapshot_id, *, identity_namespace=None):
    """Read the complete loaded range without IMAP or header hydration."""
    try:
        from . import email_source as source
    except ImportError:
        import email_source as source
    if not isinstance(range_spec, dict) or set(range_spec)-{'limit','start_date'}:
        raise source.EmailSourceError(400, '邮件范围无效')
    start=range_spec.get('start_date'); limit=range_spec.get('limit',50)
    if start is not None and (not isinstance(start,str) or not __import__('re').fullmatch(r'\d{4}-\d\d-\d\d',start)):
        raise source.EmailSourceError(400, '邮件日期范围无效')
    source._start_day(start)
    if start is None and (type(limit) is not int or limit<1):
        raise source.EmailSourceError(400, '邮件数量范围无效')
    if not isinstance(snapshot_id,str) or not 1<=len(snapshot_id)<=120:
        raise source.EmailSourceError(400, '邮件列表快照无效')
    row=_read_row(db,user_id,address,_range_key(limit,start))
    if row is None or row['snapshot_id']!=snapshot_id:
        raise source.EmailSourceError(409, '邮件列表已变化，请点击“获取邮件”后重试')
    return [source._identity(address,row['uidvalidity'],uid,identity_namespace) for uid in json.loads(row['uids'])]

"""Private, account-bound teaching attachment receipts, bytes and read cache.

SQLite keeps bytes in the same environment/backup as their source snapshot;
there are no permanent paths or arbitrary URLs accepted from the browser.
"""
import hashlib
import json
import re
import time
from copy import deepcopy
from pathlib import Path

from contracts.teaching_io import check_request
from ddl_text.adapters import AgentError
from ddl_text.image_reader import MetadataMismatch
from ddl_text.teaching_attachment_reader import read_attachment, cache_version

MAX_FILE = 5 * 1024 * 1024
MAX_ACCOUNT = 200 * 1024 * 1024
MIMES = {'application/pdf', 'image/png', 'image/jpeg',
         'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_asset_files(
        user_id TEXT NOT NULL, account TEXT NOT NULL, sha256 TEXT NOT NULL,
        mime_type TEXT NOT NULL, data BLOB NOT NULL, cache_version TEXT, result TEXT,
        PRIMARY KEY(user_id,account,sha256,mime_type))''')
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_asset_refs(
        user_id TEXT NOT NULL, account TEXT NOT NULL, request_id TEXT NOT NULL,
        ref_id TEXT NOT NULL, sha256 TEXT NOT NULL, mime_type TEXT NOT NULL,
        PRIMARY KEY(user_id,account,request_id,ref_id))''')
    # Avoid scanning every reference for every stored file during pruning.
    db.execute('''CREATE INDEX IF NOT EXISTS teaching_asset_refs_file
        ON teaching_asset_refs(user_id,account,sha256,mime_type)''')


def read_capture(folder, requests, *, input_ids=None):
    """Consume only this crawler invocation's directory, before it is removed."""
    folder = Path(folder).resolve()
    manifest = folder / 'manifest.json'
    if not manifest.exists(): return {}  # Compatibility with text-only captures.
    if manifest.is_symlink() or manifest.stat().st_size > 2_000_000: raise ValueError('invalid attachment manifest')
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(entries,list) or len(entries) > 1000: raise ValueError('too many attachments')
    refs = {(e['record']['input_id'], r['ref_id']): r for batch in requests
        for e in batch.get('request',batch)['records'] for r in e['record']['references']}
    assets, seen, files, total = {}, set(), {}, 0
    for item in entries:
        if set(item) != {'input_id','ref_id','sha256','mime_type','size_bytes'}: raise ValueError('invalid receipt')
        if input_ids is not None and item['input_id'] not in input_ids: continue
        key = (item['input_id'],item['ref_id'])
        if key in seen or key not in refs or refs[key]['kind'] not in ('attachment','content_image'): raise ValueError('invalid file binding')
        seen.add(key)
        if (not isinstance(item['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',item['sha256'])
            or item['mime_type'] not in MIMES or type(item['size_bytes']) is not int
            or not 0 < item['size_bytes'] <= MAX_FILE): raise ValueError('invalid file metadata')
        path = folder / (item['sha256'] + '.bin')
        if path.is_symlink() or path.resolve().parent != folder or path.stat().st_size != item['size_bytes']: raise ValueError('invalid staged file')
        data = files.get(item['sha256'])
        if data is None:
            with path.open('rb') as file: data = file.read(MAX_FILE+1)
            total += len(data)
            if total > 100*1024*1024 or hashlib.sha256(data).hexdigest() != item['sha256']: raise ValueError('file integrity/size mismatch')
            files[item['sha256']] = data
        if len(data) != item['size_bytes']: raise ValueError('file integrity/size mismatch')
        assets.setdefault(item['input_id'],[]).append(dict(item,data=data))
    return assets


def identity(assets):
    return sorted([(a['ref_id'],a['sha256'],a['mime_type']) for a in assets])


def store(db, uid, account, request_id, assets):
    for a in assets:
        exists = db.execute('SELECT 1 FROM teaching_asset_files WHERE user_id=? AND account=? AND sha256=? AND mime_type=?',
            (uid,account,a['sha256'],a['mime_type'])).fetchone()
        if not exists:
            used = db.execute('SELECT coalesce(sum(length(data)),0) FROM teaching_asset_files WHERE user_id=?',(uid,)).fetchone()[0]
            if used + len(a['data']) > MAX_ACCOUNT: raise ValueError('教学网附件存储超过 200 MiB，请移除旧来源凭据后重新同步')
            db.execute('INSERT INTO teaching_asset_files VALUES(?,?,?,?,?,NULL,NULL)',(uid,account,a['sha256'],a['mime_type'],a['data']))
        db.execute('INSERT OR REPLACE INTO teaching_asset_refs VALUES(?,?,?,?,?,?)',
            (uid,account,request_id,a['ref_id'],a['sha256'],a['mime_type']))


def clear(db, uid):
    db.execute('DELETE FROM teaching_asset_refs WHERE user_id=?',(uid,))
    db.execute('DELETE FROM teaching_asset_files WHERE user_id=?',(uid,))


def rebind(db, uid, account, old_request_id, new_request_id):
    """Advance captured-file ownership in the source update's transaction."""
    db.execute('''UPDATE teaching_asset_refs SET request_id=?
        WHERE user_id=? AND account=? AND request_id=?''',
        (new_request_id,uid,account,old_request_id))


def prune(db, uid):
    # Superseded snapshots cannot run again; stored results already retain their evidence text.
    db.execute('''DELETE FROM teaching_asset_refs WHERE user_id=? AND NOT EXISTS (
        SELECT 1 FROM teaching_sources s WHERE s.user_id=teaching_asset_refs.user_id
        AND s.account=teaching_asset_refs.account AND s.request_id=teaching_asset_refs.request_id)''',(uid,))
    db.execute('''DELETE FROM teaching_asset_files WHERE user_id=? AND NOT EXISTS (
        SELECT 1 FROM teaching_asset_refs r WHERE r.user_id=teaching_asset_files.user_id
        AND r.account=teaching_asset_files.account AND r.sha256=teaching_asset_files.sha256
        AND r.mime_type=teaching_asset_files.mime_type)''',(uid,))


def load(db, uid, account, request_id):
    return [dict(row) for row in db.execute('''SELECT f.*, r.ref_id FROM teaching_asset_refs r
        JOIN teaching_asset_files f USING(user_id,account,sha256,mime_type)
        WHERE r.user_id=? AND r.account=? AND r.request_id=?''',(uid,account,request_id))]


def save_read_cache(db, uid, asset, version, result):
    """Compare and write under the caller's write transaction, never a stale load."""
    key=(uid,asset['account'],asset['sha256'],asset['mime_type'])
    row=db.execute('''SELECT cache_version,result FROM teaching_asset_files
        WHERE user_id=? AND account=? AND sha256=? AND mime_type=?''',key).fetchone()
    if not row: return  # Account removed or source pruned while a reader was running.
    if row[0]==version and row[1]:
        current=json.loads(row[1])
        if current.get('read_state')=='complete': return
        if result.get('read_state')!='complete':
            def coverage(value):
                return len(value['pages']) if value.get('pdf_checkpoint')==1 else value.get('pdf_pages_read',0)
            before,after=coverage(current),coverage(result)
            if after<before or after==before and current.get('pdf_checkpoint')!=1: return
            if after==before and result.get('pdf_checkpoint')==1: return
    elif (row[0],row[1])!=(asset['cache_version'],asset['result']):
        return  # Another reader/configuration replaced the cache since this load.
    db.execute('''UPDATE teaching_asset_files SET cache_version=?,result=?
        WHERE user_id=? AND account=? AND sha256=? AND mime_type=?''',
        (version,json.dumps(result),*key))


def enrich(request, assets, client, settings, save_cache):
    """Mutate the execution copy only; captured source/digest remain immutable."""
    version = cache_version(settings)
    expires = time.monotonic() + 75  # Leave part of the shared 90s budget for task extraction.
    records = request['records']
    if len(records)!=1: raise ValueError('one captured record required')
    record = records[0]['record']
    by_ref = {r['ref_id']:r for r in record['references']}
    read_cache = {}
    for a in assets:
        if a['ref_id'] not in by_ref: raise ValueError('missing attachment binding')
        if len(a['data']) > MAX_FILE or hashlib.sha256(a['data']).hexdigest() != a['sha256']:
            raise AgentError('agent_unavailable', False)
        key = (a['sha256'],a['mime_type'])
        result = read_cache.get(key)
        checkpoint = None
        if result is None and a['result'] and a['cache_version']==version:
            cached = json.loads(a['result'])
            if cached.get('pdf_checkpoint') == 1: checkpoint = cached
            else: result = dict(read_state=cached['read_state'],text=cached['text'])
        if result is None:
            metadata = dict(mime_type=a['mime_type'],sha256=a['sha256'],size_bytes=len(a['data']))
            pages_read = len(checkpoint['pages']) if checkpoint else 0
            def save_checkpoint(value):
                nonlocal pages_read
                pages_read = len(value['pages'])
                save_cache(a,version,value)
            try:
                resume = dict(checkpoint=checkpoint, save_checkpoint=save_checkpoint) if a['mime_type']=='application/pdf' else {}
                result = read_attachment(a['data'],metadata,client,settings,expires=expires,**resume)
            except MetadataMismatch:
                raise AgentError('agent_unavailable', False) from None
            except AgentError as error:
                if error.retryable or error.code in ('agent_unavailable','invalid_agent_output'):
                    raise  # Persisted as a retryable record failure, never as no_tasks.
                # Broken/unsupported/oversized files remain explicit evidence gaps.
                result = dict(read_state='unsupported' if error.code=='reader_unsupported' else 'failed', text=None)
            if result['read_state'] in ('complete','partial'):
                cached=dict(result,pdf_pages_read=pages_read) if a['mime_type']=='application/pdf' else result
                save_cache(a,version,cached)
        read_cache[key] = result
        ref = by_ref[a['ref_id']]
        if result['text'] is None and ref.get('text'):
            continue  # A failed visual pass must not erase a captured PDF text layer.
        merged = dict(result)
        if ref.get('text') and result['text']:
            merged['text'] = ref['text'] + '\n\n【原件补充读取】\n' + result['text']
        proposal = deepcopy(request)
        next(r for r in proposal['records'][0]['record']['references'] if r['ref_id']==a['ref_id']).update(merged)
        try: check_request(proposal)
        except Exception:
            # Preserve every captured character when supplemental evidence will
            # not fit; retain a coverage gap rather than erasing the original.
            if ref.get('text'): ref['read_state']='partial'
            else: ref.update(read_state='failed',text=None)
        else: ref.update(merged)
    check_request(request)

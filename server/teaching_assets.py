"""Private, account-bound teaching attachment receipts, bytes and read cache.

SQLite keeps bytes in the same environment/backup as their source snapshot;
there are no permanent paths or arbitrary URLs accepted from the browser.
"""
import hashlib
from server import operations
import json
import re
import time
from copy import deepcopy
from pathlib import Path

from contracts.teaching_io import check_request
from ddl_text.adapters import AgentError
from ddl_text.image_reader import MetadataMismatch
from ddl_text.teaching_attachment_reader import MAX_PDF_PAGES, PDF_BATCH_PAGES, read_attachment, cache_version
from server import recognition_diagnostics as diagnostics

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
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_asset_inputs(
        user_id TEXT NOT NULL, account TEXT NOT NULL, request_id TEXT NOT NULL, identity TEXT NOT NULL,
        PRIMARY KEY(user_id,account,request_id))''')


def read_capture(folder, requests, *, input_ids=None):
    """Consume only this crawler invocation's directory, before it is removed."""
    folder = Path(folder).resolve()
    manifest = folder / 'manifest.json'
    if not manifest.exists(): return {}  # Compatibility with text-only captures.
    try:
        if manifest.is_symlink() or manifest.stat().st_size > 2_000_000: return {}
        entries = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(entries,list): return {}
    refs = {(e['record']['input_id'], r['ref_id']): r for batch in requests
        for e in batch.get('request',batch)['records'] for r in e['record']['references']}
    assets, seen, files, total = {}, set(), {}, 0
    for item in entries[:1000]:
        try:
            if not isinstance(item,dict) or set(item) != {'input_id','ref_id','sha256','mime_type','size_bytes'}: continue
            if input_ids is not None and item['input_id'] not in input_ids: continue
            key = (item['input_id'],item['ref_id'])
            if key in seen or key not in refs or refs[key]['kind'] not in ('attachment','content_image'): continue
            if (not isinstance(item['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',item['sha256'])
                or item['mime_type'] not in MIMES or type(item['size_bytes']) is not int
                or not 0 < item['size_bytes'] <= MAX_FILE): continue
            path = folder / (item['sha256'] + '.bin')
            if path.is_symlink() or path.resolve().parent != folder or path.stat().st_size != item['size_bytes']: continue
            data = files.get(item['sha256'])
            if data is None:
                if total + item['size_bytes'] > 100*1024*1024: continue
                with path.open('rb') as file: data = file.read(MAX_FILE+1)
                total += len(data)
                if len(data) != item['size_bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']: continue
                files[item['sha256']] = data
            if len(data) != item['size_bytes']: continue
            assets.setdefault(item['input_id'],[]).append(dict(item,data=data))
            seen.add(key)
        except (OSError, TypeError, ValueError):
            continue  # Reject this file without failing the other sources/files.
    return assets


def identity(assets):
    return sorted([(a['ref_id'],a['sha256'],a['mime_type']) for a in assets])


def retained_identity(db, uid, account, request_id):
    """Compare retained originals without loading their bytes or read caches."""
    return {tuple(row) for row in db.execute('''SELECT r.ref_id,r.sha256,r.mime_type
        FROM teaching_asset_refs r JOIN teaching_asset_files f USING(user_id,account,sha256,mime_type)
        WHERE r.user_id=? AND r.account=? AND r.request_id=?''',(uid,account,request_id))}


def attempted_identity(db, uid, account, request_id):
    row=db.execute('SELECT identity FROM teaching_asset_inputs WHERE user_id=? AND account=? AND request_id=?',
        (uid,account,request_id)).fetchone()
    if row: return {tuple(item) for item in json.loads(row[0])}
    batch=db.execute('SELECT request FROM recognition_batches WHERE user_id=? AND request_id=?',
        (uid,request_id)).fetchone()
    if not batch: return None
    # Legacy results have no attempt receipt. Included references are known to
    # have participated; omitted retained files get one opportunity to recover.
    refs={r['ref_id'] for e in json.loads(batch[0])['records'] for r in e['record']['references']}
    return {item for item in retained_identity(db,uid,account,request_id) if item[0] in refs}


def remember_inputs(db, uid, account, request_id, files):
    db.execute('INSERT OR REPLACE INTO teaching_asset_inputs VALUES(?,?,?,?)',
        (uid,account,request_id,json.dumps(identity(files))))


def store(db, uid, account, request_id, assets):
    # The caller has advanced teaching_sources in this transaction. Bind all
    # retained bytes before pruning, then reclaim retired files before checking
    # the quota for replacements. Omit new files that do not fit the quota.
    for a in assets:
        db.execute('INSERT OR REPLACE INTO teaching_asset_refs VALUES(?,?,?,?,?,?)',
            (uid,account,request_id,a['ref_id'],a['sha256'],a['mime_type']))
    prune(db,uid)
    for a in assets:
        exists = db.execute('SELECT 1 FROM teaching_asset_files WHERE user_id=? AND account=? AND sha256=? AND mime_type=?',
            (uid,account,a['sha256'],a['mime_type'])).fetchone()
        if not exists:
            used = db.execute('SELECT coalesce(sum(length(data)),0) FROM teaching_asset_files WHERE user_id=?',(uid,)).fetchone()[0]
            if used + len(a['data']) > MAX_ACCOUNT:
                db.execute('''DELETE FROM teaching_asset_refs
                    WHERE user_id=? AND account=? AND request_id=? AND ref_id=?''',
                    (uid,account,request_id,a['ref_id']))
                continue
            db.execute('INSERT INTO teaching_asset_files VALUES(?,?,?,?,?,NULL,NULL)',(uid,account,a['sha256'],a['mime_type'],a['data']))


def clear(db, uid):
    db.execute('DELETE FROM teaching_asset_refs WHERE user_id=?',(uid,))
    db.execute('DELETE FROM teaching_asset_files WHERE user_id=?',(uid,))
    db.execute('DELETE FROM teaching_asset_inputs WHERE user_id=?',(uid,))


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
    db.execute('''DELETE FROM teaching_asset_inputs WHERE user_id=? AND NOT EXISTS (
        SELECT 1 FROM teaching_sources s WHERE s.user_id=teaching_asset_inputs.user_id
        AND s.account=teaching_asset_inputs.account AND s.request_id=teaching_asset_inputs.request_id)''',(uid,))


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


def enrich(request, assets, client, settings, save_cache, *, guard=None):
    """Mutate the execution copy only; captured source/digest remain immutable."""
    version = cache_version(settings)
    records = request['records']
    if len(records)!=1: raise ValueError('one captured record required')
    record = records[0]['record']
    by_ref = {r['ref_id']:r for r in record['references']}
    read_cache = {}
    for a in assets:
        if guard: guard()
        if a['ref_id'] not in by_ref: raise ValueError('missing attachment binding')
        ref = by_ref[a['ref_id']]
        if (len(a['data']) > MAX_FILE or a['mime_type'] not in MIMES
                or hashlib.sha256(a['data']).hexdigest() != a['sha256']):
            ref.update(read_state='failed',text=None)
            continue
        # Captured text is authentic but not necessarily visible: PDF text layers
        # may include default-off groups, transparent or clipped content. Always
        # check the raw PDF (or its versioned cache); ordinary pages stay local.
        pdf = a['mime_type']=='application/pdf'
        key = (a['sha256'],a['mime_type'])
        result = read_cache.get(key)
        checkpoint = None
        if result is None and a['result'] and a['cache_version']==version:
            cached = json.loads(a['result'])
            if cached.get('pdf_checkpoint') == 1: checkpoint = cached
            else: result = dict(read_state=cached['read_state'],text=cached['text'])
        if result is None:
            operations.emit('outcome',code='cache_miss',operation='attachment')
            diagnostics.event('attachment',file_sha256=a['sha256'],mime_type=a['mime_type'],bytes=len(a['data']),
                              cache='checkpoint' if checkpoint else 'miss',completed_pages=len(checkpoint['pages']) if checkpoint else 0)
            metadata = dict(mime_type=a['mime_type'],sha256=a['sha256'],size_bytes=len(a['data']))
            pages_read = len(checkpoint['pages']) if checkpoint else 0
            def save_checkpoint(value):
                nonlocal pages_read
                pages_read = len(value['pages'])
                save_cache(a,version,value)
            try:
                batch_pages=PDF_BATCH_PAGES
                reductions_left=2
                while True:
                    if guard: guard()
                    resume = dict(checkpoint=checkpoint, save_checkpoint=save_checkpoint,
                                  single_pdf_batch=True, pdf_batch_pages=batch_pages) if a['mime_type']=='application/pdf' else {}
                    try:
                        with operations.span('attachment_read'):
                            result = read_attachment(a['data'],metadata,client,settings,
                                                     expires=time.monotonic()+75,guard=guard,diagnostic_event=diagnostics.event,**resume)
                    except AgentError as error:
                        # At most two smaller attempts, retaining validated pages.
                        # A one-page timeout or provider outage still exits.
                        if (a['mime_type']=='application/pdf' and batch_pages>1 and reductions_left>0
                                and (error.diagnostic=='output_truncated' or error.code=='agent_timeout')):
                            reductions_left-=1
                            diagnostics.event('pdf_reduce_batch',code=error.code,batch_pages=batch_pages,completed_pages=pages_read)
                            batch_pages=max(1,batch_pages//2)
                            continue
                        raise
                    if result.get('pdf_checkpoint')!=1: break
                    # Only a successful, advancing page batch permits continuation.
                    # Failure never advances the offset; shrinking is bounded above.
                    before=len(checkpoint['pages']) if checkpoint else 0
                    if a['mime_type']!='application/pdf' or not before<len(result['pages'])<MAX_PDF_PAGES:
                        raise AgentError('file_read_failed',False)
                    checkpoint=result
            except MetadataMismatch:
                result = dict(read_state='failed', text=None)
            except AgentError as error:
                diagnostics.event('attachment_skipped',code=error.code,diagnostic=error.diagnostic)
                result = dict(read_state='unsupported' if error.code=='reader_unsupported' else 'failed', text=None)
            if result['read_state'] in ('complete','partial'):
                cached=dict(result,pdf_pages_read=pages_read) if a['mime_type']=='application/pdf' else result
                save_cache(a,version,cached)
        else:
            diagnostics.event('attachment',file_sha256=a['sha256'],mime_type=a['mime_type'],bytes=len(a['data']),cache='hit')
        read_cache[key] = result
        if not pdf and result['text'] is None and ref.get('text'):
            continue  # Retain independently captured non-PDF text on read failure.
        merged = dict(result)
        if not pdf and ref.get('text') and result['text']:
            merged['text'] = ref['text'] + '\n\n【原件补充读取】\n' + result['text']
        proposal = deepcopy(request)
        next(r for r in proposal['records'][0]['record']['references'] if r['ref_id']==a['ref_id']).update(merged)
        try: check_request(proposal)
        except Exception:
            # A storage limit is a coverage gap. Unchecked PDF text cannot be a
            # fallback for verified evidence that will not fit the contract.
            if ref.get('text') and not pdf: ref['read_state']='partial'
            else: ref.update(read_state='failed',text=None)
        else: ref.update(merged)
    # Omit unavailable attachments from this execution's evidence, rather than
    # pretending they were read. The immutable captured source remains intact.
    record['references'] = [ref for ref in record['references']
        if ref['kind'] not in ('attachment','content_image')
        or ref['read_state']=='complete' and ref.get('text') and ref['text'].strip()]
    check_request(request)

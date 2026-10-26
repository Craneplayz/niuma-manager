"""Bounded retrieval from authenticated course snapshots, never from model memory.

Only the same owner, source account, native course and captured semester may
contribute evidence. Loading attachment bytes is deferred until recognition
owns a model-queue slot; selection never loads blobs or calls a model.
"""
from copy import deepcopy
import hashlib
import json
import re
import threading
import time
from jsonschema import ValidationError

from contracts import course_context as contract

MAX_SOURCES = 6
PART_BUDGET = 4000
READ_BUDGET_SECONDS = 45
_READ_LOCK = threading.Lock()
POLICY = re.compile(r'教学大纲|课程大纲|课程安排|教学计划|教学日历|考核|提交规则|课程说明|课程介绍|课程要求|评分标准|截止规则|所有.{0,8}作业|统一.{0,12}提交|syllabus|schedule|calendar|assessment|submission\s+policy', re.I)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_course_index(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        course_id TEXT NOT NULL, term_id TEXT NOT NULL,
        PRIMARY KEY(user_id,account,input_id))''')
    db.execute('''CREATE INDEX IF NOT EXISTS teaching_course_scope
        ON teaching_course_index(user_id,account,course_id,term_id)''')
    db.execute('''CREATE TABLE IF NOT EXISTS teaching_context_dependencies(
        user_id TEXT NOT NULL, account TEXT NOT NULL, input_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, PRIMARY KEY(user_id,account,input_id))''')
    # Populate once for pre-feature captures; no file bytes or model output.
    for row in db.execute('''SELECT s.user_id,s.account,s.input_id,s.request
        FROM teaching_sources s LEFT JOIN teaching_course_index i
        USING(user_id,account,input_id) WHERE i.input_id IS NULL'''):
        try: record=json.loads(row[3])['records'][0]['record']
        except (ValueError,KeyError,IndexError,TypeError):
            record=dict(input_id=row[2],course_term=None)
        index(db,row[0],row[1],record)


def index(db, uid, account, record):
    scoped = contract.scope(record)
    db.execute('INSERT OR REPLACE INTO teaching_course_index VALUES(?,?,?,?,?)',
               (uid,account,record['input_id'],*(scoped or ('',''))))


def terms(text):
    """Language-independent lexical retrieval, not semantic task identity."""
    text = text.casefold()
    tokens = set(re.findall(r'[a-z]+\d*|\d+',text))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        tokens.update(run[i:i+2] for i in range(len(run)-1))
    return tokens - {'作业','课程','通知','提交','完成','homework','assignment'}


def searchable(entry):
    record = entry['record']
    return '\n'.join([entry['source'].get('label') or '',record['text'],
                      *[r.get('label') or '' for r in record['references']]])


def catalog(db, uid, account, scoped=None):
    """Parse/tokenize each course source once per refresh, outside inner loops."""
    groups={}
    sql='''SELECT s.request,s.digest FROM teaching_sources s
        JOIN teaching_course_index i USING(user_id,account,input_id)
        WHERE i.user_id=? AND i.account=?'''
    args=[uid,account]
    if scoped:
        sql+=' AND i.course_id=? AND i.term_id=?'; args.extend(scoped)
    for raw,digest in db.execute(sql,args):
        request=json.loads(raw); other=request['records'][0]
        scope=contract.scope(other['record'])
        if scope is None: continue
        text=searchable(other)
        groups.setdefault(scope,[]).append(dict(request=request,digest=digest,tokens=terms(text),policy=bool(POLICY.search(text)),
            has_files=any(r['kind'] in ('attachment','content_image') for r in other['record']['references'])))
    by_request={m['request']['request_id']:m for group in groups.values() for m in group}
    # Ordinary filenames often hide the actual syllabus/rules. Search completed
    # private read caches too; they are hints only until prepare rechecks the
    # reader version and binds actual excerpts. Original blobs are never loaded.
    for rid,result in db.execute('''SELECT r.request_id,f.result FROM teaching_asset_refs r
        JOIN teaching_asset_files f USING(user_id,account,sha256,mime_type)
        WHERE r.user_id=? AND r.account=? AND f.result IS NOT NULL''',(uid,account)):
        material=by_request.get(rid)
        if material is None: continue
        cached=json.loads(result)
        if cached.get('read_state')!='complete' or not cached.get('text'): continue
        material['tokens'].update(terms(cached['text']))
        material['policy']=material['policy'] or bool(POLICY.search(cached['text']))
    return groups


def select(db, uid, account, entry, *, materials=None, receipts=None):
    scoped = contract.scope(entry['record'])
    if scoped is None: return []
    query = terms(searchable(entry)); selected = []
    if materials is None: materials=catalog(db,uid,account,scoped)
    if receipts is None: receipts={}
    for material in materials.get(scoped,[]):
        request=material['request']; other=request['records'][0]
        record = other['record']; term = record['course_term']; current = entry['record']['course_term']
        if record['input_id']==entry['record']['input_id']: continue
        if (term['starts_on'],term['ends_on']) != (current['starts_on'],current['ends_on']): continue
        overlap=len(query & material['tokens']); policy=material['policy']
        if not policy and overlap < 2 and not material['has_files']: continue
        selected.append(dict(request=request,digest=material['digest'],score=20*policy+min(overlap,15)+int(material['has_files'])))
    # Capture order must not change selection or cause periodic re-recognition.
    selected.sort(key=lambda x:(-x['score'],x['request']['records'][0]['record']['input_id']))
    selected=selected[:MAX_SOURCES]
    for item in selected:
        rid=item['request']['request_id']
        if rid not in receipts: receipts[rid]=completed_receipts(db,uid,account,rid)
        item['observed_receipts']=receipts[rid]
        item['readiness']=contract.revision(receipts[rid])
    return selected


def fingerprint(selected):
    # Retrieval rank is not a source revision. Reading a file may improve its
    # score during this very attempt without changing any of the evidence.
    return contract.revision(sorted((s['request']['records'][0]['record']['input_id'],s['digest']) for s in selected))


def catalog_revision(db, uid, account, entry):
    """Raw captured course membership/content, independent of reader progress.

    Freeze retrieval for one attempt. A concurrent cache completion may change
    even top-K membership; it should schedule a later refresh, not reject valid
    work. Raw additions, removals and edits still invalidate the save, including
    sources outside the selected top-K. Request IDs are execution identities.
    """
    scope = contract.scope(entry['record'])
    if scope is None: return contract.revision([])
    rows = db.execute('''SELECT s.input_id,s.digest,s.request FROM teaching_sources s
        JOIN teaching_course_index i USING(user_id,account,input_id)
        WHERE i.user_id=? AND i.account=? AND i.course_id=? AND i.term_id=?
        ORDER BY s.input_id''', (uid,account,*scope)).fetchall()
    current = entry['record']['course_term']
    values = []
    for iid, digest, raw in rows:
        term = json.loads(raw)['records'][0]['record'].get('course_term')
        if term and (term['starts_on'],term['ends_on']) == (current['starts_on'],current['ends_on']):
            values.append((iid,digest))
    return contract.revision(values)


def cache_receipt(ref,version,result):
    if result and json.loads(result).get('read_state')=='complete':
        return (ref,version,hashlib.sha256(result.encode()).hexdigest())
    return None


def cache_rows(db,uid,account,request_id):
    return db.execute('''SELECT r.ref_id,f.cache_version,f.result
        FROM teaching_asset_refs r JOIN teaching_asset_files f USING(user_id,account,sha256,mime_type)
        WHERE r.user_id=? AND r.account=? AND r.request_id=? ORDER BY r.ref_id''',(uid,account,request_id))


def completed_receipts(db,uid,account,request_id):
    return [receipt for row in cache_rows(db,uid,account,request_id) if (receipt:=cache_receipt(*row)) is not None]


def readiness(db,uid,account,request_id,*,references=None,observed=()):
    """Completed receipts, optionally restricted to evidence actually consumed.

    A concurrent reader can finish a file after this attempt skipped it. Do not
    acknowledge that completion on behalf of an attempt that never saw it.
    Caches already present before the attempt are acknowledged even if omitted
    by the input budget; otherwise an unrepresentable file would retry forever.
    """
    consumed = None if references is None else {r['ref_id']:r for r in references}
    values=[]
    for ref,version,result in cache_rows(db,uid,account,request_id):
        receipt=cache_receipt(ref,version,result)
        if receipt is None: continue
        cached=json.loads(result)
        if consumed is not None and receipt not in observed:
            used=consumed.get(ref,{})
            text=cached.get('text') or ''
            actual=used.get('text') or ''
            if (used.get('read_state')!='complete' or not text
                    or not (actual==text or actual.endswith('\n\n【原件补充读取】\n'+text))): continue
        values.append(receipt)
    return contract.revision(values)


def dependency_fingerprint(selected, own_readiness):
    return contract.revision(dict(own=own_readiness, sources=sorted(
        (s['request']['records'][0]['record']['input_id'],s['digest'],s.get('readiness')) for s in selected)))


def remember(db, uid, account, entry, selected, *, own_readiness=None, references=None, files=()):
    if own_readiness is None:
        row=db.execute('SELECT request_id FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                       (uid,account,entry['record']['input_id'])).fetchone()
        observed=[receipt for a in files if (receipt:=cache_receipt(a['ref_id'],a['cache_version'],a['result'])) is not None]
        own_readiness=readiness(db,uid,account,row[0],references=references,observed=observed) if row else contract.revision([])
    db.execute('INSERT OR REPLACE INTO teaching_context_dependencies VALUES(?,?,?,?)',
               (uid,account,entry['record']['input_id'],dependency_fingerprint(selected,own_readiness)))


def window(text, query, budget):
    """One contiguous original passage, retaining offset rather than rewriting."""
    if len(text) <= budget: return 0,text
    starts = range(0,len(text),max(1,budget//2))
    start = max(starts,key=lambda i:(len(query & terms(text[i:i+budget])) + 4*bool(POLICY.search(text[i:i+budget])),-i))
    return start,text[start:start+budget]


def passages(text, query, budget):
    """Retain up to two disjoint original spans, e.g. a rule and its exception."""
    if len(text)<=budget: return [(0,text)]
    if budget<1200: return [window(text,query,budget)]
    width=budget//2
    def score(i):return len(query & terms(text[i:i+width]))+4*bool(POLICY.search(text[i:i+width]))
    ranked=sorted(range(0,len(text),max(1,width//2)),key=lambda i:(-score(i),i))
    chosen=[]
    for start in ranked:
        if any(start<end and start+width>begin for begin,end in chosen): continue
        if chosen and not score(start): break
        chosen.append((start,min(start+width,len(text))))
        if len(chosen)==2:break
    return [(start,text[start:end]) for start,end in sorted(chosen)]


def attach(request, materials, *, omitted=0):
    record = request['records'][0]['record']
    # Replays may contain a previous context snapshot; rebuild rather than nest.
    record['references'] = [r for r in record['references'] if r['kind'] != 'course_context']
    record.pop('course_context',None)
    if contract.scope(record) is None: return
    budget = min(contract.MAX_CHARACTERS,100000-len(record['text'])-len(record['source_metadata_text'])-
                 sum(len(r['text'] or '') for r in record['references']))
    query = terms(searchable(request['records'][0])); documents=[]
    for material in materials:
        other = material['records'][0]; source = other['source']; r = other['record']
        if contract.scope(r) != contract.scope(record): raise ValueError('course scope changed')
        parts = [('body',r['text'],r['body_status']=='complete')]
        parts += [(ref['ref_id'],ref['text'],ref['read_state']=='complete') for ref in r['references']
                  if ref['kind']!='course_context' and ref['read_state'] in ('partial','complete')]
        for part,text,complete in parts:
            if not text or not text.strip(): continue
            if budget < 240 or len(documents)>=contract.MAX_DOCUMENTS or len(record['references'])>=50:
                omitted+=1; continue
            for offset,excerpt in passages(text,query,min(PART_BUDGET,budget)):
                if not excerpt.strip(): continue
                if len(documents)>=contract.MAX_DOCUMENTS or len(record['references'])>=50:
                    omitted+=1;continue
                identity = ':'.join([r['input_id'],r['snapshot_id'],part,str(offset)])
                rid = 'context:'+hashlib.sha256(identity.encode()).hexdigest()[:32]
                if any(ref['ref_id']==rid for ref in record['references']): raise ValueError('context reference collision')
                document=dict(ref_id=rid,input_id=r['input_id'],snapshot_id=r['snapshot_id'],course_id=r['course_id'],
                    term_id=r['course_term']['term_id'],part_id=part,source_url=source['url'],source_label=source['label'],
                    published_at=r['published_at']['value'] if r['published_at'] else None,offset=offset,length=len(excerpt))
                record['references'].append(dict(ref_id=rid,kind='course_context',label=source['label'] or '课程参考资料',
                    read_state='complete' if complete and len(excerpt)==len(text) else 'partial',text=excerpt))
                documents.append(document); budget-=len(excerpt)
    texts={ref['ref_id']:ref['text'] for ref in record['references']}
    record['course_context']=dict(contract_version=contract.VERSION,documents=documents,omitted_documents=omitted,
        revision=contract.revision([dict(d,text=texts[d['ref_id']]) for d in documents]))
    contract.validate_context(record)


def prepare(request, selected, load_files, client, settings, save_cache, guard, *, read_token=None, load_cached=None):
    """Read selected originals on first import too, within a separate small budget.

    A course attachment is optional context. Missing/invalid/slow files leave a
    coverage gap, not a failed primary record. The normal reader's safe PDF
    visibility checks, cancellation, page checkpoints and cache are reused.
    """
    from server import teaching_assets
    materials=[]; omitted=0; deadline=time.monotonic()+READ_BUDGET_SECONDS
    locked=False
    try:
        for item in selected:
            guard()
            current=deepcopy(item['request']); r=current['records'][0]['record']
            refs=r['references']
            # Related PDF transcripts are accepted only through the same verified
            # bytes/cache path as the primary source, never raw crawler OCR.
            for ref in refs:
                if ref['kind'] in ('attachment','content_image'):
                    ref.update(read_state='unread',text=None)
            # Completed verified caches require neither the model-read lock nor
            # its deadline. A slow preceding file must not hide available proof.
            if load_cached:
                from contracts.teaching_io import check_request
                for ref_id,value in load_cached(item,teaching_assets.cache_version(settings)).items():
                    proposal=deepcopy(current)
                    ref=next((r for r in proposal['records'][0]['record']['references'] if r['ref_id']==ref_id),None)
                    if ref is None: continue
                    ref.update(value)
                    try: check_request(proposal)
                    except (ValueError,KeyError,TypeError,ValidationError): continue
                    next(r for r in refs if r['ref_id']==ref_id).update(value)
            expected=len(refs)
            unread_refs={r['ref_id'] for r in refs if r['kind'] in ('attachment','content_image') and r['read_state']!='complete'}
            unread=bool(unread_refs)
            while unread and not locked and time.monotonic()<deadline:
                guard()
                locked=_READ_LOCK.acquire(timeout=min(.1,max(0,deadline-time.monotonic())))
            files=([a for a in load_files(item) if a['ref_id'] in unread_refs]
                   if unread and locked and time.monotonic()<deadline else [])
            teaching_assets.enrich(current,files,client,settings,save_cache,guard=guard,read_deadline=deadline)
            if read_token: item['readiness']=read_token(item,r['references'])
            omitted+=max(0,expected-len(r['references']))
            materials.append(current)
            del files
    finally:
        if locked: _READ_LOCK.release()
    attach(request,materials,omitted=omitted)


def current(db, uid, account, selected):
    for item in selected:
        record=item['request']['records'][0]['record']
        row=db.execute('SELECT digest FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                       (uid,account,record['input_id'])).fetchone()
        if not row or row[0]!=item['digest']: return False
    return True


def evidence_current(db, uid, account, request):
    """Check persisted provenance at the save transaction, including replays."""
    for entry in request['records']:
        for doc in entry['record'].get('course_context',{}).get('documents',[]):
            row=db.execute('SELECT request FROM teaching_sources WHERE user_id=? AND account=? AND input_id=?',
                           (uid,account,doc['input_id'])).fetchone()
            if not row: return False
            record=json.loads(row[0])['records'][0]['record']
            if record['snapshot_id']!=doc['snapshot_id'] or contract.scope(record)!=(doc['course_id'],doc['term_id']): return False
    return True


def refresh_dependents(db, uid, account):
    """Invalidate changed retrieval inputs once; keep handled tasks protected.

    Use raw source digests, not recognition request IDs, to avoid cyclic
    invalidation when two records cite one another. No model calls under lock.
    """
    import secrets
    from server import teaching_assets
    changed=[]
    rows=db.execute('''SELECT s.request,d.fingerprint FROM teaching_sources s
        JOIN teaching_context_dependencies d USING(user_id,account,input_id)
        WHERE s.user_id=? AND s.account=?''',(uid,account)).fetchall()
    if not rows: return changed
    materials=catalog(db,uid,account); receipts={}
    for raw,previous in rows:
        request=json.loads(raw); entry=request['records'][0]
        chosen=select(db,uid,account,entry,materials=materials,receipts=receipts)
        rid=request['request_id']
        if rid not in receipts: receipts[rid]=completed_receipts(db,uid,account,rid)
        own_readiness=contract.revision(receipts[rid])
        if dependency_fingerprint(chosen,own_readiness)==previous: continue
        old=request['request_id']; request['request_id']='teaching:'+secrets.token_hex(16)
        db.execute('UPDATE teaching_sources SET request_id=?,request=? WHERE user_id=? AND account=? AND input_id=?',
                   (request['request_id'],json.dumps(request),uid,account,entry['record']['input_id']))
        teaching_assets.rebind(db,uid,account,old,request['request_id'])
        db.execute('INSERT OR IGNORE INTO teaching_submission_requests VALUES(?,?,?,?)',
                   (uid,account,request['request_id'],entry['record']['input_id']))
        remember(db,uid,account,entry,chosen,own_readiness=own_readiness)
        changed.append((old,request))
    return changed

"""Authorized frozen-data replay through a new account and real HTTP/SQLite.

Reads a user-scoped, credential-free SQLite export. No school login, model calls,
or live service changes. Private inputs and detailed results stay outside Git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

TABLES = ('teaching_sources', 'teaching_submission_sources', 'teaching_submission_requests',
          'teaching_submission_recoveries', 'teaching_asset_files', 'teaching_asset_refs', 'teaching_asset_inputs',
          'recognition_batches', 'recognition_candidates', 'recognition_record_policies',
          'submission_controls', 'submission_operations', 'submission_events', 'workspace')


def fingerprint(db, uid):
    result = {}
    for table in TABLES:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): continue
        rows = db.execute('SELECT * FROM ' + table + ' WHERE user_id=? ORDER BY rowid', (uid,)).fetchall()
        def clean(value):
            return hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else value
        result[table] = hashlib.sha256(json.dumps([[clean(v) for v in r] for r in rows], ensure_ascii=False).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists() or args.output.exists(): raise SystemExit('Use new database and report paths')
    if os.environ.get('NIUMA_ENVIRONMENT') != 'test': raise SystemExit('Requires an isolated test environment')
    os.environ['NIUMA_DB'] = str(args.database.resolve())
    os.environ['NIUMA_SOURCE_SYNC_ENABLED'] = '0'
    os.environ['NIUMA_SCHEDULE_ENABLED'] = '0'
    import app
    import relation_foundation as foundation
    from contracts import relation_foundation as contract
    from auth_test_support import registration_payload

    source = sqlite3.connect(args.snapshot.resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('BEGIN')
    owners = source.execute('SELECT user_id,username,secret FROM source_credentials').fetchall()
    if len(owners) != 1 or bytes(owners[0]['secret']) != b'not-exported':
        raise SystemExit('Input must contain one authorized owner with no usable credentials')
    owner = owners[0]
    for table in TABLES:
        if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            if source.execute('SELECT 1 FROM ' + table + ' WHERE user_id<>? LIMIT 1', (owner['user_id'],)).fetchone():
                raise SystemExit('Snapshot contains another owner')
    for forbidden in ('sessions', 'users'):
        if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (forbidden,)).fetchone():
            raise SystemExit('Snapshot contains authentication data')
    app.initialize(recover=False)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    base = f'http://127.0.0.1:{server.server_port}/api'
    def call(path, body):
        request = Request(base + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
        try:
            with client.open(request, timeout=30) as response: return response.status, json.load(response)
        except HTTPError as error: return error.code, json.load(error)
    report = dict(snapshot_sha256=hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
                  live_collection=False, live_model=False, frontend_exercised=False,
                  entrypoint='/api/recognition/relation-context', business_data_unchanged=False)
    try:
        email = 'relation-foundation-' + secrets.token_hex(6) + '@example.test'
        payload = registration_payload(dict(email=email, name='跨课程关系基础验收', password=secrets.token_urlsafe(24)),
                                       lambda body: call('/auth/email-code', body))
        status, registered = call('/auth/register', payload)
        if status != 200: raise AssertionError(('registration', status))
        report['account_created'] = True
        with app.connection() as db:
            uid = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()[0]
            for table in TABLES:
                if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): continue
                columns = [r[1] for r in source.execute('PRAGMA table_info(' + table + ')')]
                target_columns = {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}
                if not target_columns: continue
                common = [c for c in columns if c in target_columns]
                for row in source.execute('SELECT * FROM ' + table + ' WHERE user_id=?', (owner['user_id'],)):
                    values = [uid if c == 'user_id' else row[c] for c in common]
                    db.execute('INSERT INTO ' + table + '(' + ','.join(common) + ') VALUES(' + ','.join('?' for _ in common) + ')', values)
            db.execute('INSERT INTO source_credentials VALUES(?,?,?)', (uid, owner['username'], b'snapshot-only-no-login'))
        with app.connection() as db:
            before = fingerprint(db, uid)
            raw = [dict(r) for r in db.execute('SELECT * FROM teaching_sources WHERE user_id=? ORDER BY input_id', (uid,))]
        account = foundation.controls.current_account(source, owner['user_id'])
        report['sources_checked'] = 0
        report['submitted_sources_checked'] = 0
        report['candidate_bindings_checked'] = 0
        report['task_bindings_checked'] = 0
        report['warnings'] = {}
        for row in raw:
            body = dict(source_account_id=account, input_ids=[row['input_id']])
            status, value = call('/recognition/relation-context', body)
            if status != 200: raise AssertionError(('context', status, value))
            original = json.loads(row['request'])['records'][0]
            record = original['record']; result = value['sources'][0]
            # Independent oracle: compare the actual persisted producer fields.
            assert result['input_id'] == record['input_id'] and result['course_id'] == record['course_id']
            assert result['snapshot_id'] == record['snapshot_id']
            assert result['term_id'] == (record.get('course_term') or {}).get('term_id')
            assert result['parts'][0]['text'] == record.get('text', '')[:24000]
            assert result['url'] == original['source'].get('url')
            # Independently enumerate persisted candidate provenance, including
            # applied/ignored cards. Source-only checks would miss lost bindings.
            candidates = {}
            for c in source.execute('''SELECT c.*,b.request AS batch_request FROM recognition_candidates c
                    JOIN recognition_batches b ON b.user_id=c.user_id AND b.request_id=c.request_id
                    JOIN teaching_submission_requests r ON r.user_id=c.user_id AND r.request_id=c.request_id
                    WHERE c.user_id=? AND r.account=? AND r.input_id=?''',
                    (owner['user_id'], account, row['input_id'])):
                if json.loads(c['candidate']).get('input_id') != row['input_id']: continue
                records = [e.get('record', {}) for e in json.loads(c['batch_request']).get('records', [])]
                if any(all(r.get(k) == record.get(k) for k in ('input_id', 'course_id', 'source_item_id'))
                       and r.get('course_term', {}).get('term_id') == record.get('course_term', {}).get('term_id') for r in records):
                    candidates[c['id']] = c['state']
            actual_candidates = {a['binding_id']: a['state'] for a in value['actions'] if a['kind'] == 'candidate'}
            assert actual_candidates == candidates
            workspace = source.execute("SELECT value FROM workspace WHERE user_id=? AND name='tasks'", (owner['user_id'],)).fetchone()
            tasks = json.loads(workspace[0]) if workspace else []
            task_ids = {t['id'] for t in tasks if any(candidates.get(h.get('storageId')) == 'applied'
                        for h in t.get('recognitionHistory', []) if isinstance(h, dict))}
            assert {a['binding_id'] for a in value['actions'] if a['kind'] == 'task'} == task_ids
            report['candidate_bindings_checked'] += len(candidates)
            report['task_bindings_checked'] += len(task_ids)
            for p in result['parts'][1:]:
                if not p['part_id'].startswith('ref:'): continue
                ref = next(r for r in record['references'] if r['ref_id'] == p['part_id'][4:])
                assert p['label'] == str(ref.get('label') or p['part_id'])[:1024]
                if p['sha256']:
                    asset = source.execute('SELECT data FROM teaching_asset_files WHERE user_id=? AND account=? AND sha256=?',
                                           (owner['user_id'], account, p['sha256'])).fetchone()
                    assert asset and hashlib.sha256(asset[0]).hexdigest() == p['sha256']
            if result['submission'] and result['submission']['state'] == 'submitted':
                report['submitted_sources_checked'] += 1
            for warning in value['warnings']:
                code = warning.split(':')[0]
                report['warnings'][code] = report['warnings'].get(code, 0) + 1
            assert call('/recognition/relation-context', body) == (status, value)
            report['sources_checked'] += 1
        scenario = json.loads(args.scenario.read_text())
        status, ctx = call('/recognition/relation-context', dict(source_account_id=account, input_ids=scenario['input_ids']))
        assert status == 200, (status, ctx)
        by_input = {s['input_id']: s for s in ctx['sources']}
        def quote(iid, text):
            s = by_input[iid]; p = s['parts'][0]
            assert text in p['text'], 'Independent expected quote is absent'
            return dict(source_id=s['id'], part_id=p['part_id'], part_revision=p['revision'], offset=p['text'].index(text), quote=text)
        evidence = [quote(iid, scenario['quotes'][iid]) for iid in scenario['input_ids']]
        receiving = by_input[scenario['submission_input_id']]
        assert receiving['submission']['state'] == scenario['expected_submission_state']
        assert all(by_input[i]['submission'] is None or by_input[i]['submission']['state'] != 'submitted'
                   for i in scenario['material_input_ids'])
        relation = dict(contract_version=contract.VERSION, context_revision=ctx['revision'], kind='bundle',
            source_ids=[s['id'] for s in ctx['sources']], evidence=evidence, deadline=None,
            roles=[dict(source_id=s['id'], role='submission' if s['id'] == receiving['id'] else 'content',
                        evidence=[e for e in evidence if e['source_id'] == s['id']]) for s in ctx['sources']],
            units=[dict(id='delivery:' + str(index), label='作业部分' + str(index), channel='书面', action_ids=[],
                        evidence=[quote(iid, scenario['quotes'][iid])]) for index, iid in enumerate(scenario['material_input_ids'])],
            coverage=[dict(source_id=receiving['id'], unit_ids=['delivery:' + str(i) for i in range(len(scenario['material_input_ids']))],
                           extent='complete', evidence=[quote(scenario['submission_input_id'], scenario['quotes'][scenario['submission_input_id']])])])
        with app.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            saved = foundation.record_proposal(db, uid, account, 'real-data-example', ctx, relation)
            assert foundation.record_proposal(db, uid, account, 'real-data-example', ctx, relation) == saved
        with app.connection() as db:
            audited = foundation.read_proposal(db, uid, account, saved['id'])
            assert audited['context'] == ctx and audited['relation'] == relation and len(audited['audit']) == 1
            assert fingerprint(db, uid) == before
        report.update(business_data_unchanged=True, evidence_roundtrip_exact=True, scenario_passed=True,
                      scenario_courses=len({s['course_id'] for s in ctx['sources']}),
                      coverage_units=len(relation['units']), proposal_state=saved['state'],
                      application_allowed=saved['application_allowed'], semantic_relation_origin='independent_user_example_not_model')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
    finally:
        source.close(); server.shutdown(); server.server_close(); worker.join(timeout=5)
        if worker.is_alive(): raise RuntimeError('Replay HTTP server did not stop')


if __name__ == '__main__':
    main()

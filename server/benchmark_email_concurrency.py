"""Synthetic HTTP email capacity benchmark; never reads real config, mail or models.

Example: .venv/bin/python server/benchmark_email_concurrency.py --users 30 --mails 50
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import platform
import resource
import sys
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app
import email_import
import email_source
import recognition
import runtime_environment
import source_sync
from email_test_support import FakeIMAP
from email_test_support import no_tasks


def percentile(values, percentile):
    import math
    values = sorted(values)
    return values[max(0, math.ceil(len(values)*percentile)-1)] if values else None


def benchmark(users=30, mails=50, model_seconds=2, timeout=900, body_bytes=4096):
    durations = []; browse_durations = []; payload_sizes = []; submissions = []; first = {}; failures = []
    lock = threading.Lock(); active = 0; peak = 0; by_user = {}; per_user_peak = 0
    identities = {}; bodies = set(); duplicate_calls = 0
    connections = 0; sessions = 0; peak_sessions = 0
    old_db = app.DB
    started = time.monotonic()
    source_hashes = {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('email_import.py','email_import_routes.py','email_import_store.py','email_scheduler.py','email_source.py','email_mailbox_pool.py','email_db_gate.py','model_queue.py')}
    class QuietHandler(app.Handler):
        def log_message(self, *args):
            pass
    class SyntheticIMAP(FakeIMAP):
        def logout(self):
            nonlocal sessions
            if not self.logged_out:
                with lock: sessions -= 1
            return super().logout()
    def fake_connect(*args, **kwargs):
        nonlocal connections, sessions, peak_sessions
        with lock:
            connections += 1; sessions += 1; peak_sessions = max(peak_sessions,sessions)
        body = (b'Synthetic capacity test. No action required. ' + b'x'*body_bytes)[:body_bytes]
        structure = '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" '+str(len(body))+' 1)'
        return SyntheticIMAP(uids=range(1,mails+1),body=body,structure=structure)
    def model(request, error):
        nonlocal active, peak, per_user_peak, duplicate_calls
        identity = request['records'][0]['input_id']
        owner = identities[identity]
        with lock:
            if identity in bodies:
                duplicate_calls += 1
            bodies.add(identity)
            active += 1; peak = max(peak, active)
            by_user[owner] = by_user.get(owner, 0) + 1
            per_user_peak = max(per_user_peak, by_user[owner])
        try:
            time.sleep(model_seconds)
            return no_tasks(request, error)
        finally:
            with lock:
                active -= 1; by_user[owner] -= 1
    with tempfile.TemporaryDirectory(prefix='niuma-email-load-') as folder:
        app.DB = Path(folder)/'load.sqlite3'
        env = dict(NIUMA_ENVIRONMENT='test', NIUMA_COOKIE_SECURE='0', NIUMA_BACKGROUND_RECOVERY='0',
                   NIUMA_ENABLE_SCHEDULED_SYNC='0', NIUMA_ENABLE_WECHAT='0', NIUMA_EMAIL_CONCURRENCY='2',
                   NIUMA_EMAIL_GLOBAL_CONCURRENCY='8', NIUMA_EMAIL_MAX_JOBS='60',
                   NIUMA_MODEL_REQUESTS_PER_MINUTE='0', NIUMA_MODEL_TOKENS_PER_MINUTE='0',
                   TEXT_HARNESS_URL='', HARNESS_URL='')
        with patch.dict(os.environ, env), patch.object(email_source.imaplib, 'IMAP4_SSL', new=fake_connect), patch.object(recognition, 'recognize', new=model):
            app.initialize(recover=False)
            clients = []
            with app.connection() as db:
                for index in range(users):
                    uid = 'synthetic-load-' + str(index)
                    token = secrets.token_urlsafe(32)
                    address = 'synthetic-load-' + str(index) + '@stu.pku.edu.cn'
                    db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid,uid+'@load.invalid','Synthetic','unused','unused'))
                    db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),uid,time.time()+3600))
                    encrypted = source_sync.cipher(app.DB.parent/'credential.key', app.ApiError, create=True).encrypt(b'Synthetic-only')
                    db.execute('INSERT INTO email_source_credentials VALUES(?,?,?,?)', (uid,address,uid,encrypted))
                    db.execute('INSERT INTO email_import_preferences VALUES(?,?,?,?,?)', (uid,address,email_import.CONSENT_VERSION,'2026-10-20T00:00:00+08:00',0))
                    ids = [email_source._identity(address, '77', number) for number in range(1,mails+1)]
                    identities.update(dict.fromkeys(ids, uid))
                    clients.append((uid,token,ids))
            server = app.ThreadingHTTPServer(('127.0.0.1',0), QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port) + '/api'
            def call(client, path, body=None):
                request = Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json', 'Cookie':runtime_environment.cookie_name()+'='+client[1]})
                tick = time.monotonic()
                try:
                    response = urlopen(request, timeout=30)
                except HTTPError as error:
                    response = error
                with response:
                    raw = response.read(); result = json.loads(raw)
                    elapsed = (time.monotonic()-tick)*1000
                    with lock:
                        if body is not None: submissions.append(elapsed)
                        elif path == '/workspace': browse_durations.append(elapsed)
                        else:
                            durations.append(elapsed)
                            payload_sizes.append(len(raw))
                    if response.status >= 400:
                        raise RuntimeError('synthetic HTTP failure: '+str(response.status))
                    return result
            jobs = []
            try:
                with ThreadPoolExecutor(max_workers=users) as pool:
                    kickoff = time.monotonic()
                    futures = [pool.submit(call, client, '/sources/email/jobs',
                               dict(operation_id='synthetic-load',input_ids=client[2])) for client in clients]
                    for client,future in zip(clients,futures):
                        jobs.append((client,future.result()['job_id']))
                    pending = list(jobs)
                    deadline = kickoff + timeout
                    while pending and time.monotonic() < deadline:
                        futures = [(client,job,pool.submit(call,client,'/sources/email/jobs/'+job+'?summary=1'),pool.submit(call,client,'/workspace')) for client,job in pending]
                        pending = []
                        for client,job,future,browse in futures:
                            status = future.result()
                            browse.result()
                            if status['counts']['processed'] and client[0] not in first:
                                first[client[0]] = time.monotonic()-kickoff
                            if status['state'] in ('queued','running'):
                                pending.append((client,job))
                            elif status['state'] != 'succeeded':
                                failures.append(status['state'])
                        if pending: time.sleep(.25)
                    if pending:
                        raise TimeoutError('synthetic load deadline exceeded')
                email_import.wait_for_jobs(app.DB, timeout=30)
                import_elapsed = time.monotonic()-kickoff
                initial_submissions = list(submissions)
                before_calls, before_connections = len(bodies),connections
                repeat_started = time.monotonic()
                with ThreadPoolExecutor(max_workers=users) as pool:
                    repeated = [pool.submit(call,client,'/sources/email/jobs',dict(operation_id='synthetic-cache-repeat',input_ids=client[2])) for client in clients]
                    repeated_jobs = [(client,future.result()['job_id']) for client,future in zip(clients,repeated)]
                email_import.wait_for_jobs(app.DB,timeout=30)
                for client,job_id in repeated_jobs:
                    status = call(client,'/sources/email/jobs/'+job_id+'?summary=1')
                    if status['state'] != 'succeeded' or status['counts']['cached'] != mails:
                        failures.append('cache_repeat')
                jobs.extend(repeated_jobs)
                report = dict(synthetic=True, hostPlatform=platform.platform(), python=sys.version.split()[0],
                    peakRssBytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                    sourceHashes=source_hashes, users=users, mailsPerUser=mails, expectedRecognitions=users*mails,
                    modelDelaySeconds=model_seconds, syntheticBodyBytes=body_bytes, imapConnections=connections, peakImapSessions=peak_sessions, elapsedSeconds=round(import_elapsed,3), cachedRepeatSeconds=round(time.monotonic()-repeat_started,3), repeatModelCalls=len(bodies)-before_calls, repeatImapConnections=connections-before_connections,
                    recognised=len(bodies), duplicateModelCalls=duplicate_calls, failedJobs=len(failures),
                    peakRecognitionConcurrency=peak, peakPerUserConcurrency=per_user_peak,
                    firstResultP95Seconds=percentile(list(first.values()),.95), firstResultMaxSeconds=max(first.values()) if first else None, allUsersReceivedResult=len(first)==users,
                    submissionP95Ms=percentile(initial_submissions,.95), cachedApiP95Ms=percentile(browse_durations,.95), statusP95Ms=percentile(durations,.95),
                    statusMeanBytes=sum(payload_sizes)/len(payload_sizes) if payload_sizes else None,
                    statusRequests=len(durations), totalSeconds=round(time.monotonic()-started,3))
                return report
            finally:
                # Stop unfinished synthetic jobs before deleting their temporary database.
                for client,job in jobs:
                    try: call(client, '/sources/email/jobs/'+job+'/stop', {})
                    except Exception: pass
                email_import.wait_for_jobs(app.DB, timeout=30)
                server.shutdown(); server.server_close(); thread.join()
                app.DB = old_db


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--users',type=int,default=30)
    parser.add_argument('--mails',type=int,default=50)
    parser.add_argument('--model-seconds',type=float,default=2)
    parser.add_argument('--timeout',type=float,default=900)
    parser.add_argument('--body-bytes',type=int,default=4096)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    if not (1 <= args.users <= 60 and 1 <= args.mails <= 10000 and 0 <= args.model_seconds <= 270 and 64 <= args.body_bytes <= 131072):
        parser.error('invalid synthetic workload')
    result = benchmark(args.users,args.mails,args.model_seconds,args.timeout,args.body_bytes)
    rendered = json.dumps(result,indent=2)+'\n'
    if args.output: args.output.write_text(rendered)
    print(rendered,end='')
    if result['failedJobs'] or result['repeatModelCalls'] or result['repeatImapConnections'] or result['duplicateModelCalls'] or not result['allUsersReceivedResult'] or result['peakRecognitionConcurrency'] > 8 or result['peakPerUserConcurrency'] > 2 or result['peakImapSessions'] > 4:
        raise SystemExit(1)

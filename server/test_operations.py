"""Real HTTP, sidecar isolation and content-free operational evidence."""
import io
import json
import pickle
import multiprocessing
import sqlite3
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.request import Request
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import app
from server import operations as ops
from server import model_queue
import test_usage as usage_tests


def observed_child(client,settings,pipe):
    try:pipe.send(client.complete_json('PRIVATE-SYNTHETIC-PROMPT','{}',settings))
    finally:pipe.close()


class SyntheticProvider(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        data=json.dumps(dict(choices=[dict(finish_reason='stop',message=dict(content='{"tasks":[]}'))])).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


class OperationsTests(unittest.TestCase):
    setUp=usage_tests.UsageTests.setUp
    tearDown=usage_tests.UsageTests.tearDown
    call=usage_tests.UsageTests.call

    def events(self):
        with ops.connection(app.DB) as db:return [dict(row) for row in db.execute('SELECT * FROM events')]

    def wait_for(self,predicate):
        for _ in range(100):
            if predicate():return
            time.sleep(.01)
        self.fail('operation record not observed')

    def test_failed_login_has_request_id_error_and_no_private_data(self):
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.call('/auth/login?secret=PRIVATE-QUERY',dict(email='PRIVATE-EMAIL@example.test',password='PRIVATE-PASSWORD'))[0],401)
            self.wait_for(lambda:any(r['operation']=='auth.login' for r in self.events()))
        login=next(r for r in self.events() if r['operation']=='auth.login')
        self.assertEqual((login['stage'],login['result'],login['code']),('http','failed','unauthorized'))
        self.assertEqual(len(login['request_id']),32)
        self.assertGreaterEqual(login['duration_ms'],0)
        self.assertNotIn('PRIVATE',json.dumps(self.events())+output.getvalue())
        self.assertNotIn('usage@example.test',json.dumps(self.events())+output.getvalue())

    def test_request_id_is_generated_not_trusted_from_client(self):
        req=Request(f'http://127.0.0.1:{self.server.server_port}/api/workspace',headers={'X-Request-ID':'PRIVATE-INJECTED'})
        with self.client.open(req) as response:
            rid=response.headers['X-Request-ID'];self.assertEqual(len(rid),32)
        self.wait_for(lambda:any(r['request_id']==rid for r in self.events()))
        self.assertNotIn('PRIVATE-INJECTED',json.dumps(self.events()))

    def test_producer_observations_validate_closed_schema_and_preserve_unknown_usage(self):
        event=dict(contract_version='producer-observation/0.1',stage='model_call',duration_ms=12.5,
                   result_code='ok',input_tokens=12,output_tokens=0,total_tokens=None)
        with ops.scope(app.DB,request_id='1'*32,run_id='2'*32,operation='sync'):
            self.assertTrue(ops.producer(event))
            self.assertTrue(ops.producer(dict(event,input_tokens=None)))
            for invalid in (dict(event,password='PRIVATE'),dict(event,input_tokens=True),dict(event,duration_ms=float('inf')),dict(event,total_tokens=-1)):
                self.assertFalse(ops.producer(invalid))
            ops.producer_stderr('PRIVATE-RAW-LOG\n'+json.dumps(dict(contract_version='producer-observation/0.1',stage='attachment_download',duration_ms=4,result_code='ok')))
        with app.connection() as db:summary=ops.summary(app.DB,db,time.time()-60,time.time()+1)
        self.assertEqual(summary['providerUsage']['observedCalls'],2)
        self.assertEqual(summary['providerUsage']['input_tokens'],dict(value=12,known=1,unknown=1))
        self.assertEqual(summary['providerUsage']['output_tokens'],dict(value=0,known=2,unknown=0))
        self.assertEqual(summary['providerUsage']['total_tokens'],dict(value=None,known=0,unknown=2))
        self.assertIn('attachment_download',[row['stage'] for row in summary['stages']])
        with ops.connection(app.DB) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM producer_events').fetchone()[0],3)
            self.assertNotIn('PRIVATE','\n'.join(db.iterdump()))

    def test_admin_only_separate_session_and_date_validation(self):
        self.assertEqual(self.call('/admin/operations')[0],401)
        with patch.dict(app.os.environ,NIUMA_ADMIN_USER_IDS=self.uid):
            self.assertEqual(self.call('/admin/auth/login',dict(email='usage@example.test',password='Usage-Test-2026!'))[0],200)
            status,result=self.call('/admin/operations')
            self.assertEqual(status,200,result)
            self.assertEqual(result['environment'],'test')
            self.assertEqual(result['retentionDays'],14)
            self.assertIsNone(result['resources'])
            self.assertIn('modelQueued',result['queue'])
            self.assertEqual(self.call('/admin/operations?start=2026-10-20&end=2026-01-01')[0],400)
            self.assertEqual(self.call('/admin/operations?start=invalid')[0],400)
        self.assertEqual(self.call('/auth/me')[1]['user']['id'],self.uid)

    def test_sidecar_failure_does_not_fail_business_request(self):
        with patch.object(ops,'connection',side_effect=sqlite3.OperationalError('PRIVATE-SECRET')):
            self.assertEqual(self.call('/workspace')[0],200)
        self.assertEqual(self.call('/auth/me')[0],200)

    def test_percentiles_error_counts_retention_and_ring_limit(self):
        now=time.time()
        with ops.scope(app.DB,request_id='a'*32,run_id='b'*32,operation='sync'):
            for index in range(1,21):ops.emit('http',index,result='failed' if index==20 else 'ok',code='rate_limited' if index==20 else 'ok')
            ops.emit('outcome',code='no_tasks',quantity=3)
            ops.emit('outcome',code='filtered',quantity=2)
            ops.emit('validation',result='failed',code='invalid_agent_output')
        with app.connection() as business:
            result=ops.summary(app.DB,business,now-1,time.time()+1)
        row=next(row for row in result['requests'] if row['operation']=='sync')
        self.assertEqual((row['count'],row['success'],row['failures'],row['p50Ms'],row['p95Ms']),(20,19,1,10.5,19))
        self.assertEqual(row['errors'],[dict(code='rate_limited',count=1)])
        self.assertEqual(result['runs']['no_tasks'],3)
        self.assertEqual(result['runs']['filtered'],2)
        self.assertEqual(result['stages'][-1]['failures'],1)
        with ops.connection(app.DB) as db:
            db.execute('INSERT INTO events(at,request_id,run_id,stage,operation,result,code,duration_ms,quantity) VALUES(?,?,?,?,?,?,?,?,?)',
                       (now-15*86400,'old','','http','sync','ok','ok',0,1))
            ops.prune(db,now)
            self.assertFalse(db.execute('SELECT 1 FROM events WHERE request_id="old"').fetchone())
            with patch.object(ops,'MAX_EVENTS',3):ops.prune(db,now)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],3)

    def test_validation_exception_does_not_log_exception_message(self):
        with ops.scope(app.DB,operation='recognition'):
            with self.assertRaises(ValueError):
                with ops.span('validation'):raise ValueError('PRIVATE-TASK-AND-KEY')
        row=next(r for r in self.events() if r['stage']=='validation' and r['operation']=='recognition')
        self.assertEqual(row['code'],'internal_error')
        self.assertNotIn('PRIVATE',json.dumps(self.events()))

    def test_provider_rate_limit_is_observed_before_runner_sanitizes_it(self):
        from ddl_text.adapters import AgentError
        try:
            try:raise HTTPError('https://provider.invalid/PRIVATE',429,'PRIVATE',{},None)
            except HTTPError:raise AgentError('agent_unavailable',True) from None
        except AgentError as error:
            self.assertEqual(ops.classify(error),'rate_limited')
        self.assertEqual(ops.classify(TimeoutError('PRIVATE')),'agent_timeout')

    def test_chat_observer_is_picklable_and_preserves_trusted_client_type(self):
        from ddl_text.chat_client import ChatClient
        with ops.scope(app.DB,request_id='c'*32,run_id='d'*32,operation='sync'):
            client=ops.observe_chat(ChatClient('SYNTHETIC-KEY'))
        self.assertIs(type(client),ChatClient)
        restored=pickle.loads(pickle.dumps(client))
        with patch.object(ChatClient,'complete_json',return_value='{"tasks":[]}'):
            self.assertEqual(restored.complete_json('SYNTHETIC-PROMPT','{}',None),'{"tasks":[]}')
        row=next(r for r in self.events() if r['stage']=='model_call')
        self.assertEqual(row['request_id'],'c'*32)
        self.assertEqual(row['run_id'],'d'*32)
        self.assertNotIn('SYNTHETIC-KEY',json.dumps(self.events()))

    def test_disposable_process_observes_model_call_without_recording_prompt_or_key(self):
        from ddl_text.chat_client import ChatClient
        from ddl_text.config import Settings
        provider=ThreadingHTTPServer(('127.0.0.1',0),SyntheticProvider)
        thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
        context=multiprocessing.get_context('spawn');receiver,sender=context.Pipe(duplex=False)
        with ops.scope(app.DB,request_id='e'*32,run_id='f'*32,operation='sync'):
            client=ops.observe_chat(ChatClient('PRIVATE-SYNTHETIC-KEY'))
        process=context.Process(target=observed_child,args=(client,Settings(endpoint=f'http://127.0.0.1:{provider.server_port}',timeout_seconds=3),sender))
        try:
            process.start();sender.close()
            self.assertTrue(receiver.poll(8));self.assertEqual(receiver.recv(),'{"tasks":[]}')
            process.join(3);self.assertFalse(process.is_alive());self.assertEqual(process.exitcode,0)
            row=next(r for r in self.events() if r['stage']=='model_call')
            self.assertEqual(row['request_id'],'e'*32)
            with ops.connection(app.DB) as db:
                observed=db.execute('SELECT * FROM producer_events').fetchone()
                self.assertEqual(observed['request_id'],'e'*32)
                self.assertIsNone(observed['total_tokens'])
            self.assertNotIn('PRIVATE',json.dumps(self.events()))
        finally:
            if process.is_alive():process.terminate();process.join(2)
            process.close();receiver.close();sender.close()
            provider.shutdown();provider.server_close();thread.join()

    def test_write_lock_wait_and_content_free_statements(self):
        locked=app.connection();db=locked.__enter__();db.execute('BEGIN IMMEDIATE')
        ready=threading.Event();done=threading.Event()
        def wait():
            with ops.scope(app.DB,operation='workspace.put'),app.connection() as current:
                ready.set();current.execute('BEGIN IMMEDIATE')
            done.set()
        worker=threading.Thread(target=wait);worker.start()
        try:
            self.assertTrue(ready.wait(2));time.sleep(.1)
            self.assertFalse(done.is_set())
        finally:locked.__exit__(None,None,None)
        worker.join(3);self.assertFalse(worker.is_alive())
        row=next(r for r in self.events() if r['stage']=='database_lock' and r['operation']=='workspace.put')
        self.assertGreaterEqual(row['duration_ms'],80)

    def test_rolled_back_business_outcomes_are_not_counted(self):
        with ops.scope(app.DB,operation='sync'):
            with self.assertRaises(RuntimeError):
                with app.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    ops.after_commit(db,'outcome',code='no_tasks')
                    raise RuntimeError('synthetic rollback')
            with app.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                ops.after_commit(db,'outcome',code='filtered',quantity=2)
            with app.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                ops.after_commit(db,'outcome',code='no_tasks')
                db.rollback()
        rows=[r for r in self.events() if r['stage']=='outcome']
        self.assertEqual([(r['code'],r['quantity']) for r in rows],[('filtered',2)])

    def test_queue_snapshot_tracks_waiting_age_and_removes_cancelled_ticket(self):
        queue=model_queue.ModelQueue(limit=1,wait=.05)
        self.assertTrue(queue.acquire())
        worker=threading.Thread(target=queue.acquire);worker.start()
        with queue.condition:self.assertTrue(queue.condition.wait_for(lambda:len(queue.waiters)==1,timeout=2))
        self.assertEqual(queue.snapshot()['queued'],1)
        worker.join(2)
        self.assertEqual(queue.snapshot()['queued'],0)
        self.assertEqual(queue.waiting_at,{})
        queue.release()


if __name__=='__main__':unittest.main()

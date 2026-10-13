import test from 'node:test';
import assert from 'node:assert/strict';
import {syncRun} from './sync-fixtures.js';
globalThis.window=new EventTarget();globalThis.location={hash:'#sources'};
const {session}=await import('../src/team-store.js');
const sync=await import('../src/source-sync-ui.js');
const response=value=>({ok:true,json:async()=>structuredClone(value)});
function reset(){sync.resetSourceSync();session.user={id:'synthetic'};sync.configureSourceSync({render(){}});}

test('refresh restores active progress without resending crawler or model calls',async()=>{
 reset();const paths=[],run=syncRun({state:'recognizing',states:['succeeded','running','queued'],added:1});
 globalThis.fetch=async(path,options)=>{paths.push([path,options.method]);return response({contract_version:'source-sync/0.1',runs:[run]});};
 await sync.refreshSourceSync();assert.equal(sync.syncIsActive(),true);assert.match(sync.sourceSyncPanel(),/已处理 1\/3 条/);
 sync.resetSourceSync();await sync.refreshSourceSync();assert.equal(paths.length,2);assert.ok(paths.every(([,method])=>method==='GET'));sync.resetSourceSync();
});
test('stop and retry call server operations; retry carries only consent and operation id',async()=>{
 reset();const sent=[];
 globalThis.fetch=async(path,options)=>{sent.push({path,body:JSON.parse(options.body)});return response(syncRun({state:'stopped',states:['interrupted']}));};
 await sync.sourceSyncClick({dataset:{sourceSyncAction:'stop',run:'run:one'}});
 await sync.sourceSyncClick({dataset:{sourceSyncAction:'retry',run:'run:one'}});
 assert.deepEqual(sent[0].body,{contract_version:'source-sync/0.1'});
 assert.equal(sent[1].body.consent,true);assert.equal(typeof sent[1].body.operation_id,'string');assert.equal(Object.hasOwn(sent[1].body,'records'),false);
});
test('network-uncertain starts reuse operation identity and do not auto-resubmit on render',async()=>{
 reset();const ids=[];
 globalThis.fetch=async(path,options)=>{ids.push(JSON.parse(options.body).operation_id);throw new Error('offline');};
 await sync.startSourceSync(['teaching_web'],true);sync.sourceSyncPanel();sync.sourceSyncPanel();assert.equal(ids.length,1);
 await sync.startSourceSync(['teaching_web'],true);assert.equal(ids[0],ids[1]);
});
test('late poll does not overwrite a newer stop response or another account',async()=>{
 reset();let release;
 globalThis.fetch=async(path,options)=>options.method==='GET'?new Promise(resolve=>{release=resolve;}):response(syncRun({state:'stopped',states:['interrupted']}));
 const polling=sync.refreshSourceSync();await sync.sourceSyncClick({dataset:{sourceSyncAction:'stop',run:'run:one'}});
 release(response({contract_version:'source-sync/0.1',runs:[syncRun({state:'recognizing',states:['running']})]}));await polling;
 assert.match(sync.sourceSyncPanel(),/已停止/);assert.equal(sync.syncIsActive(),false);
 const next=sync.refreshSourceSync();window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
 release(response({contract_version:'source-sync/0.1',runs:[syncRun()]}));await next;assert.equal(sync.sourceSyncPanel(),'');
});
test('missing source offers recollection and escapes untrusted titles',async()=>{
 reset();const run=syncRun({state:'failed',states:['incomplete']});run.records[0].label='<script>bad</script>';
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[run]});await sync.refreshSourceSync();
 const html=sync.sourceSyncPanel();assert.match(html,/data-source-sync-action="collect"/);assert.doesNotMatch(html,/data-source-sync-action="retry"/);assert.doesNotMatch(html,/<script>/);
});

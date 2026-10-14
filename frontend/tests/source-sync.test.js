import test from 'node:test';
import assert from 'node:assert/strict';
import {syncRun} from './sync-fixtures.js';
globalThis.window=new EventTarget();globalThis.location={hash:'#sources'};
const {session}=await import('../src/team-store.js');
const sync=await import('../src/source-sync-ui.js');
const response=value=>({ok:true,json:async()=>structuredClone(value)});
function reset(){sync.resetSourceSync();session.user={id:'synthetic'};sync.configureSourceSync({render(){}});}

test('unchanged progress does not rerender editable forms and completed retries suppress old actions',async()=>{
 reset();let renders=0;sync.configureSourceSync({render(){renders++;}});
 const old=syncRun({state:'failed',states:['failed']}),next=syncRun({id:'run:retry'});next.parent_run_id=old.run_id;
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[next,old]});
 await sync.refreshSourceSync();const count=renders;await sync.refreshSourceSync();assert.equal(renders,count);
 assert.match(sync.sourceSyncPanel(),/已发起后续重试/);assert.doesNotMatch(sync.sourceSyncPanel(),/data-source-sync-action="retry"/);
});

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

test('single retry keeps remaining failures visible and reports repaired history',async()=>{
 reset();const old=syncRun({state:'failed',states:['failed','failed','incomplete']}),next=syncRun({id:'run:retry'});next.parent_run_id=old.run_id;
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[next,old]});await sync.refreshSourceSync();
 let html=sync.sourceSyncPanel();assert.match(html,/1 条已恢复/);assert.match(html,/重试失败 1 条/);
 assert.match(html,/data-request="sync:1"/);assert.doesNotMatch(html,/data-request="sync:0"/);assert.doesNotMatch(html,/data-request="sync:2"/);
 assert.match(html,/重试模型不能补齐/);assert.match(html,/模型响应超时/);assert.equal(sync.syncOwnsRequest('sync:1'),true);
 let sent;
 globalThis.fetch=async(path,options)=>{sent=JSON.parse(options.body);return response(syncRun({id:'run:selected',state:'queued',states:['queued']}));};
 await sync.sourceSyncClick({dataset:{sourceSyncAction:'retry',run:old.run_id,request:'sync:1'}});
 assert.deepEqual(sent.request_ids,['sync:1']);assert.equal(sent.consent,true);
});

test('service configuration failures offer guidance without a futile retry button',async()=>{
 reset();const run=syncRun({state:'failed',states:['failed']});run.records[0].error={code:'model_configuration',message:'合成配置故障',retryable:false};
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[run]});await sync.refreshSourceSync();
 const html=sync.sourceSyncPanel();assert.match(html,/请联系管理员检查/);assert.doesNotMatch(html,/data-source-sync-action="retry"/);assert.match(html,/运行 run:one/);
});

test('progress network errors keep saved state and distinguish connection from recognition failure',async()=>{
 reset();const run=syncRun({state:'recognizing',states:['running']});
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[run]});await sync.refreshSourceSync();
 globalThis.fetch=async()=>{throw new Error('offline');};await sync.refreshSourceSync();
 assert.equal(sync.syncIsActive(),true);assert.match(sync.sourceSyncPanel(),/不代表识别失败/);assert.match(sync.sourceSyncPanel(),/停止本轮同步/);
});

test('actual recognition callback retries unchanged progress after workspace or candidate fetch fails',async()=>{
 const {createImportUI}=await import('../src/import-ui.js');
 for(const failure of ['workspace','candidates']){
  reset();let workspace=0,candidates=0,progress=0,fail=true;
  const run=syncRun({added:1});
  const ui=createImportUI({escapeHtml:s=>s,icon:()=>'',toast(){},render(){},isImportVisible:()=>true,isDemo:()=>false,refresh:async()=>{workspace++;if(fail&&failure==='workspace'){fail=false;throw new Error('temporary workspace failure');}}});
  globalThis.fetch=async path=>{
   if(path==='/api/sources/sync-runs'){progress++;return response({contract_version:'source-sync/0.1',runs:[run]});}
   assert.equal(path,'/api/recognition/batches');candidates++;
   if(fail&&failure==='candidates'){fail=false;throw new Error('temporary candidate failure');}
   return response([]);
  };
  await sync.refreshSourceSync();assert.match(sync.sourceSyncPanel(),/暂时无法读取进度/);
  await sync.sourceSyncClick({dataset:{sourceSyncAction:'refresh'}});
  assert.equal(workspace,2);assert.equal(candidates,failure==='workspace'?1:2);
  assert.doesNotMatch(sync.sourceSyncPanel(),/暂时无法读取进度/);
  await sync.refreshSourceSync();assert.equal(progress,3);assert.equal(workspace,2);
  ui.reset();
 }
});

test('older failures and expanded details remain accessible across polls, not account changes',async()=>{
 reset();const old=syncRun({id:'run:older',state:'failed',states:['failed']});old.records[0].request_id='older:source';old.records[0].label='旧故障';
 const runs=[syncRun({id:'run:a'}),syncRun({id:'run:b'}),syncRun({id:'run:c'}),old];
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs});await sync.refreshSourceSync();
 assert.doesNotMatch(sync.sourceSyncPanel(),/旧故障/);await sync.sourceSyncClick({dataset:{sourceSyncAction:'history'}});
 assert.match(sync.sourceSyncPanel(),/旧故障/);await sync.sourceSyncClick({dataset:{sourceSyncAction:'details',run:'run:older'}});
 await sync.refreshSourceSync();assert.match(sync.sourceSyncPanel(),/aria-expanded="true"/);
 sync.resetSourceSync();assert.equal(sync.syncOwnsRequest('older:source'),false);
});

test('stopped during collection offers recrawl and active work disables competing retries',async()=>{
 reset();const stopped=syncRun({state:'stopped',states:[]});
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[stopped]});await sync.refreshSourceSync();
 assert.match(sync.sourceSyncPanel(),/data-source-sync-action="collect"/);
 const active=syncRun({id:'run:active',state:'recognizing',states:['running']});active.records[0].request_id='active:source';
 const failed=syncRun({state:'failed',states:['failed']});
 globalThis.fetch=async()=>response({contract_version:'source-sync/0.1',runs:[active,failed]});await sync.refreshSourceSync();
 assert.match(sync.sourceSyncPanel(),/data-source-sync-action="retry"[^>]*disabled/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {setMaxListeners} from 'node:events';
import {syncRun} from './sync-fixtures.js';
globalThis.window=new EventTarget();setMaxListeners(0,window);
globalThis.CustomEvent ||= class extends Event {constructor(type,options){super(type);this.detail=options?.detail;}};
const store=await import('../src/team-store.js');
const {createImportUI}=await import('../src/import-ui.js');
const response=value=>({ok:true,json:async()=>structuredClone(value)});
async function setup(t,intercept=()=>{}) {
 let tasks=[],runs=[],version=0,state,account='A';const calls=[];
 let schedule={contract_version:'source-sync/0.1',enabled:false,available:true,interval_minutes:180,revision:0};
 globalThis.fetch=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):undefined,method=options.method||'GET';calls.push({path,body,method});
  const result=await intercept(path,body,method);if(result)return result;
  if(path==='/api/auth/me')return response({user:{id:account,name:account}});
  if(path==='/api/workspace')return response({tasks:{version,value:tasks}});
  if(path==='/api/data/tasks'){tasks=body.value;version++;return response({version});}
  if(path==='/api/sources/credentials')return response({configured:true,username:account+'-campus'});
  if(path==='/api/recognition/batches') {assert.equal(method,'GET');return response([]);}
  if(path==='/api/sources/sync-schedule'){
   if(method==='PUT')schedule={...schedule,...body,revision:schedule.revision+1};
   return response(schedule);
  }
  if(path==='/api/sources/sync-runs'){
   if(method==='GET')return response({contract_version:'source-sync/0.1',runs});
   assert.equal(body.consent,true);assert.deepEqual(body.sources,['teaching_web']);
   runs=[syncRun({state:'recognizing',states:['running']})];return response(runs[0]);
  }
  if(path.endsWith('/stop')){runs=[syncRun({state:'stopped',states:['interrupted']})];return response(runs[0]);}
  throw new Error(path);
 };
 state=await store.initSession();
 const ui=createImportUI({escapeHtml:s=>String(s??''),icon:()=>'',toast(){},render(){},openDialog(){},closeDialog(){},isDemo:()=>false,isImportVisible:()=>true,getState:()=>state,
  refresh:async()=>{state=await store.refreshStore();}});
 t.after(()=>ui.reset());await ui.load();await ui.loadSourceSync();await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));
 return {ui,calls,get state(){return state;},complete(){tasks=[{id:'one',title:'后端已自动加入',content_type:'notice',status:'not_started',due:null,plans:[],subtasks:[]}];version++;runs=[syncRun({added:1})];},switchAccount(){account='B';window.dispatchEvent(new Event('niuma:account-reset'));}};
}
test('one explicit sync action starts one durable run, refreshes server tasks, and restores progress on return',async t=>{
 const f=await setup(t);await f.ui.click({dataset:{importAction:'sync'}});
 assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
 assert.match(f.ui.render(),/同步中|正在识别/);
 f.complete();await f.ui.loadSourceSync();assert.equal(f.state.tasks[0].content_type,'notice');
 assert.match(f.ui.render(),/新增 1 项/);
 const writes=f.calls.filter(c=>c.method==='POST').length;await f.ui.load();await new Promise(r=>setImmediate(r));await f.ui.loadSourceSync();
 assert.equal(f.calls.filter(c=>c.method==='POST').length,writes);
 assert.ok(f.calls.every(c=>c.path!=='/api/sources/sync'&&!c.path.endsWith('/resolve')));
});
test('stop calls persistent server endpoint; account reset removes progress and selected consent',async t=>{
 const f=await setup(t);await f.ui.click({dataset:{importAction:'sync'}});
 await f.ui.click({dataset:{importAction:'sync'}});
 assert.match(f.ui.render(),/已停止/);assert.ok(f.calls.some(c=>c.path.endsWith('/stop')));
 f.switchAccount();assert.doesNotMatch(f.ui.render(),/run:one|A-campus/);
});
test('queued local edits are saved before starting a backend run',async t=>{
 let release,entered;const gate=new Promise(r=>release=r),ready=new Promise(r=>entered=r);
 const f=await setup(t,async(path)=>{if(path==='/api/data/tasks'){entered();await gate;return response({version:1});}});
 f.state.tasks.push({id:'edited',title:'本地编辑',status:'todo',due:''});store.saveStore(f.state);await ready;
 const start=f.ui.click({dataset:{importAction:'sync'}});
 await new Promise(r=>setImmediate(r));assert.equal(f.calls.some(c=>c.path==='/api/sources/sync-runs'&&c.method==='POST'),false);
 release();await start;assert.equal(f.calls.filter(c=>c.path==='/api/sources/sync-runs'&&c.method==='POST').length,1);
});

test('the shared action starts recurrence plus one run; stopping disables recurrence before stopping that run',async t=>{
 const f=await setup(t);
 f.ui.input({target:{dataset:{syncScheduleField:'interval'},value:'15'}});
 f.ui.input({target:{dataset:{syncScheduleField:'consent'},checked:true}});
 await f.ui.click({dataset:{importAction:'sync'}});
 const configured=f.calls.findIndex(c=>c.method==='PUT'&&c.path.endsWith('sync-schedule')&&c.body.enabled);
 const started=f.calls.findIndex(c=>c.method==='POST'&&c.path.endsWith('sync-runs'));
 assert.ok(configured>=0&&started>configured);
 await f.ui.click({dataset:{importAction:'sync'}});
 const disabled=f.calls.findIndex(c=>c.method==='PUT'&&c.body.enabled===false);
 const stopped=f.calls.findIndex(c=>c.path.endsWith('/stop'));
 assert.ok(disabled>started&&stopped>disabled);
 assert.match(f.ui.render(),/value="once" selected/);
 assert.equal(f.calls.filter(c=>c.method==='POST'&&c.path.endsWith('sync-runs')).length,1);
});

test('failure to disable recurrence still stops the current run and explains that future runs remain enabled',async t=>{
 const f=await setup(t,(path,body,method)=>{
  if(path.endsWith('sync-schedule')&&method==='PUT'&&!body.enabled)return {ok:false,status:409,json:async()=>({error:'conflict'})};
 });
 f.ui.input({target:{dataset:{syncScheduleField:'interval'},value:'15'}});
 f.ui.input({target:{dataset:{syncScheduleField:'consent'},checked:true}});
 await f.ui.click({dataset:{importAction:'sync'}});
 await f.ui.click({dataset:{importAction:'sync'}});
 assert.ok(f.calls.some(c=>c.path.endsWith('/stop')));
 assert.match(f.ui.render(),/定时同步尚未停止/);
});

test('the primary action stays disabled through both stop requests and repeated clicks cannot duplicate them',async t=>{
 let releaseSchedule,releaseStop,enteredSchedule,enteredStop;
 const scheduleGate=new Promise(r=>releaseSchedule=r),stopGate=new Promise(r=>releaseStop=r);
 const scheduleReady=new Promise(r=>enteredSchedule=r),stopReady=new Promise(r=>enteredStop=r);
 t.after(()=>{releaseSchedule();releaseStop();});
 const f=await setup(t,async(path,body,method)=>{
  if(path.endsWith('sync-schedule')&&method==='PUT'&&!body.enabled){enteredSchedule();await scheduleGate;}
  if(path.endsWith('/stop')){enteredStop();await stopGate;}
 });
 f.ui.input({target:{dataset:{syncScheduleField:'interval'},value:'15'}});
 f.ui.input({target:{dataset:{syncScheduleField:'consent'},checked:true}});
 await f.ui.click({dataset:{importAction:'sync'}});
 const stopping=f.ui.click({dataset:{importAction:'sync'}});await scheduleReady;
 assert.match(f.ui.render(),/data-import-action="sync" disabled>正在停止…/);
 await f.ui.click({dataset:{importAction:'sync'}});
 releaseSchedule();await stopReady;
 assert.match(f.ui.render(),/data-import-action="sync" disabled>正在停止…/);
 await f.ui.click({dataset:{importAction:'sync'}});
 releaseStop();await stopping;
 assert.equal(f.calls.filter(c=>c.method==='PUT'&&!c.body.enabled).length,1);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/stop')).length,1);
 assert.match(f.ui.render(),/data-import-action="sync" >开始识别/);
 assert.doesNotMatch(f.ui.render(),/data-source-sync-action="stop"/);
});

test('a failed stop restores the enabled primary stop action and retries without starting another import',async t=>{
 let failed=false;
 const f=await setup(t,path=>{
  if(path.endsWith('/stop')&&!failed){failed=true;return {ok:false,status:503,json:async()=>({error:'temporary failure'})};}
 });
 await f.ui.click({dataset:{importAction:'sync'}});
 await f.ui.click({dataset:{importAction:'sync'}});
 assert.match(f.ui.render(),/停止导入未完成/);
 assert.match(f.ui.render(),/data-import-action="sync" >.*停止导入/);
 await f.ui.click({dataset:{importAction:'sync'}});
 assert.match(f.ui.render(),/已停止导入/);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/stop')).length,2);
 assert.equal(f.calls.filter(c=>c.path.endsWith('sync-runs')&&c.method==='POST').length,1);
});

test('switching accounts while disabling the schedule cannot stop the new account run',async t=>{
 let release,entered;
 const gate=new Promise(r=>release=r),ready=new Promise(r=>entered=r);t.after(release);
 const f=await setup(t,async(path,body,method)=>{
  if(path.endsWith('sync-schedule')&&method==='PUT'&&!body.enabled){entered();await gate;}
 });
 f.ui.input({target:{dataset:{syncScheduleField:'interval'},value:'15'}});
 f.ui.input({target:{dataset:{syncScheduleField:'consent'},checked:true}});
 await f.ui.click({dataset:{importAction:'sync'}});
 const stopping=f.ui.click({dataset:{importAction:'sync'}});await ready;
 f.switchAccount();await f.ui.load();await f.ui.loadSourceSync();
 release();await stopping;
 assert.equal(f.calls.some(c=>c.path.endsWith('/stop')),false);
 assert.doesNotMatch(f.ui.render(),/正在停止…|A-campus/);
});

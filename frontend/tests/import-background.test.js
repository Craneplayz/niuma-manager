import test from 'node:test';
import assert from 'node:assert/strict';
import {setMaxListeners} from 'node:events';
import {syncRun} from './sync-fixtures.js';
globalThis.window=new EventTarget();setMaxListeners(0,window);
globalThis.CustomEvent ||= class extends Event {constructor(type,options){super(type);this.detail=options?.detail;}};
const store=await import('../src/team-store.js');
const {createImportUI}=await import('../src/import-ui.js');
const {refreshSourceSync}=await import('../src/source-sync-ui.js');
const response=value=>({ok:true,json:async()=>structuredClone(value)});
async function setup(t,intercept=()=>{}) {
 let tasks=[],runs=[],version=0,state,account='A';const calls=[],navigation=[];
 globalThis.fetch=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):undefined,method=options.method||'GET';calls.push({path,body,method});
  const result=await intercept(path,body,method);if(result)return result;
  if(path==='/api/auth/me')return response({user:{id:account,name:account}});
  if(path==='/api/workspace')return response({tasks:{version,value:tasks}});
  if(path==='/api/data/tasks'){tasks=body.value;version++;return response({version});}
  if(path==='/api/sources/credentials')return response({configured:true,username:account+'-campus'});
  if(path==='/api/recognition/batches') {assert.equal(method,'GET');return response([]);}
  if(path==='/api/sources/sync-schedule')return response({contract_version:'source-sync/0.1',enabled:false,available:true,interval_minutes:180,revision:0});
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
  navigateTasks:()=>navigation.push('tasks'),showReview:()=>navigation.push('review'),refresh:async()=>{state=await store.refreshStore();}});
 t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'teaching'}});await refreshSourceSync();
 return {ui,calls,navigation,get state(){return state;},complete(){tasks=[{id:'one',title:'后端已自动加入',content_type:'notice',status:'not_started',due:null,plans:[],subtasks:[]}];version++;runs=[syncRun({added:1})];},switchAccount(){account='B';window.dispatchEvent(new Event('niuma:account-reset'));}};
}
test('new UI starts one durable run only after consent, refreshes server tasks, and restores progress on return',async t=>{
 const f=await setup(t);await f.ui.click({dataset:{importAction:'sync'}});
 assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
 f.ui.input({target:{id:'teaching-sync-consent',checked:true}});await f.ui.click({dataset:{importAction:'sync'}});
 assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
 assert.match(f.ui.render(),/后台同步中|正在并发识别/);
 f.complete();await refreshSourceSync();assert.equal(f.state.tasks[0].content_type,'notice');
 assert.match(f.ui.render(),/新增 1 项/);
 const writes=f.calls.filter(c=>c.method==='POST').length;await f.ui.load();await refreshSourceSync();
 assert.equal(f.calls.filter(c=>c.method==='POST').length,writes);
 assert.ok(f.calls.every(c=>c.path!=='/api/sources/sync'&&!c.path.endsWith('/resolve')));
 await f.ui.click({dataset:{sourceSyncAction:'tasks'}});await f.ui.click({dataset:{sourceSyncAction:'review'}});
 assert.deepEqual(f.navigation,['tasks','review']);
});
test('stop calls persistent server endpoint; account reset removes progress and selected consent',async t=>{
 const f=await setup(t);f.ui.input({target:{id:'teaching-sync-consent',checked:true}});await f.ui.click({dataset:{importAction:'sync'}});
 await f.ui.click({dataset:{sourceSyncAction:'stop',run:'run:one'}});
 assert.match(f.ui.render(),/已停止/);assert.ok(f.calls.some(c=>c.path.endsWith('/stop')));
 f.switchAccount();assert.doesNotMatch(f.ui.render(),/run:one|A-campus/);
});
test('queued local edits are saved before starting a backend run',async t=>{
 let release,entered;const gate=new Promise(r=>release=r),ready=new Promise(r=>entered=r);
 const f=await setup(t,async(path)=>{if(path==='/api/data/tasks'){entered();await gate;return response({version:1});}});
 f.state.tasks.push({id:'edited',title:'本地编辑',status:'todo',due:''});store.saveStore(f.state);await ready;
 f.ui.input({target:{id:'teaching-sync-consent',checked:true}});const start=f.ui.click({dataset:{importAction:'sync'}});
 await new Promise(r=>setImmediate(r));assert.equal(f.calls.some(c=>c.path==='/api/sources/sync-runs'&&c.method==='POST'),false);
 release();await start;assert.equal(f.calls.filter(c=>c.path==='/api/sources/sync-runs'&&c.method==='POST').length,1);
});

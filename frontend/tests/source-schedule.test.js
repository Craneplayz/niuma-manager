import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();globalThis.location={hash:'#sources'};
const {session}=await import('../src/team-store.js');
const schedule=await import('../src/source-schedule-ui.js');
const fixture=(extra={})=>({contract_version:'source-sync/0.1',enabled:false,available:true,revision:0,interval_minutes:180,sources:['teaching_web'],next_run_at:null,last_run_id:null,error:null,...extra});
const response=value=>({ok:true,json:async()=>value});
const field=(key,value)=>schedule.syncScheduleInput({target:{dataset:{syncScheduleField:key},value,checked:value}});
const click=()=>schedule.syncScheduleClick({dataset:{syncScheduleAction:'save'}});
function reset(){session.user={id:'synthetic'};schedule.resetSyncSchedule();schedule.configureSyncSchedule({render(){}});}

test('schedule sends explicit selected source, interval, consent and current revision',async()=>{
 reset();let sent;
 globalThis.fetch=async(path,options)=>{
  if(options.method==='GET')return response(fixture());
  sent=JSON.parse(options.body);return response(fixture({...sent,revision:1,next_run_at:1800000000}));
 };
 await schedule.refreshSyncSchedule();field('enabled',true);field('minutes','60');
 await click();assert.equal(sent,undefined);assert.match(schedule.syncSchedulePanel(),/请选定教学网/);
 field('consent',true);field('teaching',false);await click();assert.equal(sent,undefined);
 field('teaching',true);await click();assert.deepEqual(sent,{contract_version:'source-sync/0.1',revision:0,enabled:true,interval_minutes:60,sources:['teaching_web'],consent:true});
 assert.match(schedule.syncSchedulePanel(),/下次同步/);
});

test('rerenders preserve unsaved draft while network conflict asks for refresh',async()=>{
 reset();globalThis.fetch=async()=>response(fixture());await schedule.refreshSyncSchedule();
 field('minutes','45');field('enabled',true);field('consent',true);
 assert.match(schedule.syncSchedulePanel(),/value="45"/);assert.match(schedule.syncSchedulePanel(),/value="45"/);
 globalThis.fetch=async()=>({ok:false,status:409,json:async()=>({error:'定时设置已变化，请刷新'})});
 await click();assert.match(schedule.syncSchedulePanel(),/定时设置已变化/);assert.match(schedule.syncSchedulePanel(),/value="45"/);
});

test('disabled environment permits switching off existing schedule without new consent',async()=>{
 reset();let sent;
 globalThis.fetch=async(path,options)=>{
  if(options.method==='GET')return response(fixture({enabled:true,available:false,revision:2}));
  sent=JSON.parse(options.body);return response(fixture({revision:3,available:false}));
 };
 await schedule.refreshSyncSchedule();assert.match(schedule.syncSchedulePanel(),/当前环境未开启/);
 field('enabled',false);await click();assert.equal(sent.enabled,false);assert.equal(sent.consent,false);
});

test('account reset drops delayed settings and never enables another account',async()=>{
 reset();let release;globalThis.fetch=()=>new Promise(resolve=>{release=resolve;});
 const pending=schedule.refreshSyncSchedule();window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
 release(response(fixture({enabled:true})));await pending;assert.equal(schedule.syncSchedulePanel(),'');
});

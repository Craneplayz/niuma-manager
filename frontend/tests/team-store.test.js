import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window={addEventListener(){},dispatchEvent(){}};
globalThis.CustomEvent ||= class {constructor(type){this.type=type;}};
const {initSession,saveStore,flushStore}=await import('../src/team-store.js');

test('collaborator task edits preserve team metadata, plans and cancelled history',async()=>{
 const original=[
  {id:'active',title:'报告',description:'原文说明',course:'课程',category:'课程作业',
   priority:'high',status:'not_started',due:{date:'2026-09-30',precision:'date'},
   source:'识别',sourceRequirements:['提交 PDF'],recognitionHistory:[{candidateId:'c1'}],
   subtasks:[{id:'step',title:'起草',status:'pending'}],
   plans:[{id:'plan',title:'写作',startAt:'2026-09-28T19:00:00+08:00',endAt:'2026-09-28T20:00:00+08:00',origin:'ai'}]},
  {id:'cancelled',title:'取消的任务',status:'cancelled',subtasks:[],plans:[],due:null}
 ];
 const writes=[];
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return {ok:true,json:async()=>({user:{id:'u',name:'测试',email:'u@test.local'}})};
  if(path==='/api/workspace')return {ok:true,json:async()=>({tasks:{value:original,version:1}})};
  if(path==='/api/data/tasks'){
   const body=JSON.parse(options.body);writes.push(body);
   return {ok:true,json:async()=>({version:2})};
  }
  throw new Error('unexpected request '+path);
 };
 const state=await initSession();
 assert.equal(state.tasks.length,1);
 state.tasks[0].subtasks[0].done=true;
 state.tasks[0].due='2026-10-01';
 state.tasks[0].dueTime='23:59';
 assert.equal(saveStore(state),true);
 await flushStore();
 assert.equal(writes.length,1);
 const edited=writes[0].value.find(t=>t.id==='active');
 const cancelled=writes[0].value.find(t=>t.id==='cancelled');
 assert.equal(edited.due.time,'23:59');
 assert.equal(edited.subtasks[0].status,'completed');
 assert.deepEqual(edited.sourceRequirements,['提交 PDF']);
 assert.deepEqual(edited.recognitionHistory,[{candidateId:'c1'}]);
 assert.deepEqual(edited.plans,original[0].plans);
 assert.deepEqual(cancelled,original[1]);
});

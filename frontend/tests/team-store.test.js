import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window={addEventListener(){},dispatchEvent(){}};
globalThis.CustomEvent ||= class {constructor(type){this.type=type;}};
const {initSession,saveStore,deleteStoredTasks,flushStore}=await import('../src/team-store.js');

test('collaborator task edits preserve team metadata, plans and cancelled history',async()=>{
 const original=[
  {id:'active',title:'报告',description:'原文说明',course:'课程',category:'课程作业',
   priority:'high',status:'not_started',due:{date:'2026-09-30',precision:'date'},
   source:'识别',sourceRequirements:['提交 PDF'],recognitionHistory:[{candidateId:'c1'}],
   subtasks:[{id:'step',title:'起草',status:'pending',sourceRequirement:'保留子任务来源'}],
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
 state.tasks[0].subtasks[0].title='修改后的步骤标题';
 state.tasks[0].due='2026-10-01';
 state.tasks[0].dueTime='23:59';
 assert.equal(saveStore(state),true);
 await flushStore();
 assert.equal(writes.length,1);
 const edited=writes[0].value.find(t=>t.id==='active');
 const cancelled=writes[0].value.find(t=>t.id==='cancelled');
 assert.equal(edited.due.time,'23:59');
 assert.equal(edited.subtasks[0].status,'completed');
 assert.equal(edited.subtasks[0].id,'step');
 assert.equal(edited.subtasks[0].title,'修改后的步骤标题');
 assert.equal(edited.subtasks[0].sourceRequirement,'保留子任务来源');
 assert.deepEqual(edited.sourceRequirements,['提交 PDF']);
 assert.deepEqual(edited.recognitionHistory,[{candidateId:'c1'}]);
 assert.deepEqual(edited.plans,original[0].plans);
 assert.deepEqual(cancelled,original[1]);
});

test('planning exclusion and completion dates survive saving, reloading and reopening',async()=>{
 let tasks=[{id:'task',title:'测试任务',status:'not_started',due:null,plans:[],subtasks:[]}],version=1;
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return {ok:true,json:async()=>({user:{id:'u',name:'测试'}})};
  if(path==='/api/workspace')return {ok:true,json:async()=>({tasks:{value:structuredClone(tasks),version}})};
  if(path==='/api/data/tasks'){
   const body=JSON.parse(options.body);assert.equal(body.version,version);
   tasks=body.value;return {ok:true,json:async()=>({version:++version})};
  }
  throw new Error('unexpected request '+path);
 };
 let state=await initSession();
 state.tasks[0].planningExcluded=true;
 state.tasks[0].status='done';
 saveStore(state);await flushStore();
 state=await initSession();
 assert.equal(state.tasks[0].planningExcluded,true);
 assert.match(state.tasks[0].completedAt,/^\d{4}-\d{2}-\d{2}$/);
 state.tasks[0].planningExcluded=false;state.tasks[0].status='todo';
 saveStore(state);await flushStore();
 state=await initSession();
 assert.equal(state.tasks[0].planningExcluded,false);
 assert.equal(state.tasks[0].completedAt,'');
 state.tasks[0].status='done';state.tasks[0].completedAt='2026-09-20';
 saveStore(state);await flushStore();
 assert.equal((await initSession()).tasks[0].completedAt,'2026-09-20');
});

test('UTC plans display at the same Beijing time as offset timestamps',async()=>{
 const plan={id:'p',title:'跨日计划',startAt:'2026-09-25T17:30:00Z',endAt:'2026-09-25T18:30:00Z'};
 globalThis.fetch=async path=>({ok:true,json:async()=>path==='/api/auth/me'?{user:{id:'u',name:'测试'}}:
  {tasks:{version:1,value:[{id:'t',title:'任务',status:'not_started',due:null,subtasks:[],plans:[plan]}]}}});
 const state=await initSession();
 assert.equal(state.schedule[0].date,'2026-09-26');
 assert.equal(state.schedule[0].start,1.5);
 assert.equal(state.schedule[0].hours,1);
});

test('permanent task deletion uses the server delete endpoint and cannot return in later saves',async()=>{
 let tasks=[
  {id:'delete',title:'永久删除',status:'completed',due:null,subtasks:[],plans:[{id:'p',title:'计划',startAt:'2026-10-07T10:00:00+08:00',endAt:'2026-10-07T11:00:00+08:00'}]},
  {id:'keep',title:'保留',status:'not_started',dependsOn:'delete',due:null,subtasks:[],plans:[]}
 ],version=3,deletedRequest,saveRequest;
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return {ok:true,json:async()=>({user:{id:'u',name:'测试'}})};
  if(path==='/api/workspace')return {ok:true,json:async()=>({tasks:{value:structuredClone(tasks),version},feedback:{value:[{taskId:'delete',actualMinutes:30}],version:1}})};
  if(path==='/api/data/tasks'&&options.method==='DELETE'){
   deletedRequest=JSON.parse(options.body);tasks=[{...tasks[1],dependsOn:''}];version++;
   return {ok:true,json:async()=>({deletedIds:['delete'],version,feedback:[],feedbackVersion:2})};
  }
  if(path==='/api/data/tasks'){
   saveRequest=JSON.parse(options.body);tasks=saveRequest.value;version++;
   return {ok:true,json:async()=>({version})};
  }
  throw new Error('unexpected request '+path);
 };
 const state=await initSession();await deleteStoredTasks(['delete']);
 assert.deepEqual(deletedRequest,{ids:['delete'],version:3});
 state.tasks=state.tasks.filter(task=>task.id!=='delete');state.tasks[0].title='仍然保留';state.tasks[0].dependsOn='';
 saveStore(state);await flushStore();
 assert.deepEqual(saveRequest.value.map(task=>task.id),['keep']);
 assert.equal(saveRequest.value[0].title,'仍然保留');
});

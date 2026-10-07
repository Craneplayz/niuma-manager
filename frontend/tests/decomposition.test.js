import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.window=new EventTarget();
globalThis.CustomEvent ||= class extends Event {constructor(type){super(type);}};
const store=await import('../src/team-store.js');
const decomposition=await import('../src/team-decomposition-ui.js');

const examples=JSON.parse(await readFile(new URL('../../contracts/task-decomposition/examples-v0.1.json',import.meta.url)));
const response=value=>({ok:true,json:async()=>structuredClone(value)});

test('AI decomposition asks questions, previews steps, and only writes after confirmation',async()=>{
 let workspace={tasks:{version:1,value:[{
  id:'project',title:'完成课程项目',description:'实现任务页并准备展示',course:'软件工程',
  status:'not_started',due:null,plans:[],subtasks:[]
 }]}};
 const calls=[];
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return response({user:{id:'u',name:'测试',email:'u@test.local'}});
  if(path==='/api/workspace')return response(workspace);
  const body=JSON.parse(options.body);calls.push({path,body});
  if(path==='/api/decomposition/proposals'&&calls.filter(call=>call.path===path).length===1)return response(examples.clarification.response);
  if(path==='/api/decomposition/proposals')return response(examples.ready.response);
  if(path==='/api/decomposition/apply'){
   workspace={tasks:{version:2,value:[{...workspace.tasks.value[0],estimatedMinutes:120,
    subtasks:[{id:'s1',title:'完成任务页',status:'pending'},{id:'s2',title:'制作演示文稿',status:'pending'}]}]}};
   return response({value:workspace.tasks.value,version:2});
  }
  throw new Error('unexpected request '+path);
 };
 let state=await store.initSession(),renders=0,message='';
 decomposition.configureDecomposition({
  getState:()=>state,render(){renders++;},toast:value=>{message=value;},requireLogin(){throw new Error('unexpected login');},
  refresh:async()=>{state=await store.refreshStore();}
 });
 assert.match(decomposition.decompositionPage(),/AI 任务拆解/);
 decomposition.decompositionClick({dataset:{decompositionAction:'select',taskId:'project'}});
 assert.match(decomposition.decompositionPage(),/开始 AI 拆解/);
 decomposition.decompositionClick({dataset:{decompositionAction:'start'}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(decomposition.decompositionPage(),/目前完成到哪一步/);

 const handled=decomposition.decompositionSubmit({
  id:'decomposition-questions-form',querySelectorAll:()=>[{value:'登录已完成，任务页尚未开始'}]
 });
 assert.equal(handled,true);
 await new Promise(resolve=>setTimeout(resolve,0));
 const preview=decomposition.decompositionPage();
 assert.match(preview,/完成任务页/);
 assert.match(preview,/约 120 分钟/);
 assert.equal(calls[1].body.conversation[1].role,'user');
 assert.equal(state.tasks[0].subtasks.length,0);

 decomposition.decompositionClick({dataset:{decompositionAction:'apply',requestId:examples.ready.response.requestId}});
 await new Promise(resolve=>setTimeout(resolve,0));
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.deepEqual(calls.at(-1).body,examples.apply.request);
 assert.equal(state.tasks[0].subtasks.length,2);
 assert.match(decomposition.decompositionPage(),/步骤已经写入任务/);
 assert.match(message,/已写入任务/);
 assert.ok(renders>=5);
});

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const click=(action,taskId,requestId)=>decomposition.decompositionClick({dataset:{decompositionAction:action,taskId,requestId}});
async function deferredSetup(){
 decomposition.resetDecomposition();
 let state={tasks:['project','B'].map(id=>({id,title:id==='project'?'A 的任务':'B 的任务',status:'todo',subtasks:[],due:'',notes:''}))};
 store.session.user={id:'u'};
 const pending=[],calls=[],toasts=[];
 globalThis.fetch=async(path,options={})=>{
  calls.push({path,body:options.body?JSON.parse(options.body):null,signal:options.signal});
  if(path==='/api/decomposition/proposals'||path==='/api/decomposition/apply')return new Promise(resolve=>pending.push({path,resolve:value=>resolve(response(value))}));
  throw new Error('unexpected request '+path);
 };
 decomposition.configureDecomposition({getState:()=>state,render(){},toast:value=>toasts.push(value),refresh:async()=>{}});
 return {pending,calls,toasts};
}

test('switching A to B aborts A and its late result cannot display or clear B waiting state',async()=>{
 const {pending,calls}=await deferredSetup();
 click('select','project');click('start');await tick();
 assert.equal(calls[0].body.taskId,'project');
 click('select','B');assert.equal(calls[0].signal.aborted,true);
 click('start');await tick();
 assert.equal(calls[1].body.taskId,'B');
 pending[0].resolve(examples.ready.response);await tick();
 assert.match(decomposition.decompositionPage(),/正在理解任务/);
 assert.doesNotMatch(decomposition.decompositionPage(),/AI 拆解预览/);
 pending[1].resolve({...examples.ready.response,taskId:'B',requestId:'B-proposal',plan:{...examples.ready.response.plan,goal:'B 的方案'}});await tick();
 assert.match(decomposition.decompositionPage(),/B 的方案/);
 click('apply',undefined,examples.ready.response.requestId);await tick();
 assert.equal(calls.filter(call=>call.path.endsWith('/apply')).length,0);
 click('apply',undefined,'B-proposal');await tick();
 assert.deepEqual(calls.at(-1).body,{requestId:'B-proposal',taskId:'B'});
 pending[2].resolve({});await tick();
});

test('resetting and reselecting the same task discards the old request by identity',async()=>{
 const {pending}=await deferredSetup();
 click('select','project');click('start');await tick();
 click('reset');click('select','project');click('start');await tick();
 pending[0].resolve(examples.ready.response);await tick();
 assert.match(decomposition.decompositionPage(),/正在理解任务/);
 pending[1].resolve(examples.clarification.response);await tick();
 assert.match(decomposition.decompositionPage(),/目前完成到哪一步/);
});

test('switching before pending local saves finish never sends B as the target of A request',async()=>{
 const {calls}=await deferredSetup();
 click('select','project');click('start');click('select','B');await tick();
 assert.equal(calls.length,0);
 assert.match(decomposition.decompositionPage(),/B 的任务/);
 assert.doesNotMatch(decomposition.decompositionPage(),/正在理解任务/);
});

test('mismatched response task IDs are rejected before preview or apply',async()=>{
 const {pending,calls}=await deferredSetup();
 click('select','project');click('start');await tick();
 pending[0].resolve({...examples.ready.response,taskId:'B'});await tick();
 assert.match(decomposition.decompositionPage(),/拆解方案与当前任务不匹配/);
 click('apply',undefined,examples.ready.response.requestId);await tick();
 assert.equal(calls.length,1);
});

test('switching during confirmed apply discards A completion feedback on B',async()=>{
 const {pending,toasts}=await deferredSetup();
 click('select','project');click('start');await tick();
 pending[0].resolve(examples.ready.response);await tick();
 click('apply',undefined,examples.ready.response.requestId);await tick();
 click('select','B');pending[1].resolve({});await tick();
 assert.deepEqual(toasts,[]);
 assert.doesNotMatch(decomposition.decompositionPage(),/步骤已经写入任务/);
});

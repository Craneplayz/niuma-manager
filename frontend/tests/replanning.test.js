import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const examples=JSON.parse(await readFile(new URL('../../contracts/task-planning/api-examples.json',import.meta.url)));
globalThis.window=new EventTarget();globalThis.document=new EventTarget();
globalThis.CustomEvent ||= class extends Event{constructor(type){super(type);}};
const store=await import('../src/team-store.js');
const planner=await import('../src/team-planner-ui.js');
let workspace,state,calls,waiter;
const reply=value=>({ok:true,json:async()=>structuredClone(value)});
async function setup(){
 workspace={tasks:{version:1,value:[{id:'scheduled',title:'已有安排',status:'not_started',subtasks:[],due:{date:'2026-10-10'},plans:[{id:'old',title:'学习',startAt:'2026-10-08T19:00:00+08:00',endAt:'2026-10-08T20:00:00+08:00'}]},{id:'new',title:'新任务',status:'not_started',subtasks:[],due:{date:'2026-10-10'},plans:[]}]}};
 calls=[];waiter=null;
 globalThis.fetch=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
  if(path==='/api/auth/me')return reply({user:{id:'u',name:'测试'}});
  if(path==='/api/workspace')return reply(workspace);
  if(path==='/api/data/availability')return reply({version:1});
  if(path==='/api/planning/proposals')return waiter?waiter.promise:reply({requestId:'proposal',estimates:{scheduled:60},replannedTaskIds:['scheduled'],blocks:[{taskId:'scheduled',title:'新时段',startAt:'2026-10-09T19:00:00+08:00',endAt:'2026-10-09T20:00:00+08:00'}],warnings:[]});
  if(path==='/api/planning/apply'){workspace.tasks.value[0].plans=[{id:'new-plan',title:'新时段',startAt:'2026-10-09T19:00:00+08:00',endAt:'2026-10-09T20:00:00+08:00'}];workspace.tasks.version++;return reply({});}
  throw new Error('unexpected '+path);
 };
 state=await store.initSession();window.dispatchEvent(new CustomEvent('niuma:account-reset'));
 planner.configurePlanner({getState:()=>state,render(){},openPreview(){},toast(){},refresh:async()=>{state=await store.refreshStore();}});
}
test('replanning one scheduled task previews replacement, keeps its old block until apply, and cancels without writing',async()=>{
 await setup();
 assert.match(planner.plannerPage(),/1 项待规划/);
 await planner.replanTasks(['scheduled']);
 assert.deepEqual(calls.find(call=>call.path==='/api/planning/proposals').body,examples.singleReplanRequest);
 assert.equal(state.schedule[0].id,'old');
 assert.match(planner.plannerPage(),/只重新规划：已有安排/);
 assert.match(planner.plannerPage(),/确认后将替换以下任务/);
 await planner.plannerClick({dataset:{planAction:'discard'}});
 assert.equal(state.schedule[0].id,'old');assert.equal(calls.some(call=>call.path==='/api/planning/apply'),false);
 await planner.replanTasks(['scheduled']);
 await planner.plannerClick({dataset:{planAction:'apply'}});
 assert.equal(state.schedule[0].id,'new-plan');assert.equal(state.tasks.length,2);
});
test('the global replan option includes scheduled tasks; a late cancelled preview cannot replace old plans',async()=>{
 await setup();
 planner.plannerChange({target:{dataset:{planReplan:'true'},checked:true}});
 assert.match(planner.plannerPage(),/2 项待规划/);
 let release;waiter={promise:new Promise(resolve=>release=resolve)};
 const operation=planner.plannerClick({dataset:{planAction:'start'}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.deepEqual(calls.find(call=>call.path==='/api/planning/proposals').body,examples.allReplanRequest);
 await planner.plannerClick({dataset:{planAction:'cancel'}});
 release(reply({requestId:'late',estimates:{},blocks:[],warnings:[]}));await operation;
 assert.equal(state.schedule[0].id,'old');assert.doesNotMatch(planner.plannerPage(),/取消提案/);
});

test('automatic planning after a targeted replan resets the range to newly imported tasks',async()=>{
 await setup();
 await planner.replanTasks(['scheduled']);
 assert.match(planner.plannerPage(),/只重新规划：已有安排/);
 waiter={promise:Promise.resolve(reply({requestId:'automatic',estimates:{new:60},blocks:[],warnings:[]}))};
 await planner.proposeForTasks(['new']);
 assert.deepEqual(calls.filter(call=>call.path==='/api/planning/proposals').at(-1).body,examples.newTasksRequest);
 assert.doesNotMatch(planner.plannerPage(),/只重新规划|确认后将替换以下任务/);
 assert.match(planner.plannerPage(),/1 项待规划/);
});

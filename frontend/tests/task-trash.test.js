import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.CustomEvent ||= class extends Event {constructor(type){super(type);}};
const store=await import('../src/team-store.js');
const trash=await import('../src/task-trash-ui.js');
const reply=value=>({ok:true,json:async()=>structuredClone(value)});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const plan={id:'plan',title:'写报告',startAt:'2026-10-07T19:00:00+08:00',endAt:'2026-10-07T20:00:00+08:00',origin:'ai'};
function setup(){
 let version=1,rejectWrite=false;
 let tasks=[
  {id:'homework',title:'教学网作业',description:'提交 PDF',status:'in_progress',due:{date:'2026-10-10',precision:'date'},subtasks:[{id:'step',title:'起草',status:'completed'}],plans:[plan],source:'教学网',recognitionHistory:[{storageId:'recognized'}],sourceRequirements:['提交 PDF']},
  {id:'done',title:'已完成',status:'completed',completedAt:'2026-10-01',due:null,subtasks:[],plans:[]},
  {id:'dependent',title:'后续任务',status:'not_started',dependsOn:'homework',due:null,subtasks:[],plans:[]},
  {id:'cancelled',title:'原有取消记录',status:'cancelled',due:null,subtasks:[],plans:[]}
 ];
 const original=structuredClone(tasks),writes=[];
 let feedback=[{taskId:'done',actualMinutes:45}],feedbackVersion=1;
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return reply({user:{id:'trash-tests',name:'测试'}});
  if(path==='/api/workspace')return reply({tasks:{value:tasks,version},feedback:{value:feedback,version:feedbackVersion}});
  if(path==='/api/data/tasks'){
   const body=JSON.parse(options.body);writes.push({method:options.method,body});
   if(rejectWrite)return {ok:false,status:409,json:async()=>({error:'数据已在其他窗口修改'})};
   assert.equal(body.version,version);version++;
   if(options.method==='DELETE'){
    const ids=new Set(body.ids);tasks=tasks.filter(task=>!ids.has(task.id)).map(task=>({...task,dependsOn:ids.has(task.dependsOn)?'':task.dependsOn}));
    feedback=feedback.filter(item=>!ids.has(item.taskId));feedbackVersion++;
    return reply({deletedIds:body.ids,version,feedback,feedbackVersion});
   }
   tasks=structuredClone(body.value);return reply({version});
  }
  throw new Error('unexpected '+path);
 };
 return {original,writes,get tasks(){return tasks;},get feedback(){return feedback;},reject:()=>{rejectWrite=true;}};
}

test('moving single or multiple tasks to trash persists across reload and restores metadata, status, plans and dependencies',async()=>{
 const fixture=setup();let state=await store.initSession();
 await store.trashStoredTasks(['homework','done']);
 state=await store.refreshStore();
 assert.deepEqual(state.tasks.map(task=>task.id),['dependent']);
 assert.deepEqual(state.trash.map(task=>task.id),['homework','done']);
 assert.deepEqual(state.schedule,[]);
 assert.equal(state.tasks[0].dependsOn,'');
 assert.equal(state.trash[0].status,'doing');assert.equal(state.trash[1].status,'done');
 assert.equal(fixture.tasks.find(task=>task.id==='homework').status,'cancelled');
 assert.equal(fixture.tasks.find(task=>task.id==='done').status,'completed');
 assert.deepEqual(fixture.feedback,[{taskId:'done',actualMinutes:45}]);
 state.tasks[0].notes='回收站存在时正常编辑';store.saveStore(state);await store.flushStore();
 assert.deepEqual(fixture.tasks.find(task=>task.id==='homework').recycleBin.plans,[plan]);
 await store.restoreStoredTasks(['homework','done']);
 state=await store.refreshStore();
 assert.deepEqual(state.trash,[]);
 for(const id of ['homework','done'])assert.deepEqual(fixture.tasks.find(task=>task.id===id),{...fixture.original.find(task=>task.id===id),dependsOn:''});
 assert.equal(state.tasks.find(task=>task.id==='dependent').dependsOn,'homework');
 assert.equal(state.schedule[0].id,'plan');
 assert.deepEqual(fixture.tasks.find(task=>task.id==='cancelled'),fixture.original[3]);
});

test('a stale ordinary save cannot resurrect a trashed task or its calendar entries',async()=>{
 const fixture=setup(),stale=await store.initSession();
 await store.trashStoredTasks(['homework']);
 stale.tasks.find(task=>task.id==='dependent').title='正常编辑';store.saveStore(stale);await store.flushStore();
 const archived=fixture.tasks.find(task=>task.id==='homework');
 assert.equal(archived.status,'cancelled');assert.deepEqual(archived.plans,[]);
 assert.deepEqual(archived.recycleBin.plans,[plan]);
 assert.equal((await store.refreshStore()).trash.length,1);
});

test('dependent tasks can be restored before their predecessor without losing the original link',async()=>{
 setup();await store.initSession();await store.trashStoredTasks(['homework','dependent']);
 await store.restoreStoredTasks(['dependent']);
 assert.equal((await store.refreshStore()).tasks.find(task=>task.id==='dependent').dependsOn,'');
 await store.restoreStoredTasks(['homework']);
 assert.equal((await store.refreshStore()).tasks.find(task=>task.id==='dependent').dependsOn,'homework');
});

test('permanent deletion removes only selected trash, its feedback and never returns on save',async()=>{
 const fixture=setup();await store.initSession();await store.trashStoredTasks(['homework','done']);
 await store.deleteStoredTasks(['done']);
 let state=await store.refreshStore();assert.deepEqual(state.trash.map(task=>task.id),['homework']);
 assert.deepEqual(fixture.feedback,[]);
 state.tasks[0].title='之后编辑';store.saveStore(state);await store.flushStore();
 assert.equal(fixture.tasks.some(task=>task.id==='done'),false);
 assert.equal(fixture.writes.find(write=>write.method==='DELETE').body.ids[0],'done');
});

test('failed writes leave tasks and trash intact, and invalid restoration cannot overwrite existing tasks',async()=>{
 const fixture=setup();await store.initSession();fixture.reject();
 await assert.rejects(store.trashStoredTasks(['homework']),/其他窗口/);
 assert.deepEqual(store.workspaceValue('tasks'),fixture.original);
 await assert.rejects(store.restoreStoredTasks(['homework']),/回收站任务不存在/);
 assert.equal(fixture.writes.length,1);
});

test('trash UI escapes task content, requires confirmation for deletion and restores through account storage',async()=>{
 setup();let state=await store.initSession();let dialog='',renders=0;const messages=[];
 trash.configureTrash({getState:()=>state,render:()=>renders++,toast:message=>messages.push(message),closeDialog(){},openDialog:html=>{dialog=html;},dialogHeader:title=>`<h2>${title}</h2>`,refresh:async()=>{state=await store.refreshStore();}});
 await trash.moveTasksToTrash(['homework']);
 state.trash[0].title='<img src=x onerror=alert(1)>';
 const page=trash.trashPage();assert.match(page,/&lt;img/);assert.doesNotMatch(page,/<img/);
 assert.match(page,/保留 1 项日程/);assert.match(page,/data-trash-restore="homework"/);
 trash.trashClick({dataset:{trashDelete:'homework'}});
 assert.match(dialog,/无法恢复/);assert.equal(state.trash.length,1);
 trash.trashClick({dataset:{trashRestore:'homework'}});await tick();
 assert.equal(state.trash.length,0);assert.equal(state.tasks.some(task=>task.id==='homework'),true);
 assert.ok(renders>0);assert.ok(messages.some(message=>message.includes('已恢复')));
 await trash.moveTasksToTrash(['homework','done']);
 trash.trashClick({dataset:{trashAction:'empty'}});assert.equal(state.trash.length,2);
 trash.trashClick({dataset:{trashAction:'confirm-delete'}});await tick();
 assert.equal(state.trash.length,0);assert.deepEqual(state.tasks.map(task=>task.id),['dependent']);
});

test('account switching clears trash and invalidates the previous account deletion confirmation',async()=>{
 setup();let state=await store.initSession();
 trash.configureTrash({getState:()=>state,render(){},toast(){},closeDialog(){},openDialog(){},dialogHeader:()=>'',refresh:async()=>{state=await store.refreshStore();}});
 await trash.moveTasksToTrash(['homework']);trash.trashClick({dataset:{trashDelete:'homework'}});
 globalThis.fetch=async(path)=>reply(path==='/api/auth/me'?{user:{id:'other-account',name:'另一账号'}}:{});
 state=await store.initSession();assert.deepEqual(state.trash,[]);
 assert.doesNotMatch(trash.trashPage(),/教学网作业/);
 trash.trashClick({dataset:{trashAction:'confirm-delete'}});assert.equal(trash.isTrashBusy(),false);
});

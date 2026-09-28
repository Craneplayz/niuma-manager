import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.document=new EventTarget();
globalThis.CustomEvent ||= class extends Event {constructor(type){super(type);}};
globalThis.location={hash:'#settings'};
const store=await import('../src/team-store.js');
const planner=await import('../src/team-planner-ui.js');
const feedback=await import('../src/team-personalization-ui.js');
const recognition=await import('../src/team-recognition-ui.js');
let state,active='A',delay=null,requests=[];
const task=(id,title,status)=>({id,title,status,due:null,plans:[],subtasks:[]});
const accounts={
 A:{tasks:{version:1,value:[task('a','A 的私有任务','completed'),task('a2','A 待规划','not_started')]}},
 B:{tasks:{version:1,value:[task('b','B 的任务','completed'),task('b2','B 待规划','not_started')]},
    availability:{version:1,value:{startTime:'08:00',endTime:'10:00',weekdays:[0]}}}
};
const response=value=>({ok:true,json:async()=>structuredClone(value)});
globalThis.fetch=async(path,options={})=>{
 const body=options.body?JSON.parse(options.body):null;
 requests.push({path,body,account:active,signal:options.signal});
 if(delay?.path===path){const pending=delay;delay=null;pending.started();return pending.promise;}
 if(path==='/api/auth/me')return response({user:{id:active,name:active}});
 if(path==='/api/auth/logout'){active=null;return response({});}
 if(path==='/api/auth/login'){active=body.email;return response({});}
 if(path==='/api/workspace')return response(accounts[active]);
 if(path.startsWith('/api/data/')){
  const name=path.split('/').at(-1),version=(accounts[active][name]?.version||0)+1;
  accounts[active][name]={version,value:body.value};
  return response({version});
 }
 if(path==='/api/planning/proposals')return response({requestId:'B-plan',estimates:{},blocks:[],warnings:[]});
 if(path==='/api/recognition/batches')return response([]);
 throw new Error('unexpected request '+path);
};
const host={getState:()=>state,render(){},persist(){store.saveStore(state);},refresh:async()=>{state=await store.refreshStore();},toast(){}};
planner.configurePlanner(host);feedback.configurePersonalization(host);recognition.configureAssistant(host);
function defer(path){
 let release,started;
 const promise=new Promise(resolve=>{release=resolve;});
 const ready=new Promise(resolve=>{started=resolve;});
 delay={path,promise,started};
 return {ready,release:value=>release(response(value))};
}
async function login(id){state=await store.authenticate('login',{email:id,password:'test'});}

test('account switch clears feedback samples, planning drafts and recognition metadata',async()=>{
 state=await store.initSession();
 feedback.personalizationClick({dataset:{feedbackAction:'sample'}});
 assert.match(feedback.personalizationSection(state.tasks),/A 的私有任务/);
 planner.plannerChange({target:{dataset:{planField:'startTime'},value:'13:00'}});
 recognition.handleAssistantInput({target:{id:'agent-published',value:'2026-09-01'}});
 recognition.handleAssistantInput({target:{id:'agent-quality',value:'partial'}});
 state=await store.logout();
 assert.doesNotMatch(feedback.personalizationSection(state.tasks),/A 的私有任务/);
 await login('B');
 assert.doesNotMatch(feedback.personalizationSection(state.tasks),/A 的私有任务/);
 assert.match(planner.plannerPage(),/value="08:00"/);
 assert.doesNotMatch(planner.plannerPage(),/value="13:00"/);
 assert.doesNotMatch(recognition.assistantPage(),/2026-09-01/);
 assert.match(recognition.assistantPage(),/value="complete" selected/);
 feedback.personalizationClick({dataset:{feedbackAction:'sample'}});
 assert.match(feedback.personalizationSection(state.tasks),/B 的任务/);
});

test('late planning response cannot repopulate another account or leave it busy',async()=>{
 await login('A');
 const pending=defer('/api/planning/proposals');
 const operation=planner.plannerClick({dataset:{planAction:'start'}});
 await pending.ready;
 const signal=requests.at(-1).signal;
 state=await store.logout();await login('B');
 assert.equal(signal.aborted,true);
 pending.release({requestId:'A-private-plan',estimates:{},blocks:[{title:'A 私有计划',startAt:'2026-09-25T10:00:00Z',endAt:'2026-09-25T11:00:00Z'}],warnings:[]});
 await operation;
 assert.doesNotMatch(planner.plannerPage(),/A 私有计划|正在处理|账号已切换/);
 await planner.plannerClick({dataset:{planAction:'start'}});
 assert.equal(requests.at(-1).account,'B');
});

test('late recognition response is discarded after logout and a new account can recognize',async()=>{
 await login('A');
 const pending=defer('/api/recognition/batches');
 const operation=recognition.submitRecognitionText('A 的保密通知');
 await pending.ready;
 state=await store.logout();await login('B');
 // Deliberately ignore abort in this mock: even a delivered response must be discarded.
 pending.release([{private:'A 的保密结果'}]);
 await operation;
 assert.doesNotMatch(recognition.assistantPage(),/A 的保密|识别中|账号已切换/);
 await recognition.submitRecognitionText('B 的通知');
 const request=requests.filter(r=>r.path==='/api/recognition/batches'&&r.body).at(-1);
 assert.equal(request.account,'B');
 assert.equal(request.body.records[0].text,'B 的通知');
});

test('shared recognition request survives the old 30-second limit but stops at 100 seconds',async(t)=>{
 await login('A');
 const pending=defer('/api/recognition/batches');
 t.mock.timers.enable({apis:['setTimeout']});
 const operation=recognition.submitRecognitionText('合成通知：请提交报告');
 await pending.ready;
 const signal=requests.at(-1).signal;
 try{
  t.mock.timers.tick(30001);
  assert.equal(signal.aborted,false);
  t.mock.timers.tick(70000);
  assert.equal(signal.aborted,true);
 }finally{
  pending.release([]);
  await operation;
  t.mock.timers.reset();
 }
});

test('planning exclusion is in the saved payload before a planning request is sent',async()=>{
 await login('B');
 planner.plannerChange({target:{dataset:{planExclude:'b2'},checked:true}});
 await planner.plannerClick({dataset:{planAction:'start'}});
 assert.equal(accounts.B.tasks.value.find(t=>t.id==='b2').planningExcluded,true);
 const save=requests.findLastIndex(r=>r.path==='/api/data/tasks');
 const plan=requests.findLastIndex(r=>r.path==='/api/planning/proposals');
 assert.ok(save<plan);
});

test('logout drains pending saves and refuses new writes during account transition',async()=>{
 await login('A');
 const pending=defer('/api/data/feedback');
 const save=store.saveData('feedback',[{taskId:'a',actualMinutes:30}]);
 await pending.ready;
 const logout=store.logout();
 await assert.rejects(store.saveData('feedback',[]),/切换/);
 assert.equal(active,'A');
 pending.release({version:1});
 await save;state=await logout;
 assert.equal(active,null);
 await login('B');
 assert.deepEqual(store.workspaceValue('feedback',[]),[]);
});

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
test('planning cards show concise reasons beside each task and collapse duplicate warnings',()=>{
 const html=planner.proposalSummaryHtml({estimates:{a:90,b:60,c:20},blocks:[{taskId:'b',startAt:'2026-10-06T19:00:00+08:00',endAt:'2026-10-06T20:00:00+08:00'}],priorityOrder:[{taskId:'a',rank:1,level:0,availableMinutes:0,estimatedMinutes:90,slackMinutes:-90},{taskId:'b',rank:2,level:2,availableMinutes:180,estimatedMinutes:60,slackMinutes:120}],warnings:['报告 <草稿>：P0，DDL 前尚缺 90 分钟','报告 <草稿>：未全部安排，请检查约束或截止时间']},[{id:'a',title:'报告 <草稿>',priority:'high',due:'2026-10-05',dueTime:'23:00'},{id:'b',title:'练习',priority:'medium',due:'2026-10-07',dueTime:'18:00'},{id:'c',title:'无日期',priority:'low',due:''}],Date.parse('2026-10-06T10:00:00+08:00'));
 assert.match(html,/报告 &lt;草稿&gt;/);
 assert.match(html,/已过截止时间/);
 assert.match(html,/截止时间已过，无法安排/);
 assert.match(html,/风险 P0 · 时间不足/);
 assert.match(html,/风险 P2 · 余量偏紧/);
 assert.match(html,/截止前可用 180 分钟，余量 120 分钟/);
 assert.match(html,/高优先级|中优先级/);
 assert.match(html,/排程优先第 1 位/);
 assert.doesNotMatch(html,/排程优先第 2 位/);
 assert.match(html,/1 项可安排 · 2 项需要处理/);
 assert.equal((html.match(/class="proposal-risk/g)||[]).length,2);
 assert.doesNotMatch(html,/P0，DDL|未全部安排|为什么这样排序/);
});
test('every computed risk level has its own label without replacing manual priority',()=>{
 const tasks=[0,1,2,3].map(level=>({id:String(level),title:'任务'+level,priority:'high',due:'2026-10-10'}));
 const html=planner.proposalSummaryHtml({estimates:Object.fromEntries(tasks.map(task=>[task.id,60])),blocks:[],warnings:[],priorityOrder:tasks.map((task,level)=>({taskId:task.id,rank:level+1,level,availableMinutes:level*60,estimatedMinutes:60,slackMinutes:(level-1)*60}))},tasks,Date.parse('2026-10-02T10:00:00+08:00'));
 for(let level=0;level<4;level++)assert.match(html,new RegExp('风险 P'+level));
 assert.equal((html.match(/高优先级/g)||[]).length,4);
});
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

test('automatic proposal stays a preview until confirmed or cancelled',async()=>{
 await login('A');
 requests=[];
 await planner.proposeForTasks(['a2']);
 const call=requests.find(item=>item.path==='/api/planning/proposals');
 assert.deepEqual(call.body,{taskIds:['a2']});
 assert.match(planner.plannerPage(),/排程提案|取消提案/);
 assert.equal(state.schedule.length,0);
 assert.equal(requests.some(item=>item.path==='/api/planning/apply'),false);
 await planner.plannerClick({dataset:{planAction:'discard'}});
 assert.doesNotMatch(planner.plannerPage(),/取消提案/);
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
  t.mock.timers.tick(30000);
  assert.equal(signal.aborted,false); // The shared API must not abort at 60 seconds.
  t.mock.timers.tick(40000);
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


test('sync processes more than twenty single records, continues past failure and retries only the failed batch',async()=>{
 await login('A');
 const originalFetch=globalThis.fetch,seen=[],saved=[];
 const syncRequests=Array.from({length:25},(_,i)=>({contract_version:'0.2',request_id:'sync:'+i,records:[{input_id:'r:'+i,text:'合成通知 '+i,platform:'teaching_web'}]}));
 let fail=true;
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/sources/sync')return response({requests:syncRequests,summary:{total:65,skipped:40,pending:25}});
  if(path==='/api/recognition/retry'){
   const request=JSON.parse(options.body);seen.push(request);
   assert.equal(request.records.length,1);
   const failed=fail&&request.request_id==='sync:0';
   const result={status:failed?'failed':'no_tasks',record_results:[{input_id:request.records[0].input_id,status:failed?'failed':'no_tasks',warnings:[]}],errors:failed?[{input_id:request.records[0].input_id,message:'Agent 调用超时。'}]:[]};
   const item={request,result,items:[],demo:false},index=saved.findIndex(b=>b.request.request_id===request.request_id);
   if(index<0)saved.push(item);else saved[index]=item;
   return response(saved);
  }
  return originalFetch(path,options);
 };
 try{
  recognition.handleAssistantInput({target:{dataset:{agentSyncSource:'teaching_web'},checked:true}});
  recognition.handleAssistantInput({target:{id:'agent-sync-consent',checked:true}});
  await recognition.handleAssistantClick({dataset:{agentAction:'sync-sources'}});
  assert.equal(seen.length,25);
  const page=recognition.assistantPage();
  assert.match(page,/尚未全部完成：成功 24 条，失败 1 条，未处理 0 条，跳过 40 条/);
  assert.doesNotMatch(page,/新任务已加入日历/);
  assert.match(page,/data-agent-retry="sync:0"/);
  assert.doesNotMatch(page,/data-agent-retry="sync:1"/);
  fail=false;
  await recognition.handleAssistantClick({dataset:{agentRetry:'sync:0'}});
  assert.equal(seen.length,26);
  assert.equal(seen.at(-1).request_id,'sync:0');
  assert.doesNotMatch(recognition.assistantPage(),/data-agent-retry=/);
 }finally{globalThis.fetch=originalFetch;}
});

test('empty source sync does not report zero unchanged records as filtered content',async()=>{
 await login('A');
 const originalFetch=globalThis.fetch;
 globalThis.fetch=async(path,options)=>path==='/api/sources/sync'
  ? response({requests:[],summary:{total:0,skipped:0,pending:0}})
  : originalFetch(path,options);
 try{
  recognition.handleAssistantInput({target:{dataset:{agentSyncSource:'teaching_web'},checked:true}});
  recognition.handleAssistantInput({target:{id:'agent-sync-consent',checked:true}});
  await recognition.handleAssistantClick({dataset:{agentAction:'sync-sources'}});
  const page=recognition.assistantPage();
  assert.match(page,/同步完成，暂无待识别内容/);
  assert.doesNotMatch(page,/跳过 0 条/);
 }finally{globalThis.fetch=originalFetch;}
});

test('a late sync response cannot send any records after account switch',async()=>{
 await login('A');
 recognition.handleAssistantInput({target:{dataset:{agentSyncSource:'teaching_web'},checked:true}});
 recognition.handleAssistantInput({target:{id:'agent-sync-consent',checked:true}});
 const pending=defer('/api/sources/sync');
 const operation=recognition.handleAssistantClick({dataset:{agentAction:'sync-sources'}});
 await pending.ready;
 state=await store.logout();await login('B');
 const before=requests.length;
 pending.release({requests:[{request_id:'private-sync',records:[{text:'private'}]}]});
 await operation;
 assert.equal(requests.length,before);
 assert.doesNotMatch(recognition.assistantPage(),/private-sync|private/);
});

test('failed source sync clears stale loading notice and remains retryable',async()=>{
 await login('A');
 const originalFetch=globalThis.fetch;
 globalThis.fetch=async(path,options)=>path==='/api/sources/sync'
  ? {ok:false,status:502,json:async()=>({error:'服务处理失败，请稍后重试；数据未变更'})}
  : originalFetch(path,options);
 try{
  recognition.handleAssistantInput({target:{dataset:{agentSyncSource:'teaching_web'},checked:true}});
  recognition.handleAssistantInput({target:{id:'agent-sync-consent',checked:true}});
  await recognition.handleAssistantClick({dataset:{agentAction:'sync-sources'}});
  const page=recognition.assistantPage();
  assert.match(page,/服务处理失败，请稍后重试/);
  assert.doesNotMatch(page,/正在获取教学网信息/);
  assert.match(page,/data-agent-action="sync-sources"/);
 }finally{globalThis.fetch=originalFetch;}
});

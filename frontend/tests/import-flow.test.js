import test from 'node:test';
import assert from 'node:assert/strict';
import 'fake-indexeddb/auto';
import {readFile} from 'node:fs/promises';
import {setMaxListeners} from 'node:events';

globalThis.window=new EventTarget();
setMaxListeners(0,globalThis.window);
globalThis.CustomEvent ||= class extends Event {constructor(type,options){super(type);this.detail=options?.detail;}};
globalThis.FormData=class {constructor(form){this.values=form.values;}*[Symbol.iterator](){yield* Object.entries(this.values);}};

const store=await import('../src/team-store.js');
const {createImportUI:createImportController,buildTextImport,buildCandidateResolution,candidateDue,shanghaiNow,buildRecognitionRequest,describeImportError}=await import('../src/import-ui.js');
const {createAccountUI}=await import('../src/account-ui.js');
function createImportUI(host) {
 const ui=createImportController(host), account=createAccountUI(host);
 host.renderEmailSettings=()=>ui.renderEmailSettings();
 host.loadEmailSettings=()=>ui.loadEmailSettings();
 host.openAccountSettings=section=>account.open(section);
 host.refreshAccountSettings=section=>account.refresh(section);
 return ui;
}
const {syncRun}=await import('./sync-fixtures.js');
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const deadline={kind:'deadline',status:'explicit',precision:'date',value:'2026-10-20'};
const candidate={title:'提交报告',description:'原文要求',course_or_project:'概率论',notice_type:'create',deadline,evidence:[{quote:'请在10月20日前提交报告。'}],sources:[],review_reasons:[]};
const item={id:'candidate',state:'pending',version:3,candidate};
const teachingCases=JSON.parse(await readFile(new URL('../../contracts/examples/teaching-workspace-v01.json',import.meta.url),'utf8')).cases;
const teachingCase=name=>teachingCases.find(item=>item.name===name);

test('text publication uses Shanghai wall time and an explicit offset',()=>{
 const now=new Date('2026-10-13T18:20:05Z');
 assert.equal(shanghaiNow(now),'2026-10-14T02:20:05');
 const request=buildTextImport(' 明天提交报告 ','2026-10-14T02:20:05','one','2026-10-13T18:20:05.000Z');
 assert.equal(request.contract_version,'0.2');
 assert.equal(request.records[0].text,'明天提交报告');
 assert.deepEqual(request.records[0].published_at,{value:'2026-10-14T02:20:05+08:00',precision:'datetime',basis:'upstream_unverified'});
 assert.equal(request.records[0].timezone,'Asia/Shanghai');
 assert.equal(request.records[0].collected_at,'2026-10-13T18:20:05.000Z');
 assert.throws(()=>buildTextImport(' '.repeat(2)),/粘贴/);
 assert.throws(()=>buildTextImport('a'.repeat(4001)),/4000/);
 assert.throws(()=>buildTextImport('通知','2026-02-30T12:00:00'),/有效/);
});

test('review submit confirms changes without a separate checkbox and retains date precision',()=>{
 const values={action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:''};
 const result=buildCandidateResolution(item,values,[],7);
 assert.deepEqual(result.draft.due,{date:'2026-10-20',precision:'date'});
 assert.equal(result.version,3);assert.equal(result.tasks_version,7);
 assert.equal(result.reviewed,true);
 assert.throws(()=>buildCandidateResolution(item,{...values,date:'',time:'18:00'},[],7),/截止日期/);
 const task={id:'target',title:'原任务',notes:'已有说明',course:'原课程'};
 const cancelled={...item,candidate:{...candidate,notice_type:'cancel'}};
 assert.throws(()=>buildCandidateResolution(cancelled,{action:'cancel',target_id:'missing'},[task],7),/选择/);
 const cancel=buildCandidateResolution(cancelled,{action:'cancel',target_id:'target'},[task],7);
 assert.equal(cancel.target_id,'target');assert.equal(cancel.draft,undefined);
 const postpone=buildCandidateResolution({...item,candidate:{...candidate,notice_type:'postpone'}},{...values,action:'postpone',target_id:'target'},[task],7);
 assert.equal(postpone.draft.title,'原任务');assert.equal(postpone.draft.description,'已有说明');
 assert.deepEqual(candidateDue({...deadline,precision:'datetime',value:'2026-10-19T18:30:00Z'}),{date:'2026-10-20',time:'02:30'});
 assert.deepEqual(candidateDue({...deadline,precision:'datetime',value:'2026-10-20T12:30:45+08:00'}),{date:'2026-10-20',time:''});
});

test('candidate confirmation retains notice classification and never changes the selected target type',()=>{
 const values={action:'create',title:'地点调整',description:'上课地点更改',course:'',date:'',time:''};
 const notice={...item,candidate:{...candidate,content_type:'notice'}};
 assert.equal(buildCandidateResolution(notice,values,[],7).draft.content_type,'notice');
 assert.equal(buildCandidateResolution(item,values,[],7).draft.content_type,'task');
 const update={...item,candidate:{...candidate,notice_type:'update'}};
 const target={id:'target',title:'原通知',content_type:'notice',notes:'原说明'};
 assert.equal(buildCandidateResolution(update,{...values,action:'update',target_id:'target'},[target],7).draft.content_type,'notice');
 const incomingNotice={...notice,candidate:{...notice.candidate,notice_type:'update'}};
 assert.equal(buildCandidateResolution(incomingNotice,{...values,action:'update',target_id:'target'},[{...target,content_type:'task'}],7).draft.content_type,'task');
});

function fakeForm(kind,values,extra={}){
 const error={textContent:'',hidden:true},retry={hidden:true},submit={textContent:'确认',disabled:false,dataset:{}};
 const fields=[submit,{disabled:false,dataset:{}}];
 return {dataset:{importForm:kind,...extra},values,error,retry,submit,querySelector(selector){if(selector.includes('refresh-review'))return retry;if(selector==='[data-import-error-details]')return null;if(selector.includes('error'))return error;if(selector==='[type="submit"]')return submit;return null;},querySelectorAll(){return fields;}};
}
function heldRequest() {
 let enter,release;
 const entered=new Promise(resolve=>enter=resolve),waiting=new Promise(resolve=>release=resolve);
 return {entered,release,async wait(){enter();await waiting;}};
}
function fixture(scenario={}){
 let account='A',state,dialog='',calls=[],messages=[],renderCalls=0,batches={A:[],B:[]},taskVersion=7,deferred=null,workspaces={A:[],B:[]},teachingFailed=false,runs={A:[],B:[]},textJobs={A:[],B:[]},operations={A:new Map(),B:new Map()};
 const respond=value=>({ok:true,json:async()=>structuredClone(value)});
 const reject=error=>({ok:false,status:error.status||503,json:async()=>({error:error.message})});
 const syncCases=scenario.syncCases||[teachingCase('date-only'),teachingCase('no-deadline')];
 const batch=(who='A',id='request-'+who)=>({request:buildTextImport(who+' 私有通知','2026-10-14T12:00:00',id),result:{status:'success'},items:[{...structuredClone(item),id:'candidate-'+who,candidate:{...structuredClone(candidate),title:who+' 私有任务'}}]});
 batches.A=[batch('A')];batches.B=[batch('B')];
 globalThis.fetch=async(path,options={})=>{
  const method=options.method||'GET',body=options.body?JSON.parse(options.body):undefined;
  calls.push({path,method,body,account,signal:options.signal});
  if(scenario.intercept){
   const value=await scenario.intercept(path,body,method);
   if(value!==undefined)return respond(value);
  }
  if(path==='/api/sources/email/credentials'&&method==='GET')return respond(scenario.emailCredentials||{configured:false,address:null,username:null});
  if(path==='/api/sources/email/preferences'&&method==='GET')return respond(scenario.emailPreferences||{consent_granted:false,process_all:false,consent_version:null,consented_at:null});
  if(path==='/api/sources/email/preferences'&&method==='POST'){if(scenario.emailPreferenceError)return reject(scenario.emailPreferenceError);return respond({consent_granted:body.consent,process_all:body.process_all,consent_version:body.consent?'email-v1':null,consented_at:body.consent?'2026-10-19T00:00:00Z':null});}
  if(path==='/api/sources/email/credentials'&&method==='POST')return respond({configured:true,address:body.address,username:body.username});
  if(path==='/api/sources/email/credentials/remove'&&method==='POST')return respond({configured:false,address:null,username:null});
  if(path==='/api/sources/email/preview'&&method==='POST')return respond(scenario.emailPreview||{messages:[],summary:{total:0},next_offset:null});
  if(path==='/api/sources/email/reprocess'&&method==='POST')return respond(scenario.emailReprocessJob||{kind:'reprocess',job_id:'email-reprocess-1',state:'queued',counts:{selected:body.input_ids.length,processed:0,cached:0,excluded:0,failed:0,pending:body.input_ids.length},items:body.input_ids.map(input_id=>({input_id,state:'queued'}))});
  if(path==='/api/sources/email/exclusions'&&method==='POST')return respond({ok:true});
  if(path==='/api/sources/email/jobs?summary=1'&&method==='GET')return respond({revision:1,jobs:(scenario.emailJobs||[]).map(job=>({...job,revision:job.revision??1}))});
  if(path==='/api/sources/email/jobs'&&method==='GET')return respond({jobs:scenario.emailJobs||[]});
  if(path==='/api/sources/email/jobs'&&method==='POST'){if(scenario.jobStartErrors>0){scenario.jobStartErrors--;return reject({status:503,message:'暂时失败'});}return respond(scenario.emailJob||{job_id:'email-job-1',state:'queued',counts:{selected:1,processed:0,cached:0,excluded:0,failed:0,pending:1},items:[]});}
  if(path.startsWith('/api/sources/email/jobs/')&&method==='GET'){if(scenario.emailJobGetError)return reject(scenario.emailJobGetError);const job=(scenario.emailJobFull&& !path.endsWith('?summary=1'))?scenario.emailJobFull:(scenario.emailJob||{job_id:'email-job-1',state:'succeeded',counts:{selected:1,processed:1,cached:0,excluded:0,failed:0,pending:0},items:[]});return respond(path.endsWith('?summary=1')?((({items,...summary})=>({...summary,revision:scenario.emailSummaryRevision??job.revision??1,waiting_reason:scenario.emailWaitingReason??job.waiting_reason}))(job)):job);}
  if(path.startsWith('/api/sources/email/jobs/')&&method==='POST'){if(path.endsWith('/retry')&&scenario.jobRetryErrors>0){scenario.jobRetryErrors--;return reject({status:503,message:'暂时失败'});}return respond(scenario.emailJob||{job_id:'email-job-1',state:'stopped',counts:{selected:1,processed:0,cached:0,excluded:0,failed:0,pending:1},items:[]});}
  if(path==='/api/sources/sync'&&method==='POST'&&body.sources?.[0]==='email')return respond(scenario.emailSyncResponse||{requests:[],summary:{total:0,skipped:0,pending:0,selected:0,unavailable:0,partial:0}});
  if(path==='/api/auth/login'||path==='/api/auth/register'){account=body.email.split('@')[0];return respond({});}
  if(path==='/api/auth/me')return respond({user:{id:account,name:account,email:account+'@test.local'}});
  if(path==='/api/auth/logout')return respond({});
  if(path==='/api/recognition/text-jobs'){
   if(method==='POST'){
    if(scenario.recognitionError)return reject(scenario.recognitionError);
    const now='2026-10-22T02:00:00Z';
    if(!textJobs[account].some(job=>job.request_id===body.request_id)){
     textJobs[account].push({request_id:body.request_id,state:'success',attempt:1,created_at:now,updated_at:now,error:null});
     batches[account].push({request:body,result:{status:'success'},items:[]});
    }
   }
   return respond({contract_version:'text-import-jobs/0.1',jobs:textJobs[account]});
  }
  if(path==='/api/workspace')return respond({tasks:{version:taskVersion,value:workspaces[account]}});
  if (path === '/api/sources/sync-schedule') return respond({contract_version:'source-sync/0.1', enabled:false, available:true, interval_minutes:180, revision:0});
  if(path==='/api/sources/credentials'){if(method==='POST'&&scenario.credentialError)return reject(scenario.credentialError);return respond({configured:true,username:account+'-campus'});}
  if(path==='/api/recognition/batches'&&method==='GET'){
   if(deferred){const value=structuredClone(batches[account]);const pending=deferred;deferred=null;pending.ready();await pending.wait;return respond(value);}
   return respond(batches[account]);
  }
  if(path==='/api/recognition/batches'&&method==='POST'&&body.records?.some(record=>record.platform==='email')&&scenario.emailRecognitionResponse){
   batches[account]=structuredClone(scenario.emailRecognitionResponse);return respond(batches[account]);
  }
  if((path==='/api/recognition/batches'||path==='/api/recognition/retry')&&method==='POST'){
   if(scenario.recognitionError)return reject(scenario.recognitionError);
   let next;
   if(body.contract_version==='teaching-run/0.1'){
    const fixture=syncCases.find(item=>item.run.request_id===body.request_id);
    assert.ok(fixture,'the identifier must originate from a captured server fixture');
    assert.deepEqual(body,fixture.run,'teaching recognition accepts only the server-issued run identity');
    const existing=batches[account].find(batch=>batch.request.request_id===body.request_id);
    if(existing&&['success','no_tasks'].includes(existing.result.status))return respond(batches[account]);
    const failed=scenario.teachingFailOnce&&!teachingFailed;
    if(failed)teachingFailed=true;
    const result=failed?{...fixture.result,status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]}:fixture.result;
    next={request:fixture.request,result,items:result.tasks.map((candidate,index)=>({id:body.request_id+':storage:'+index,state:'pending',version:1,candidate}))};
   }else next={request:body,result:{status:'success'},items:[]};
   batches[account]=batches[account].filter(batch=>batch.request.request_id!==next.request.request_id);batches[account].push(next);return respond(batches[account]);
  }
  if(path==='/api/tasks/reconcile')return respond({applied:body.operations.filter(operation=>operations[account].has(operation.id)).map(operation=>operation.id),tasks:{value:workspaces[account],version:taskVersion},candidates:batches[account].flatMap(batch=>batch.items).map(({id,state,version})=>({id,state,version})),generation:0});
  if(path.includes('/resolve')){
   const operation=body.operation;
   if(operation&&operations[account].has(operation.id))return respond({...operations[account].get(operation.id),replayed:true});
   const found=batches[account].flatMap(b=>b.items).find(item=>decodeURIComponent(path).includes(item.id));
   if(found){
    if(found.state==='pending'&&body.action==='create'){
     const due=candidateDue(found.candidate.deadline),draft=body.draft||{title:found.candidate.title,description:found.candidate.description,course:found.candidate.course_or_project,due:due.date?{date:due.date,precision:due.time?'datetime':'date',...(due.time?{time:due.time}:{})}:null};
     workspaces[account].push({id:operation?.intent.newTaskId||'task-'+found.id,...draft,source:'教学网',status:body.complete?'completed':'not_started',plans:[],subtasks:[],recognitionHistory:[{storageId:found.id,requestId:batches[account].find(batch=>batch.items.includes(found)).request.request_id,action:'create'}]});taskVersion++;
    }else if(['update','postpone','cancel'].includes(body.action)){
     const target=workspaces[account].find(task=>task.id===body.target_id);assert.ok(target,'review changes apply to an existing target');
     if(body.action==='cancel')target.status='cancelled';
     else if(body.action==='postpone')target.due=structuredClone(body.draft.due);
     else Object.assign(target,structuredClone(body.draft));
     target.recognitionHistory=[...(target.recognitionHistory||[]),{storageId:found.id,action:body.action}];taskVersion++;
    }
    found.state=body.action==='ignore'?'ignored':body.action==='reopen'?'pending':'applied';found.version++;
   }
   const result={batches:batches[account],...(operation?{operationId:operation.id,tasks:{value:workspaces[account],version:taskVersion},candidates:batches[account].flatMap(batch=>batch.items).map(({id,state,version})=>({id,state,version})),generation:0}:{})};
   if(operation)operations[account].set(operation.id,structuredClone(result));
   return respond(result);
  }
  if(path==='/api/sources/sync-runs'){
   if(method==='GET')return respond({contract_version:'source-sync/0.1',runs:runs[account]});
   if(scenario.syncError)return reject(scenario.syncError);
   assert.equal(body.contract_version,'source-sync/0.1');assert.deepEqual(body.sources,['teaching_web']);assert.equal(body.consent,true);assert.ok(body.operation_id);
   const records=[];
   for(const source of syncCases){
    const failed=scenario.teachingFailOnce&&!teachingFailed;
    if(failed)teachingFailed=true;
    const result=failed?{...source.result,status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]}:source.result;
    const next={request:structuredClone(source.request),result:structuredClone(result),items:result.tasks.map((candidate,index)=>({id:source.run.request_id+':storage:'+index,state:'applied',version:2,candidate:structuredClone(candidate)}))};
    const existing=batches[account].find(batch=>batch.request.request_id===source.request.request_id);
    if(!existing){
     batches[account].push(next);
     for(const found of next.items){
      const due=candidateDue(found.candidate.deadline);
      workspaces[account].push({id:'task-'+found.id,title:found.candidate.title,description:found.candidate.description,course:found.candidate.course_or_project,source:'教学网',due:due.date?{date:due.date,precision:due.time?'datetime':'date',...(due.time?{time:due.time}:{})}:null,status:'not_started',plans:[],subtasks:[]});taskVersion++;
     }
    }
    records.push({request_id:source.request.request_id,label:source.request.records[0].label||'教学网通知',course:source.request.records[0].course_or_project||'',state:failed?'failed':'succeeded',added:existing?0:next.items.length,seconds:1,error:failed?{code:'agent_timeout',message:'识别服务连接失败或超时',retryable:true}:null});
   }
   const counts=Object.fromEntries(['queued','running','succeeded','failed','incomplete','cancelled','interrupted'].map(name=>[name,records.filter(record=>record.state===name).length]));
   const run={contract_version:'source-sync/0.1',run_id:'sync-run-'+runs[account].length,operation_id:body.operation_id,sources:['teaching_web'],trigger:'manual',parent_run_id:null,state:counts.failed?'partial_success':'succeeded',revision:2,created_at:'2026-10-14T12:00:00Z',updated_at:'2026-10-14T12:00:01Z',skipped:0,records,counts:{...counts,total:records.length,skipped:0,added:records.reduce((sum,record)=>sum+record.added,0)},error:null};
   runs[account].unshift(run);return respond(run);
  }
  if(path==='/api/auth/profile')return respond({user:{id:account,name:body.name,email:account+'@test.local'}});
  if(path==='/api/auth/password')return respond({});
  throw new Error('unexpected request '+path);
 };
 const focuses=[];
 const host={focusEmailSettings:()=>focuses.push("pku-email-settings"),focusAccountSection:section=>focuses.push(section),escapeHtml,icon:()=>'',toast:message=>messages.push(message),render(){renderCalls++;},openDialog(html){dialog=html;},closeDialog(){dialog='';},isDialogOpen:selector=>dialog.includes(selector.slice(1,-1)),getState:()=>state,setState:value=>state=value,onReviewStore:()=>state=store.readProjectedStore(),refresh:async()=>state=await store.refreshStore(),isDemo:()=>false};
 return {host,batch,batches,calls,messages,focuses,scenario,get renderCalls(){return renderCalls;},get dialog(){return dialog;},get state(){return state;},async login(who='A'){state=await store.authenticate('login',{email:who+'@test.local',password:'test-password'});},seedTask(task,who='A'){workspaces[who].push(structuredClone(task));},setVersion(value){taskVersion=value;},defer(){let resolve,ready;const wait=new Promise(r=>resolve=r),entered=new Promise(r=>ready=r);deferred={wait,ready};return {entered,release:resolve};}};
}

test('email connection saves a school mailbox credential without exposing the secret in the page',async t=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 await ui.click({dataset:{importSource:'email'}});
 assert.match(ui.render(),/邮箱/);
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 assert.match(f.dialog,/data-import-form="email-credentials"/);
 assert.match(f.dialog,/type="password"/);
 await ui.submit(fakeForm('email-credentials',{address:' student@stu.pku.edu.cn ',username:' student ',password:'client-secret'}));
 const saved=f.calls.find(call=>call.path==='/api/sources/email/credentials'&&call.method==='POST');
 assert.deepEqual(saved.body,{address:'student@stu.pku.edu.cn',username:'student',password:'client-secret',consent:false,process_all:false});
 assert.doesNotMatch(ui.render(),/client-secret/);
 assert.ok(f.messages.includes('北大邮箱已连接'));
});

test('email senders retain full text for mobile and expose safe desktop names with address tooltips',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:[
  {input_id:'email:sender:1',subject:'Named sender',sender:'GitHub <noreply@github.com>',state:'unprocessed'},
  {input_id:'email:sender:2',subject:'Quoted sender',sender:'"A & B" <updates@example.org>',state:'unprocessed'},
  {input_id:'email:sender:3',subject:'Address only',sender:'plain@example.org',state:'unprocessed'},
  {input_id:'email:sender:4',subject:'Untrusted sender',sender:'<img src=x onerror=alert(1)> <unsafe@example.org>',state:'unprocessed'},
 ],summary:{total:4},next_offset:null}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-preview'}});
 const html=ui.render();
 assert.match(html,/class="email-sender-desktop" data-email-stop-toggle tabindex="0" aria-describedby="(email-sender-tooltip-\d+)">GitHub<span id="\1" class="email-sort-tooltip" role="tooltip">noreply@github.com<\/span>/);
 assert.match(html,/class="email-sender-full">GitHub &lt;noreply@github.com&gt;<\/span>/);
 assert.match(html,/>A &amp; B<span id="email-sender-tooltip-\d+"/);
 assert.equal((html.match(/class="email-sender-desktop"/g)||[]).length,2,'only named addresses receive a compact display');
 assert.match(html,/plain@example.org/);assert.doesNotMatch(html,/<img src=x/);assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('email management updates consent and recognition settings without resending credentials',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:[{input_id:'email:box:77:1',subject:'通知',excluded:false,state:'unprocessed'}],summary:{total:1},next_offset:null}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-preview'}});
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 assert.match(f.dialog,/留空以保留已保存的密码/);
 assert.doesNotMatch(f.dialog,/name="password"[^>]*required/);
 await ui.submit(fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:'',consent:'true',process_all:'true'}));
 assert.equal(f.calls.filter(call=>call.path==='/api/sources/email/credentials'&&call.method==='POST').length,0);
 const saved=f.calls.find(call=>call.path==='/api/sources/email/preferences'&&call.method==='POST');
 assert.deepEqual(saved.body,{consent:true,process_all:true});
 assert.match(ui.render(),/通知/,'changing preferences preserves the current mail preview');
 assert.ok(f.messages.includes('邮箱设置已保存'));
 assert.equal(f.dialog,'');
 assert.match(ui.renderEmailSettings(true),/name="process_all"[^>]*checked/);
 assert.equal(f.focuses.at(-1),'pku-email-settings');
});

test('email management requires a password to change the mailbox but permits explicit password rotation',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 const changed=fakeForm('email-credentials',{address:'other@stu.pku.edu.cn',username:'other',password:'',consent:'true'});
 await ui.submit(changed);assert.equal(changed.error.hidden,false);
 assert.equal(f.calls.some(call=>call.path==='/api/sources/email/credentials'&&call.method==='POST'),false);
 await ui.submit(fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:'Synthetic-rotated',consent:'true'}));
 const saved=f.calls.find(call=>call.path==='/api/sources/email/credentials'&&call.method==='POST');
 assert.equal(saved.body.password,'Synthetic-rotated');
 assert.doesNotMatch(ui.render(),/Synthetic-rotated/);
});

test('email preferences persist, range preview is paged, exclusions are explicit and jobs process selected mail',async t=>{
 const inputId='email:mailboxhash:77:7';
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false,consent_version:'email-v1',consented_at:'2026-10-14T00:00:00Z'},emailPreview:{messages:[{input_id:inputId,subject:'作业通知',sender:'teacher@example.edu.cn',received_at:'2026-10-14T10:00:00+08:00',state:'unprocessed',excluded:false,task_count:0}],summary:{total:51},next_offset:50}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-preview'}});
 const previews=()=>f.calls.filter(call=>call.path==='/api/sources/email/preview'&&call.method==='POST');
 assert.equal(previews()[0].body.cache_only,true);
 assert.deepEqual(previews()[1].body,{limit:50,start_date:null,filter:'all',sort:'attention',offset:0,page_size:25,load_all:true,refresh:true,cache_only:false});
 ui.input({target:{dataset:{emailLimit:''},value:'75'}});
 const beforeRangeApply=f.calls.length;ui.input({target:{dataset:{emailFilter:''},value:'attention'}});
 assert.equal(f.calls.length,beforeRangeApply);assert.equal(previews().at(-1).body.limit,50);
 await ui.click({dataset:{importAction:'email-preview'}});assert.match(ui.render(),/已选 0 封/);
 assert.deepEqual(previews().at(-1).body,{limit:75,start_date:null,filter:'all',sort:'attention',offset:0,page_size:25,load_all:true,refresh:true,cache_only:false});
 assert.match(ui.render(),/选择所有/);assert.doesNotMatch(ui.render(),/data-email-consent|data-email-process-all/);
 ui.input({target:{dataset:{emailMessage:inputId},checked:true}});
 await ui.click({dataset:{emailExclude:inputId,excluded:'true'}});
 assert.deepEqual(f.calls.find(call=>call.path==='/api/sources/email/exclusions').body,{input_ids:[inputId],excluded:true});
 await ui.click({dataset:{importAction:'email-select-range'}});
 await ui.click({dataset:{importAction:'email-start-job'}});
 const job=f.calls.find(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST');
 assert.deepEqual(Object.keys(job.body).sort(),['operation_id','range']);
 assert.deepEqual(job.body.range,{limit:75,start_date:null});
 assert.equal(f.calls.some(call=>call.path==='/api/sources/sync'),false);
 assert.match(ui.render(),/role="status"><div class="email-job-summary"><div class="email-job-copy"><div class="email-job-heading"><strong>排队中/);
});

test('email filters and sorting operate on the loaded list without API requests',async t=>{
 const first='email:synthetic:1', second='email:synthetic:2';
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:[{input_id:first,subject:'样例一',state:'no_tasks',excluded:false},{input_id:second,subject:'样例二',state:'has_tasks',pending_review_count:1,excluded:false},{input_id:'email:synthetic:3',subject:'失败样例',state:'failed',excluded:false,recognition_error:{code:'invalid_agent_output',retryable:true,failure:{reason:'draft_evidence',category:'evidence',stage:'validate'},diagnostic_id:'1234567890abcdef12345678'}},{input_id:'email:synthetic:4',subject:'排除样例',state:'excluded',excluded:true}],summary:{total:80},next_offset:50}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-preview'}});
 const calls=f.calls.length;
 ui.input({target:{dataset:{emailFilter:''},value:'pending_review'}});
 assert.match(ui.render(),/匹配 1 封 \/ 本范围共 80 封/);assert.match(ui.render(),/已选 3 封/);assert.match(ui.render(),/样例二/);assert.doesNotMatch(ui.render(),/样例一|失败样例|排除样例/);
 await ui.click({dataset:{importAction:'email-select-page'}});assert.match(ui.render(),/已选 1 封/);
 ui.input({target:{dataset:{emailFilter:''},value:'failed'}});assert.match(ui.render(),/失败样例/);assert.doesNotMatch(ui.render(),/样例二/);
 assert.match(ui.render(),/诊断编号：1234567890abcdef12345678/);assert.match(ui.render(),/data-error-feedback=/);
 assert.match(ui.render(),/<\/label>[\s\S]*email-message-failure/);
 ui.input({target:{dataset:{emailFilter:''},value:'attention'}});assert.match(ui.render(),/样例二/);assert.match(ui.render(),/失败样例/);assert.doesNotMatch(ui.render(),/样例一|排除样例/);
 ui.input({target:{dataset:{emailFilter:''},value:'excluded'}});assert.match(ui.render(),/排除样例/);assert.doesNotMatch(ui.render(),/样例一|样例二|失败样例/);
 ui.input({target:{dataset:{emailFilter:''},value:'unprocessed'}});assert.match(ui.render(),/已获取邮件中没有匹配邮件/);
 ui.input({target:{dataset:{emailFilter:''},value:'all'}});assert.match(ui.render(),/样例一/);assert.match(ui.render(),/样例二/);
 for(let i=0;i<3;i++)await ui.click({dataset:{importAction:'email-cycle-sort'}});
 assert.equal(f.calls.length,calls,'local filter and sort never fetch mail');assert.match(ui.render(),/已选 1 封/);
 ui.input({target:{dataset:{emailFilter:''},value:'failed'}});
 await ui.click({dataset:{importAction:'email-next'}});
 const page=f.calls.filter(call=>call.path==='/api/sources/email/preview'&&call.method==='POST').at(-1).body;
 assert.equal(page.offset,0);assert.equal(page.filter,'all');assert.equal(page.sort,'attention');
});

test('email preview restores the persistent header cache and pins pagination to its snapshot',async t=>{
 const id='email:cache:1';
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:[{input_id:id,subject:'Synthetic cached header',state:'unprocessed',excluded:false}],summary:{total:51},next_offset:50,cache:{snapshot_id:'snapshot-one',miss:false,complete:true,synced_at:'2026-10-20T01:00:00Z'}}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const previews=()=>f.calls.filter(call=>call.path==='/api/sources/email/preview'&&call.method==='POST');
 assert.equal(previews()[0].body.cache_only,true);assert.equal(previews()[0].body.refresh,false);assert.equal(previews()[0].body.snapshot_id,undefined);
 assert.match(ui.render(),/Synthetic cached header/);
 await ui.click({dataset:{importAction:'email-preview'}});
 assert.equal(previews()[1].body.refresh,true);assert.equal(previews()[1].body.cache_only,false);assert.equal(previews()[1].body.snapshot_id,undefined);
 assert.match(ui.render().match(new RegExp(`<input type="checkbox" data-email-message="${id}"[^>]*>`))[0],/checked/,'the first explicit read retains the initial-page selection behavior after cache restore');
 await ui.click({dataset:{importAction:'email-next'}});
 assert.equal(previews().length,2,'page navigation does not call the preview API');
 ui.input({target:{dataset:{emailLimit:''},value:'100'}});await ui.click({dataset:{importAction:'email-preview'}});
 assert.equal(previews()[2].body.limit,100);assert.equal(previews()[2].body.snapshot_id,undefined,'a changed range never carries the previous range token');
});

test('email cache pointer restores the applied range and page per account without persisting draft edits',async t=>{
 const originalStorage=globalThis.localStorage, values=new Map();globalThis.localStorage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};t.after(()=>{if(originalStorage===undefined)delete globalThis.localStorage;else globalThis.localStorage=originalStorage;});
 const calls=[], id='email:pointer:1';
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},intercept:async(path,body,method)=>{
  if(path!=='/api/sources/email/preview'||method!=='POST')return;
  calls.push(structuredClone(body));
  if(body.cache_only)return {messages:[],summary:{total:0},next_offset:null,cache:{snapshot_id:null,miss:true,complete:false}};
  return {messages:Array.from({length:101},(_,i)=>({input_id:id+':'+i,subject:'Synthetic pointer header '+i,state:'unprocessed',excluded:false})),summary:{total:101},next_offset:body.offset===0?50:null,cache:{snapshot_id:body.start_date?'snapshot-date':'snapshot-100',miss:false,complete:true}};
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 ui.input({target:{dataset:{emailLimit:''},value:'100'}});await ui.click({dataset:{importAction:'email-preview'}});await ui.click({dataset:{importAction:'email-next'}});
 assert.equal(calls.at(-1).offset,0);assert.match(ui.render(),/第 2 页/);
 ui.input({target:{dataset:{emailRangeLimit:''},value:'all'}});ui.input({target:{dataset:{emailStartDate:''},value:'2026-08-01'}});await ui.click({dataset:{importAction:'email-preview'}});
 assert.equal(calls.at(-1).start_date,'2026-08-01');assert.equal(calls.at(-1).limit,null);
 const saved=[...values.values()].join(' ');assert.match(saved,/2026-08-01/);assert.doesNotMatch(saved,/Synthetic pointer header|email:pointer/);
 ui.reset();await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(calls.at(-1).cache_only,true);assert.equal(calls.at(-1).refresh,false);assert.equal(calls.at(-1).start_date,'2026-08-01');assert.equal(calls.at(-1).offset,0);
 await f.login('B');await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(calls.at(-1).cache_only,true);assert.equal(calls.at(-1).limit,50);assert.equal(calls.at(-1).start_date,null);assert.equal(calls.at(-1).offset,0,'a different account does not reuse the previous account pointer');
});

test('policy warnings appear only on successful outdated email results',async t=>{
 const ok='email:policy:1',pending='email:policy:2';
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:[{input_id:ok,subject:'Synthetic successful mail',state:'has_tasks',policy_status:'outdated',saved_policy_version:'old',current_policy_version:'new',excluded:false},{input_id:pending,subject:'Synthetic pending mail',state:'unprocessed',policy_status:'unknown',excluded:false}],summary:{total:2},next_offset:null,cache:{snapshot_id:'snapshot-policy',miss:false,complete:true}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(ui.render(),/Synthetic successful mail <span class="email-policy-warning"[^>]*tabindex="0"[^>]*aria-describedby="email-policy-tooltip-\d+"[^>]*>.*?class="email-sort-tooltip" role="tooltip">使用旧版规则识别<\/span><\/span><\/strong>/);assert.doesNotMatch(ui.render(),/识别规则已更新/);assert.doesNotMatch(ui.render(),/识别规则版本未知/,'pending messages do not show policy drift');
 assert.doesNotMatch(ui.render(),/data-import-action="email-reprocess"/,'the retired popup has no user-facing trigger');
});

test('late cached email responses are ignored after explicit retrieval and account reset',async t=>{
 let releaseOld,releaseAccount;const oldWait=new Promise(resolve=>releaseOld=resolve),accountWait=new Promise(resolve=>releaseAccount=resolve);let count=0;
 const result=subject=>({messages:[{input_id:'email:race:1',subject,state:'unprocessed',excluded:false}],summary:{total:1},next_offset:null,cache:{snapshot_id:'race',complete:true,miss:false}});
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},intercept:async(path,body,method)=>{
  if(path!=='/api/sources/email/preview'||method!=='POST')return;
  if(body.cache_only)return oldWait;
  count++;return count===1?result('新结果'):accountWait;
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-preview'}});releaseOld(result('旧结果'));await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(ui.render(),/新结果/);assert.doesNotMatch(ui.render(),/旧结果/);
 const late=ui.click({dataset:{importAction:'email-preview'}});await new Promise(resolve=>setTimeout(resolve,0));await f.login('B');releaseAccount(result('旧账户结果'));await late;assert.doesNotMatch(ui.render(),/旧账户结果/);
});

test('email settings live only in connection management and failed saves retain acknowledged consent',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreferenceError:{status:503,message:'暂时失败'}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});
 assert.doesNotMatch(ui.render(),/data-email-consent|data-email-process-all|name="consent"|name="process_all"/);
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 assert.match(f.dialog,/name="consent"/);assert.match(f.dialog,/name="process_all"/);
 const form=fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:''});
 await ui.submit(form);
 assert.equal(form.error.hidden,false);assert.match(ui.renderEmailSettings(true),/name="consent"[^>]*checked/);
 assert.match(ui.render(),/<h2>北大邮箱<\/h2><span class="email-connection-identity"><span class="status-note">student@stu.pku.edu.cn<\/span>/);
 assert.doesNotMatch(ui.render(),/已连接 student|你已同意|标题和发件人预览不读取正文|请先在“管理连接”/);
});

test('late email preference and job responses from a prior account cannot update the new account',async t=>{
 let releasePreference, releaseJob;
 const preferenceWait=new Promise(resolve=>releasePreference=resolve), jobWait=new Promise(resolve=>releaseJob=resolve);
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:false,process_all:false},intercept:async(path,body,method)=>{if(path==='/api/sources/email/preferences'&&method==='POST')return await preferenceWait;if(path==='/api/sources/email/jobs'&&method==='POST')return await jobWait;}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 const saving=ui.submit(fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:'',consent:'true'}));
 await new Promise(resolve=>setTimeout(resolve,0));await f.login('B');releasePreference({consent_granted:true,process_all:false});await saving;
 await ui.load();await ui.click({dataset:{importSource:'email'}});assert.doesNotMatch(ui.render(),/你已同意/);
 // A fresh authorized job response must also be ignored after an account reset.
 await ui.click({dataset:{importAction:'email-preview'}});await ui.click({dataset:{importAction:'email-select-range'}});
 // Persist consent for B before starting the range.
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 await ui.submit(fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:'',consent:'true'}));
 const starting=ui.click({dataset:{importAction:'email-start-job'}});await new Promise(resolve=>setTimeout(resolve,0));await f.login('C');releaseJob({job_id:'old-account-job',state:'queued',counts:{selected:1,processed:0,cached:0,excluded:0,failed:0,pending:1},items:[]});await starting;
 assert.doesNotMatch(ui.render(),/old-account-job/);
});

test('email polling errors expose a resume control and retry POSTs reuse their operation id',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[{job_id:'recover-job',state:'running',counts:{selected:1,processed:0,cached:0,excluded:0,failed:0,pending:1},items:[]}],emailJobGetError:{status:503,message:'暂时失败'},jobRetryErrors:1,emailJob:{job_id:'recover-job',state:'queued',counts:{selected:1,processed:0,cached:0,excluded:0,failed:0,pending:1},items:[]}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-poll'}});assert.match(ui.render(),/继续读取状态/);
 scenario.emailJobGetError=null;await ui.click({dataset:{importAction:'email-poll'}});assert.doesNotMatch(ui.render(),/继续读取状态/);
 // A terminal job remains available after polling and retries with one stable operation id.
 scenario.emailJobs=[{job_id:'recover-job',state:'stopped',counts:{selected:1,processed:0,cached:0,excluded:0,failed:1,pending:1},items:[]}];scenario.emailJob={...scenario.emailJobs[0]};await ui.load();
 await ui.click({dataset:{importAction:'email-retry-job'}});await ui.click({dataset:{importAction:'email-retry-job'}});
 const retries=f.calls.filter(call=>call.path.endsWith('/retry')&&call.method==='POST');assert.equal(retries.length,2);assert.equal(retries[0].body.operation_id,retries[1].body.operation_id);
});

test('email start retries reuse the operation id and latest stopped jobs remain retryable',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:[{input_id:'email:retry',state:'unprocessed',excluded:false}],summary:{total:1},next_offset:null},jobStartErrors:1,emailJobs:[{job_id:'stopped-job',state:'stopped',counts:{selected:1,processed:0,cached:0,excluded:0,failed:1,pending:1},items:[]}]};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(ui.render(),/重试识别/);await ui.click({dataset:{importAction:'email-preview'}});ui.input({target:{dataset:{emailMessage:'email:retry'},checked:true}});
 await ui.click({dataset:{importAction:'email-start-job'}});await ui.click({dataset:{importAction:'email-start-job'}});
 const starts=f.calls.filter(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST');assert.equal(starts.length,2);assert.equal(starts[0].body.operation_id,starts[1].body.operation_id);
});

test('an active email job restores and stops without allowing a duplicate start',async t=>{
 const messages=Array.from({length:25},(_,i)=>({input_id:`email:mailboxhash:${i}`,subject:`通知 ${i}`,sender:'teacher@example.edu.cn',state:'unprocessed',excluded:false}));
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages,summary:{total:25},next_offset:null},emailJobs:[{job_id:'restored-job',state:'running',counts:{selected:25,processed:3,cached:1,excluded:0,failed:0,pending:21},items:[{input_id:messages[0].input_id,state:'has_tasks',task_count:2}]}]};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(ui.render(),/role="status"><div class="email-job-summary"><div class="email-job-copy"><div class="email-job-heading"><strong>正在处理/,'an active job is restored before metadata preview');
 await ui.click({dataset:{importAction:'email-start-job'}});assert.equal(f.calls.filter(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST').length,0,'active job blocks a duplicate start');
 scenario.emailJobs=[];await ui.click({dataset:{importAction:'email-stop-job'}});
 assert.ok(f.calls.some(call=>call.path==='/api/sources/email/jobs/restored-job/stop'&&call.method==='POST'));
});

test('confirming a candidate without a checkbox sends reviewed plus both versions without fabricated time',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importReview:'candidate-A'}});
 assert.doesNotMatch(f.dialog,/name="reviewed"|我已核对原文/);
 const form=fakeForm('review',{action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:''},{candidate:'candidate-A'});
 await ui.submit(form);
 const call=f.calls.find(call=>call.path.endsWith('/resolve'));
 assert.deepEqual(call.body.draft.due,{date:'2026-10-20',precision:'date'});
 assert.equal(call.body.version,3);assert.equal(call.body.tasks_version,7);assert.equal(call.body.reviewed,true);
 assert.equal(f.calls.some(call=>call.path.includes('/planning/')),false);
 assert.equal(f.dialog,'');assert.ok(f.messages.includes('已加入任务'));
 assert.equal(call.body.operation.intent.kind,'review');assert.equal(call.body.operation.intent.candidateId,'candidate-A');
 assert.equal(f.calls.filter(call=>call.path==='/api/workspace').length,1,'manual review needs no follow-up workspace read');
});

test('a confirmed history candidate keeps its receipt count and opens the task with a live status',async t=>{
 const f=fixture();await f.login();const opened=[];f.host.openTask=id=>opened.push(id);
 const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 await ui.click({dataset:{importHistoryCategory:'review',importHistoryBatch:'request-A'}});
 assert.match(ui.render(),/sync-item-status-review">待核对/);
 assert.match(ui.render(),/class="sync-category sync-category-review"[^>]*data-import-history-category="review"/);
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:'request-A'}});
 await ui.submit(fakeForm('review',{action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:''},{candidate:'candidate-A'}));
 assert.match(ui.render(),/待核对 1 项/);assert.doesNotMatch(ui.render(),/新增 1 项/);
 assert.match(ui.render(),/class="sync-category sync-category-success"[^>]*data-import-history-category="review"/);
 assert.match(ui.render(),/sync-item-status-muted">未完成/);
 const confirmedId=f.state.tasks[0].id;
 f.state.tasks[0].status='done';
 f.state.tasks[0].content_type='notice';
 assert.match(ui.render(),/sync-item-status-success">✓ 已完成/);
 assert.match(ui.render(),/<span class="notice-label">通知<\/span><span class="sync-result-title">/);
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:'request-A'}});
 assert.deepEqual(opened,[confirmedId]);
 f.state.trash=[f.state.tasks.pop()];
 assert.match(ui.render(),/>回收站<\/small>/);assert.doesNotMatch(ui.render(),/已移至|">未完成<|进行中/);
 f.state.trash=[];
 assert.match(ui.render(),/sync-item-status-muted">已移除/);assert.doesNotMatch(ui.render(),/已核对/);
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:'request-A'}});
 assert.equal(f.messages.at(-1),'这条任务或通知已移除，无法打开详情');
});

test('an unrelated workspace version change does not block a candidate creation',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
 f.setVersion(8);await f.host.refresh();
 const form=fakeForm('review',{action:'create',title:'报告',date:'2026-10-20',time:''},{candidate:'candidate-A'});
 await ui.submit(form);
 assert.equal(f.calls.filter(call=>call.path.endsWith('/resolve')).length,1);
 assert.equal(f.state.tasks[0].title,'报告');assert.equal(form.error.hidden,true);
});

test('independent history moves a reviewed category behind additions after confirmation',async t=>{
 const f=fixture();
 f.batches.A[0].items.push({...structuredClone(item),id:'auto-A',state:'applied',autoApplied:true});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const order=()=>[...ui.render().matchAll(/data-import-history-category="([^"]+)"/g)].map(match=>match[1]);
 assert.deepEqual(order(),['review','added']);
 await ui.click({dataset:{importHistoryCategory:'review',importHistoryBatch:'request-A'}});
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:'request-A'}});
 await ui.submit(fakeForm('review',{action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:''},{candidate:'candidate-A'}));
 assert.deepEqual(order(),['added','review']);
 assert.match(ui.render(),/class="sync-category sync-category-success"[^>]*data-import-history-category="review"/);
 assert.match(ui.render(),/data-import-history-category="review"[^>]*aria-expanded="true"/);
 assert.match(ui.render(),/新增 1 项/);assert.match(ui.render(),/待核对 1 项/);
});

test('a late review reread cannot replace another candidate draft',async()=>{
 const f=fixture();await f.login();
 const other=f.batch('A','another-request');other.items[0].id='another-candidate';other.items[0].candidate.title='另一项待核对任务';f.batches.A.push(other);
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
 let enter,release;const entered=new Promise(resolve=>enter=resolve),waiting=new Promise(resolve=>release=resolve);
 f.host.refresh=async()=>{enter();await waiting;};
 const oldForm=fakeForm('review',{});oldForm.isConnected=true;
 const rereading=ui.click({dataset:{importAction:'refresh-review'},closest:()=>oldForm});
 await entered;await ui.click({dataset:{importAction:'close'}});oldForm.isConnected=false;
 await ui.click({dataset:{importReview:'another-candidate'}});
 const draft=fakeForm('review',{action:'create',title:'保留这份新草稿',description:'仍在编辑',course:'概率论',date:'2026-10-20',time:''});
 ui.input({target:{dataset:{importField:'title'},closest:()=>draft}});
 const expected=f.dialog;assert.match(expected,/保留这份新草稿/);
 release();await rereading;
 assert.equal(f.dialog,expected);
});

test('review rereading locks its form and cannot reopen a dialog dismissed with Escape',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
 let enter,release;const entered=new Promise(resolve=>enter=resolve),waiting=new Promise(resolve=>release=resolve);
 f.host.refresh=async()=>{enter();await waiting;};
 const dialog={open:true},form=fakeForm('review',{});form.isConnected=true;form.closest=()=>dialog;
 const rereading=ui.click({dataset:{importAction:'refresh-review'},closest:()=>form});
 await entered;const locked=form.submit.disabled;
 dialog.open=false;f.host.closeDialog();release();await rereading;
 assert.equal(f.dialog,'');
 assert.equal(locked,true);
 assert.equal(form.submit.disabled,false);
});

test('a completed candidate save cannot close a different dialog opened while it was pending',async()=>{
 let enter,release;const entered=new Promise(resolve=>enter=resolve),waiting=new Promise(resolve=>release=resolve);
 const f=fixture({async intercept(path){if(path.endsWith('/resolve')){enter();await waiting;}}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
 const form=fakeForm('review',{action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:''},{candidate:'candidate-A'});form.isConnected=true;
 const saving=ui.submit(form);await entered;form.isConnected=false;f.host.openDialog('<form>另一个窗口的草稿</form>');
 release();await saving;
 assert.equal(f.dialog,'<form>另一个窗口的草稿</form>');
 assert.equal(f.state.tasks.length,1);
 assert.ok(f.messages.includes('已加入任务'));
});

test('ignored candidates can be restored using the newest candidate version',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importIgnore:'candidate-A'}});
 assert.match(ui.render(),/已忽略/);
 await ui.click({dataset:{importReopen:'candidate-A'}});
 const calls=f.calls.filter(call=>call.path.endsWith('/resolve'));
 assert.deepEqual(calls.map(call=>({action:call.body.action,version:call.body.version})),[{action:'ignore',version:3},{action:'reopen',version:4}]);
 assert.ok(calls.every(call=>call.body.operation.intent.kind==='review'));
 assert.match(ui.render(),/data-import-review="candidate-A"/);
});

test('teaching collection starts a background run and reads its server-created tasks without planning',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
 const sync=f.calls.find(call=>call.path==='/api/sources/sync-runs'&&call.method==='POST');assert.deepEqual(sync.body.sources,['teaching_web']);assert.equal(sync.body.consent,true);
 assert.equal(f.calls.filter(call=>call.path==='/api/recognition/batches'&&call.method==='POST').length,0);
 assert.equal(f.calls.filter(call=>call.path.endsWith('/resolve')).length,0);
 assert.equal(f.host.getState().tasks.length,2);
 assert.equal(f.calls.some(call=>call.path.includes('/planning/')),false);
 assert.match(ui.render(),/导入完成/);
});

test('an old account response cannot restore another account’s candidates or connection',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host),pending=f.defer(),loading=ui.load();await pending.entered;
 const old=f.calls.findLast(call=>call.path==='/api/recognition/batches');
 await f.login('B');assert.equal(old.signal.aborted,true);pending.release();await loading;await ui.load();
 const html=ui.render();assert.doesNotMatch(html,/A 私有任务|A-campus/);assert.match(html,/B 私有任务/);
 await ui.click({dataset:{importSource:'teaching'}});assert.match(ui.render(),/B-campus/);
});

test('task-list readiness resets with the account and ignores an old pending read',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);
 assert.equal(ui.taskReviewsReady(),false);
 const pending=f.defer(),reading=ui.loadTaskReviews();await pending.entered;
 assert.equal(ui.taskReviewsReady(),false);
 await f.login('B');pending.release();await reading;
 assert.equal(ui.taskReviewsReady(),false);
 assert.deepEqual(ui.getReviewItems(),[]);
 await ui.loadTaskReviews();
 assert.equal(ui.taskReviewsReady(),true);
 assert.equal(ui.getReviewItems()[0].candidate.title,'B 私有任务');
 ui.reset();assert.equal(ui.taskReviewsReady(),false);
});

test('demo text submit stays local',async()=>{
 const f=fixture();await f.login();const ui=createImportUI({...f.host,isDemo:()=>true});const before=f.calls.length;
 await ui.submit(fakeForm('text',{text:'示例通知',published:'2026-10-14T12:00:00'}));
 assert.equal(f.calls.length,before);assert.match(f.messages.at(-1),/示例预览暂不发送/);
});

test('account login survives its own reset and replaces the root account state',async()=>{
 const f=fixture();await f.login();const ui=createAccountUI(f.host),form=fakeForm('',{email:'B@test.local',password:'test-password'});
 form.dataset={accountForm:'login'};await ui.submit(form);
 assert.equal(store.session.user.id,'B');assert.ok(f.messages.includes('已登录'));assert.equal(form.submit.disabled,false);
 await ui.click({dataset:{accountAction:'profile'}});
 assert.match(f.dialog,/data-account-form="profile"/);
 const profile=fakeForm('',{name:'新昵称'});profile.dataset={accountForm:'profile'};await ui.submit(profile);
 assert.equal(store.session.user.name,'新昵称');assert.ok(f.messages.includes('昵称已更新'));
});

test('password mismatch is visible and does not send a request',async()=>{
 const f=fixture();await f.login();const ui=createAccountUI(f.host),form=fakeForm('',{currentPassword:'old',newPassword:'new-password',confirmPassword:'different'});
 form.dataset={accountForm:'password'};const before=f.calls.length;await ui.submit(form);
 assert.equal(f.calls.length,before);assert.match(form.error.textContent,/不一致/);assert.equal(form.error.hidden,false);
});

test('account saves retain their result without closing a replacement dialog',async()=>{
 for(const kind of ['profile','password']){
  let enter,release;const entered=new Promise(resolve=>enter=resolve),waiting=new Promise(resolve=>release=resolve);
  const f=fixture({async intercept(path){if(path==='/api/auth/'+kind){enter();await waiting;}}});await f.login();
  const ui=createAccountUI(f.host);await ui.click({dataset:{accountAction:kind}});
  const values=kind==='profile'?{name:'保存的新昵称'}:{currentPassword:'old-password',newPassword:'new-password',confirmPassword:'new-password'};
  const form=fakeForm('',values);form.dataset={accountForm:kind};form.isConnected=true;
  const saving=ui.submit(form);await entered;form.isConnected=false;f.host.openDialog('<form>保留其他窗口的草稿</form>');
  release();await saving;
  assert.equal(f.dialog,'<form>保留其他窗口的草稿</form>');
  assert.ok(f.messages.includes(kind==='profile'?'昵称已更新':'密码已更新'));
  if(kind==='profile')assert.equal(store.session.user.name,'保存的新昵称');
 }
});

test('real teaching workspace views become identity-only server run requests',()=>{
 for(const fixture of teachingCases){
  assert.deepEqual(buildRecognitionRequest(fixture.request),fixture.run,fixture.name);
  const edited={...fixture.request,records:[{text:'客户端修改的正文',password:'not-to-be-sent'}],unexpected:'drop-this'};
  assert.deepEqual(buildRecognitionRequest(edited),fixture.run);
 }
 const manual=buildTextImport('文本通知','2026-10-14T12:00:00','manual');
 assert.deepEqual(buildRecognitionRequest(manual),manual);
 assert.throws(()=>buildRecognitionRequest({contract_version:'teaching-workspace/0.1',records:[]}),/重新采集/);
});

test('real teaching candidates retain date-only, absent, and unknown-year deadlines',()=>{
 const values={action:'create',title:'核对后的任务',description:'',course:''};
 const dateOnly=teachingCase('date-only').result.tasks[0];
 assert.deepEqual(candidateDue(dateOnly.deadline),{date:'2026-10-23',time:''});
 assert.deepEqual(buildCandidateResolution({version:1,candidate:dateOnly},{...values,...candidateDue(dateOnly.deadline)},[],2).draft.due,{date:'2026-10-23',precision:'date'});
 for(const name of ['no-deadline','unknown-year']){
  const candidate=teachingCase(name).result.tasks[0];
  assert.deepEqual(candidateDue(candidate.deadline),{date:'',time:''},name);
  assert.equal(buildCandidateResolution({version:1,candidate},{...values,...candidateDue(candidate.deadline)},[],2).draft.due,null);
 }
 assert.equal(teachingCase('unknown-year').result.tasks[0].needs_review,true);
 assert.equal(teachingCase('unknown-year').result.tasks[0].deadline.value,null);
});

test('a teaching run with two server-created tasks refreshes both as normal tasks',async()=>{
 const source=teachingCase('two-tasks'),f=fixture({syncCases:[source]});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
 const html=ui.render();
 assert.doesNotMatch(html.split('class="import-results"')[1],/已处理 ·|:storage:/);
 assert.equal(f.calls.filter(call=>call.path.endsWith('/resolve')).length,0);
 assert.equal(f.host.getState().tasks.length,2);
 assert.ok(f.host.getState().tasks.every(task=>task.status==='todo'));
 assert.doesNotMatch(html,/data-import-review="[^"]+:storage:/);
});

test('retrying a saved teaching failure outside background runs sends its identity and applies clear results through the backend',async()=>{
 const source=teachingCase('date-only'),f=fixture({syncCases:[source]});
 f.batches.A.push({request:structuredClone(source.request),result:{...structuredClone(source.result),status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]},items:[]});
 await f.login();const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});
 const html=ui.render();
 assert.match(html,/历史导入 · 1/);assert.doesNotMatch(html,/历史导入 · 识别未完成/);
 assert.ok(html.includes(`data-import-history-retry="${source.run.request_id}"`));
 await ui.click({dataset:{importHistoryCategory:'error',importHistoryBatch:source.run.request_id}});
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:source.run.request_id}});
 assert.match(ui.render(),/任务识别未完成/);
 assert.doesNotMatch(html.split('class="import-results"')[1],/import-failure|data-import-retry/);
 await new Promise(r=>setImmediate(r));
 await ui.click({dataset:{importHistoryRetry:source.run.request_id}});
 const calls=f.calls.filter(call=>['/api/recognition/batches','/api/recognition/retry'].includes(call.path)&&call.method==='POST');
 assert.deepEqual(calls.map(call=>({path:call.path,body:call.body})),[{path:'/api/recognition/retry',body:source.run}]);
 const automatic=f.calls.filter(call=>call.path.endsWith('/resolve'));
 assert.equal(automatic.length,1);assert.deepEqual(automatic[0].body,{action:'create',auto_apply:true,version:1});
 assert.equal(f.host.getState().tasks.length,1);assert.equal(f.calls.some(call=>call.path.includes('/planning/')),false);
 await ui.click({dataset:{importHistoryRetry:source.run.request_id}});
 assert.equal(f.calls.filter(call=>call.path.endsWith('/resolve')).length,1);assert.equal(f.host.getState().tasks.length,1);
});

test('hidden sync failures cannot reappear or retry through legacy history while independent failures remain usable',async t=>{
 const managed=teachingCase('date-only'),legacy=teachingCase('no-deadline');
 const f=fixture({syncCases:[managed,legacy]});
 for(const [source,sourceSyncManaged] of [[managed,true],[legacy,false]])f.batches.A.push({
  request:structuredClone(source.request),result:{...structuredClone(source.result),status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]},items:[],sourceSyncManaged,
 });
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));
 const html=ui.render();
 assert.match(html,/历史导入 · 1/);assert.doesNotMatch(html,/历史导入 · 识别未完成/);
 assert.doesNotMatch(html,new RegExp(`data-import-retry="${managed.run.request_id}"`));
 assert.ok(html.includes(`data-import-history-retry="${legacy.run.request_id}"`));
 assert.match(html,/data-import-review="candidate-A"/);
 await ui.click({dataset:{importRetry:managed.run.request_id}});
 assert.equal(f.calls.filter(call=>call.path==='/api/recognition/retry'&&call.method==='POST').length,0);
 await ui.click({dataset:{importHistoryRetry:legacy.run.request_id}});
 const retries=f.calls.filter(call=>call.path==='/api/recognition/retry'&&call.method==='POST');
 assert.equal(retries.length,1);assert.equal(retries[0].body.request_id,legacy.run.request_id);
});

test('sync runs and independent recognition batches share one history with in-batch errors',async t=>{
 const legacy=teachingCase('date-only'),run=syncRun({id:'sync:visible',added:1});
 const f=fixture({intercept(path,body,method){if(path==='/api/sources/sync-runs'&&method==='GET')return {contract_version:'source-sync/0.1',runs:[run]};}});
 f.batches.A.push({request:structuredClone(legacy.request),result:{...structuredClone(legacy.result),status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]},items:[]});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});await ui.loadSourceSync();await new Promise(r=>setImmediate(r));
 const html=ui.render();
 assert.equal((html.match(/<summary>历史导入/g)||[]).length,1);assert.match(html,/历史导入 · 2/);
 assert.match(html,/新增 1 项/);assert.match(html,/错误 1 条/);assert.doesNotMatch(html,/识别未完成|data-task-status|history-status/);
 await ui.toggle({target:{dataset:{sourceSyncHistory:''},open:true,isConnected:true}});
 await ui.click({dataset:{importHistoryCategory:'error',importHistoryBatch:legacy.run.request_id}});
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:legacy.run.request_id}});
 assert.match(ui.render(),/sync-problem/);assert.match(ui.render(),/data-error-feedback=/);
 await ui.load();assert.match(ui.render(),/data-source-sync-history open/);
 await ui.click({dataset:{importSource:'text'}});
 assert.doesNotMatch(ui.render(),/sync-category-sync:visible|data-import-history-batch-entry="synthetic:date-only"/);
});

test('records from one independent import remain one batch with candidates and errors',async t=>{
 const pending=teachingCase('date-only'),failed=teachingCase('no-deadline');
 const f=fixture({syncCases:[pending,failed]});
 f.batches.A=[
  {importId:'batch:shared',request:structuredClone(pending.request),result:structuredClone(pending.result),items:[{...structuredClone(item),id:'candidate-shared'}]},
  {importId:'batch:shared',request:structuredClone(failed.request),result:{...structuredClone(failed.result),status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]},items:[]},
 ];
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));
 assert.equal((ui.render().match(/data-import-history-batch-entry=/g)||[]).length,1);
 assert.match(ui.render(),/历史导入 · 1/);assert.match(ui.render(),/待核对 1 项/);assert.match(ui.render(),/错误 1 条/);
 await ui.click({dataset:{importHistoryCategory:'review',importHistoryBatch:'batch:shared'}});
 await ui.click({dataset:{importHistoryItem:'0',importHistoryBatch:'batch:shared'}});
 assert.match(f.dialog,/data-import-form="review"/);
 await ui.click({dataset:{importHistoryRetry:'batch:shared'}});
 const retries=f.calls.filter(call=>call.path==='/api/recognition/retry'&&call.method==='POST');
 assert.equal(retries.length,1);assert.equal(retries[0].body.request_id,failed.run.request_id);
});

test('configuration, credentials, and connection failures give different next steps',()=>{
 const cases=[
  ['教学网采集依赖未安装；请先在 crawler/ 执行 pnpm install','crawler_dependency',/暂时无法从教学网获取通知/],
  ['凭据加密依赖未安装；请安装 server/requirements.txt','credential_dependency',/无法保存教学网连接/],
  ['无法解密教学网凭据；请重新保存账号和密码','credential_storage',/重新保存账号和密码/],
  ['文本识别配置不可用，请检查当前环境的模型配置文件或环境变量中的模型配置与密钥','model_configuration',/需要维护人员处理/],
  ['教学网识别配置不可用，请检查当前环境的七牛云模型配置并重启后端','teaching_model_configuration',/需要维护人员处理/],
  ['教学网采集失败；请检查账号、密码、浏览器及校园网连接','teaching_connection',/核对账号和密码后重试/],
  ['无法连接服务，请检查后端','service_connection',/检查网络后重试/],
  ['模型请求排队已满或等待超时，请稍后重试；未开始模型调用','model_busy',/稍后重试/],
 ];
 for(const [message,kind,expected] of cases){const report=describeImportError({message,status:503});assert.equal(report.kind,kind);assert.match(report.message,expected);}
 assert.match(describeImportError({status:401,message:'请先登录'}).message,/重新登录/);
 const raw='调用失败 API_KEY=private-model-key Authorization: Bearer private-token password=campus-password';
 assert.doesNotMatch(JSON.stringify(describeImportError({status:502,message:raw})),/private-model-key|private-token|campus-password/);
});

test('a missing crawler component offers user guidance and an error report without installation commands',async()=>{
 const f=fixture({syncError:{status:503,message:'教学网采集依赖未安装；请先在 crawler/ 执行 pnpm install'}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
 const html=ui.render(),main=html.match(/<p class="user-error-message">([^<]+)<\/p>/)?.[1];
 assert.match(main,/暂时无法从教学网获取通知.*维护人员处理/);
 assert.doesNotMatch(main,/crawler|pnpm|Node/);
 assert.match(html,/data-error-feedback=/);
 assert.doesNotMatch(html,/<details class="import-error-details"[^>]*\bopen\b/);
 assert.doesNotMatch(html,/pnpm install|server\/requirements/);
 assert.doesNotMatch(html,/采集服务尚未就绪/);
});

test('model configuration failures are actionable and never echo model credentials',async()=>{
 const f=fixture({recognitionError:{status:503,message:'文本识别配置不可用，请检查模型密钥 API_KEY=private-test-key'}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.submit(fakeForm('text',{text:'提交作业',published:'2026-10-14T12:00:00'}));
 const html=ui.render();assert.match(html,/任务识别暂时不可用.*维护人员处理/);
 assert.match(html,/反馈此问题/);assert.doesNotMatch(html,/private-test-key/);
});

test('account management opens the existing teaching connection form before visiting import',async t=>{
 const f=fixture();await f.login();
 const ui=createImportUI(f.host),account=createAccountUI(f.host);t.after(()=>ui.reset());
 await account.click({dataset:{accountAction:'open'}});
 assert.match(f.dialog,/aria-label="教学网账号管理"/);
 assert.match(f.dialog,/data-import-action="credentials"/);
 await ui.click({dataset:{importAction:'credentials'},textContent:'管理连接'});
 assert.match(f.dialog,/data-import-form="credentials"/);
 assert.match(f.dialog,/value="A-campus"/);
 assert.match(f.dialog,/data-import-action="remove-credentials"/);
 assert.equal(f.calls.filter(call=>call.path==='/api/sources/credentials').length,1);
 assert.equal(f.calls.some(call=>call.path==='/api/recognition/batches'),false);
});

test('closing account management during a credential read does not reopen its dialog',async t=>{
 let enter,release;const entered=new Promise(resolve=>enter=resolve),waiting=new Promise(resolve=>release=resolve);
 const f=fixture({async intercept(path){if(path==='/api/sources/credentials'){enter();await waiting;}}});await f.login();
 const ui=createImportUI(f.host);t.after(()=>ui.reset());
 const dialog={open:true},button={dataset:{importAction:'credentials'},textContent:'管理连接',isConnected:true,closest:()=>dialog};
 const reading=ui.click(button);await entered;
 assert.equal(button.disabled,true);
 dialog.open=false;f.host.closeDialog();release();await reading;
 assert.equal(f.dialog,'');assert.equal(button.disabled,false);
});

test('text and file entry do not request teaching credentials',async t=>{
 const f=fixture({intercept(path){
  if(path==='/api/materials/limits')return {limits:{asset_bytes:20971520,account_bytes:209715200}};
  if(path==='/api/materials/imports')return {jobs:[]};
 }});await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 assert.equal(f.calls.some(call=>call.path==='/api/sources/credentials'),false);
 await ui.click({dataset:{importSource:'files'}});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.calls.some(call=>call.path==='/api/sources/credentials'),false);
 assert.doesNotMatch(ui.render(),/id="material-files"[^>]*disabled/);
});

test('a slow teaching credential read cannot lock text drafting or file selection',async t=>{
 const gate=heldRequest();t.after(gate.release);
 const f=fixture({async intercept(path){
  if(path==='/api/sources/credentials'){await gate.wait();return {configured:true,username:'A-campus'};}
  if(path==='/api/materials/limits')return {limits:{asset_bytes:20971520,account_bytes:209715200}};
  if(path==='/api/materials/imports')return {jobs:[]};
 }});await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const reading=ui.click({dataset:{importSource:'teaching'}});await gate.entered;
 await ui.click({dataset:{importSource:'text'}});
 ui.input({target:{id:'import-text',value:'下一段草稿'}});
 assert.match(ui.render(),/>下一段草稿<\/textarea>/);
 assert.doesNotMatch(ui.render(),/id="import-(?:text|published)"[^>]*disabled/);
 await ui.click({dataset:{importSource:'files'}});
 assert.doesNotMatch(ui.render(),/id="material-files"[^>]*disabled/);
 gate.release();await reading;
});

test('teaching read errors remain scoped and may retry without clearing a text draft',async t=>{
 let failed=false;
 const f=fixture({intercept(path){if(path==='/api/sources/credentials'&&!failed){failed=true;throw new Error('合成连接读取失败');}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 ui.input({target:{id:'import-text',value:'保留的文本草稿'}});
 await ui.click({dataset:{importSource:'teaching'}});
 assert.match(ui.render(),/data-import-action="refresh-credentials"/);
 await ui.click({dataset:{importSource:'text'}});
 assert.match(ui.render(),/>保留的文本草稿<\/textarea>/);
 assert.doesNotMatch(ui.render(),/合成连接读取失败|data-import-action="refresh-credentials"/);
 await ui.click({dataset:{importSource:'teaching'}});
 assert.match(ui.render(),/A-campus/);
});

test('a late credential response cannot replace the next account connection state',async t=>{
 const gate=heldRequest();t.after(gate.release);let readingA=true;
 const f=fixture({async intercept(path){if(path==='/api/sources/credentials'&&readingA){readingA=false;await gate.wait();return {configured:true,username:'A-old-campus'};}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const reading=ui.click({dataset:{importSource:'teaching'}});await gate.entered;
 await f.login('B');await ui.load();await ui.click({dataset:{importSource:'teaching'}});
 assert.match(ui.render(),/B-campus/);
 gate.release();await reading;
 assert.match(ui.render(),/B-campus/);assert.doesNotMatch(ui.render(),/A-old-campus/);
});

test('failed candidate reads leave text drafting and independent teaching reads available',async t=>{
 const f=fixture({intercept(path){if(path==='/api/recognition/batches')throw new Error('合成候选读取失败');}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 assert.equal(ui.taskReviewsReady(),true);assert.match(ui.taskReviewsStatus(),/重试读取待核对/);
 ui.input({target:{id:'import-text',value:'候选失败后仍可编辑'}});
 assert.match(ui.render(),/>候选失败后仍可编辑<\/textarea>/);
 assert.doesNotMatch(ui.render(),/id="import-(?:text|published)"[^>]*disabled/);
 await ui.click({dataset:{importSource:'teaching'}});assert.match(ui.render(),/A-campus/);
});

test('pending text submission captures its payload and preserves a newer editable draft',async t=>{
 const gate=heldRequest();t.after(gate.release);
 const f=fixture({async intercept(path,body,method){if(path==='/api/recognition/text-jobs'&&method==='POST'){
  await gate.wait();return {contract_version:'text-import-jobs/0.1',jobs:[{request_id:body.request_id,state:'recognizing',attempt:1,created_at:'2026-10-23T01:00:00Z',updated_at:'2026-10-23T01:00:00Z',error:null}]};
 }}});await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const submitting=ui.submit(fakeForm('text',{text:'第一段已提交通知',published:'2026-10-23T09:00:00'}));await gate.entered;
 ui.input({target:{id:'import-text',value:'第二段未提交草稿'}});
 ui.input({target:{id:'import-published',value:'2026-10-24T10:30:00'}});
 assert.doesNotMatch(ui.render(),/id="import-(?:text|published)"[^>]*disabled/);
 assert.match(ui.render(),/<button class="btn primary" type="submit" disabled/);
 gate.release();await submitting;
 assert.match(ui.render(),/>第二段未提交草稿<\/textarea>/);
 assert.match(ui.render(),/value="2026-10-24T10:30:00"/);
 assert.match(ui.render(),/<button class="btn primary" type="submit" disabled/);
 const post=f.calls.find(call=>call.path==='/api/recognition/text-jobs'&&call.method==='POST');
 assert.equal(post.body.records[0].text,'第一段已提交通知');
 assert.equal(post.body.records[0].published_at.value,'2026-10-23T09:00:00+08:00');
});

test('a rejected text submission leaves the newer draft untouched',async t=>{
 const gate=heldRequest();t.after(gate.release);
 const f=fixture({async intercept(path,body,method){if(path==='/api/recognition/text-jobs'&&method==='POST'){await gate.wait();throw new Error('合成提交失败');}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const submitting=ui.submit(fakeForm('text',{text:'失败的旧提交',published:'2026-10-23T09:00:00'}));await gate.entered;
 ui.input({target:{id:'import-text',value:'新的待提交通知'}});gate.release();await submitting;
 assert.match(ui.render(),/>新的待提交通知<\/textarea>/);
 assert.doesNotMatch(ui.render(),/id="import-text"[^>]*disabled/);
});

test('credential storage errors remain visible in the connection dialog',async()=>{
 const f=fixture({credentialError:{status:503,message:'凭据加密依赖未安装；请安装 server/requirements.txt'}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importAction:'credentials'}});
 const form=fakeForm('credentials',{username:'campus-user',password:'campus-password'});
 await ui.submit(form);assert.match(form.error.textContent,/无法保存教学网连接/);assert.equal(form.error.hidden,false);
 assert.doesNotMatch(form.error.textContent,/campus-user|campus-password/);
});

test('refreshing candidates prevents a concurrent ignore from being overwritten by an old read',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 const gate=f.defer(),reading=ui.load();await gate.entered;
 const disabled=/data-import-ignore="candidate-A" disabled/.test(ui.render());
 await ui.click({dataset:{importIgnore:'candidate-A'}});
 const premature=f.calls.filter(call=>call.path.endsWith('/resolve')).length;
 gate.release();await reading;
 await ui.click({dataset:{importIgnore:'candidate-A'}});
 assert.equal(disabled,true);
 assert.equal(premature,0);
 assert.doesNotMatch(ui.render(),/data-import-review="candidate-A"/);
 assert.match(ui.render(),/data-import-reopen="candidate-A"/);
});

test('a delayed ordinary refresh cannot replace newer background recognition results',async()=>{
 const {syncRun}=await import('./sync-fixtures.js');
 const f=fixture({intercept(path,body,method){
  if(path==='/api/sources/sync-runs'&&method==='GET')return {contract_version:'source-sync/0.1',runs:[syncRun()]};
 }});
 await f.login();const ui=createImportUI(f.host);await ui.load();
 const gate=f.defer(),reading=ui.load();await gate.entered;
 Object.assign(f.batches.A[0].items[0],{state:'ignored',version:4});
 await ui.loadSourceSync();
 gate.release();await reading;
 assert.doesNotMatch(ui.render(),/data-import-review="candidate-A"/);
 assert.match(ui.render(),/data-import-reopen="candidate-A"/);
});

test('active teaching sync permits navigation, text recognition and candidate review but not another sync',async(t)=>{
 const {syncRun}=await import('./sync-fixtures.js');
 const f=fixture({intercept(path,body,method){
  if(path==='/api/sources/sync-runs'&&method==='GET')return {contract_version:'source-sync/0.1',runs:[syncRun({state:'recognizing',states:['running']})]};
  if(path.endsWith('/stop'))return syncRun({state:'stopped',states:['interrupted']});
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.loadSourceSync();
 await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));
 let html=ui.render();
 assert.match(html,/data-import-action="sync" >.*停止导入/);
 assert.match(html,/data-import-action="credentials" disabled/);
 assert.doesNotMatch(html,/data-import-(?:review|ignore)="candidate-A" disabled/);
 for(const name of ['text','files','teaching']) {
  await ui.click({dataset:{importSource:name}});
  if(name==='text')assert.match(ui.render(),/data-import-form="text"/);
  if(name==='files')assert.match(ui.render(),/id="material-files"/);
 }
 await ui.click({dataset:{importReview:'candidate-A'}});assert.match(f.dialog,/核对任务/);
 const form=fakeForm('review',{action:'create',title:'提交报告',description:'明确要求',course:'概率论',date:'2026-10-20',time:''},{candidate:'candidate-A'});
 await ui.submit(form);assert.equal(f.state.tasks.length,1);assert.equal(f.dialog,'');
 await ui.submit(fakeForm('text',{text:'独立文本识别',published:'2026-10-14T12:00:00'}));
 await ui.click({dataset:{importAction:'sync'}});
 assert.equal(f.calls.filter(c=>c.path==='/api/recognition/text-jobs'&&c.method==='POST').length,1);
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/sync-runs'&&c.method==='POST').length,0);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/stop')).length,1);
});

test('a delayed background read cannot resurrect a candidate resolved during sync',async(t)=>{
 const {syncRun}=await import('./sync-fixtures.js');
 const f=fixture({intercept(path,body,method){
  if(path==='/api/sources/sync-runs'&&method==='GET')return {contract_version:'source-sync/0.1',runs:[syncRun({state:'recognizing',states:['running']})]};
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const gate=f.defer(),reading=ui.loadSourceSync();t.after(gate.release);await gate.entered;
 await ui.click({dataset:{importIgnore:'candidate-A'}});
 gate.release();await reading;
 assert.doesNotMatch(ui.render(),/data-import-review="candidate-A"/);
 assert.match(ui.render(),/data-import-reopen="candidate-A"/);
 assert.doesNotMatch(ui.render(),/导入结果已保存，刷新失败/);
});

test('a delayed file-job refresh cannot resurrect an ignored candidate',async t=>{
 const f=fixture({intercept(path){
  if(path==='/api/materials/limits')return {limits:{asset_bytes:20971520,account_bytes:209715200}};
  if(path==='/api/materials/imports')return {jobs:[{job_id:'file-one',status:'succeeded',items:[]}]};
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const gate=f.defer();t.after(gate.release);
 await ui.click({dataset:{importSource:'files'}});await gate.entered;
 await ui.click({dataset:{importIgnore:'candidate-A'}});
 gate.release();await new Promise(resolve=>setImmediate(resolve));
 assert.doesNotMatch(ui.render(),/data-import-review="candidate-A"/);
 assert.match(ui.render(),/data-import-reopen="candidate-A"/);
});

test('removed submission directory has no entry or background reads',async t=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});await new Promise(resolve=>setImmediate(resolve));
 assert.doesNotMatch(ui.render(),/作业与提交状态|data-submission-/);
 assert.equal(f.calls.filter(call=>call.path.startsWith('/api/sources/submissions')).length,0);
});

test('mail edit opens and focuses PKU settings directly while account settings retain their management row',async t=>{
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());
 const before=f.calls.length;
 await ui.click({dataset:{importAction:'email-credentials'}});
 assert.match(f.dialog,/data-email-settings-dialog/);assert.match(f.dialog,/data-connection-settings="pku-email"/);
 assert.match(f.dialog,/data-import-form="email-credentials"/);assert.doesNotMatch(f.dialog,/data-account-settings-panel/);
 assert.deepEqual(f.calls.slice(before).map(call=>call.path).sort(),['/api/sources/email/credentials','/api/sources/email/preferences']);
 assert.deepEqual(f.focuses,['pku-email-settings','pku-email-settings']);
 assert.match(ui.renderEmailSettings(),/data-account-section="pku-email"/);assert.match(ui.renderEmailSettings(),/>管理连接<\/button>/);
 const form=fakeForm('email-credentials',{});
 await ui.click({dataset:{importAction:'remove-email-credentials'},closest:()=>form});
 assert.equal(f.dialog,'');assert.match(ui.renderEmailSettings(true),/保存连接/);assert.doesNotMatch(ui.renderEmailSettings(true),/student@stu.pku.edu.cn/);
});

test('failed account email settings read offers retry without an editable unknown connection',async t=>{
 let fail=true;
 const f=fixture({intercept:async(path,body,method)=>{
  if(fail&&path==='/api/sources/email/preferences'&&method==='GET')throw new Error('暂时无法读取设置');
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 assert.match(f.dialog,/重试读取设置/);
 assert.doesNotMatch(f.dialog,/data-import-form="email-credentials"/);
 fail=false;await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 assert.match(f.dialog,/data-import-form="email-credentials"/);
});

test('a settings read started during saving cannot overwrite the saved preference',async t=>{
 let reading=false,release;
 const pending=new Promise(resolve=>release=resolve);
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},intercept:async(path,body,method)=>{
  if(reading&&path==='/api/sources/email/preferences'&&method==='GET')return await pending;
 }};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());
 await ui.click({dataset:{importAction:'email-credentials'}});await ui.click({dataset:{importAction:'email-settings'}});
 reading=true;
 const loading=ui.loadEmailSettings();
 await ui.submit(fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:'',consent:'true',process_all:'true'}));
 release({consent_granted:false,process_all:false});await loading;
 assert.match(ui.renderEmailSettings(true),/name="consent"[^>]*checked/);
 assert.match(ui.renderEmailSettings(true),/name="process_all"[^>]*checked/);
});

test('closing or replacing email details while loading prevents a late popup reopen',async t=>{
 let hold=false,release;
 const pending=new Promise(resolve=>release=resolve);
 const f=fixture({intercept:async(path,body,method)=>{
  if(hold&&path==='/api/sources/email/preferences'&&method==='GET')return await pending;
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());
 await ui.click({dataset:{importAction:'email-credentials'}});
 hold=true;const opening=ui.click({dataset:{importAction:'email-settings'}});
 assert.match(f.dialog,/正在读取邮箱设置/);
 f.host.closeDialog();
 release({consent_granted:false,process_all:false});await opening;
 assert.equal(f.dialog,'');
});

test('50 selected emails can start recognition, and missing consent opens settings instead of disabling',async t=>{
 const messages=Array.from({length:50},(_,i)=>({input_id:`email:batch:${i}`,subject:`通知 ${i}`,state:'unprocessed',excluded:false}));
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:false,process_all:false},emailPreview:{messages,summary:{total:50},next_offset:null}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());
 await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-preview'}});await ui.click({dataset:{importAction:'email-cycle-page-size'}});await ui.click({dataset:{importAction:'email-select-page'}});
 const button=ui.render().match(/<button[^>]*data-import-action="email-start-job"[^>]*>/)?.[0];
 assert.ok(button);assert.doesNotMatch(button,/disabled/);assert.match(ui.render(),/识别邮件<span class="email-selection-badge" aria-hidden="true">50<\/span>/);assert.match(button,/aria-label="识别邮件，已选 50 封邮件"/);
 await ui.click({dataset:{importAction:'email-start-job'}});
 assert.match(f.dialog,/data-email-settings-dialog/);
 assert.equal(f.calls.some(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST'),false);
 await ui.submit(fakeForm('email-credentials',{address:'student@stu.pku.edu.cn',username:'student',password:'',consent:'true'}));
 await ui.click({dataset:{importAction:'email-start-job'}});
 const job=f.calls.find(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST');
 assert.equal(job.body.input_ids.length,50);
 assert.deepEqual(new Set(job.body.input_ids),new Set(messages.map(message=>message.input_id)));
 await ui.click({dataset:{importAction:'email-stop-job'}});
 assert.ok(f.calls.some(call=>call.path==='/api/sources/email/jobs/email-job-1/stop'&&call.method==='POST'));
 assert.equal(f.calls.some(call=>call.path==='/api/recognition/batches'&&call.method==='POST'),false);
});

test('clear selection disables visibly and overrides process-all for the current selection only',async t=>{
 const id='email:clear:1';
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:true},emailPreview:{messages:[{input_id:id,subject:'Synthetic clear selection',state:'unprocessed',excluded:false}],summary:{total:50},next_offset:null}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-preview'}});
 const clear=()=>ui.render().match(/<button[^>]*data-import-action="email-clear-selection"[^>]*>/)[0];
 assert.doesNotMatch(clear(),/disabled/);
 await ui.click({dataset:{importAction:'email-clear-selection'}});
 assert.match(clear(),/disabled/);assert.match(ui.render(),/已选 0 封/);
 assert.match(ui.render().match(/<button[^>]*data-import-action="email-start-job"[^>]*>/)[0],/disabled/);
 await ui.click({dataset:{importAction:'email-start-job'}});
 assert.equal(f.calls.some(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST'),false);
 await ui.click({dataset:{importAction:'email-select-page'}});assert.doesNotMatch(clear(),/disabled/);
 await ui.click({dataset:{importAction:'email-start-job'}});
 const job=f.calls.find(call=>call.path==='/api/sources/email/jobs'&&call.method==='POST');assert.deepEqual(job.body.input_ids,[id]);assert.equal(job.body.range,undefined);
 assert.equal(f.calls.some(call=>call.path==='/api/sources/email/preferences'&&call.method==='POST'),false);
});

test('bulk recognition update starts directly for the loaded full range and skips unknown versions',async t=>{
 const done={job_id:'done',state:'succeeded',counts:{selected:100,processed:98,cached:98,excluded:2,failed:0,pending:0},items:[]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[done],emailReprocessJob:{...done,kind:'reprocess',job_id:'bulk-update',state:'queued'},emailPreview:{messages:[{input_id:'email:bulk:100',subject:'Visible current mail',state:'no_tasks',policy_status:'current',excluded:false}],summary:{total:100},next_offset:50,cache:{snapshot_id:'range-100'},recognition_updates:{available:true,outdated_count:3,unknown_count:2,policy_version:'current-policy',selection_token:'confirmed-targets'}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 ui.input({target:{dataset:{emailLimit:''},value:'100'}});await ui.click({dataset:{importAction:'email-preview'}});
 assert.match(ui.render(),/data-import-action="email-update-recognition"[^>]*>更新识别<\/button>/);assert.match(ui.render(),/旧版缓存 3/);assert.doesNotMatch(ui.render(),/更新识别（/);
 ui.input({target:{dataset:{emailLimit:''},value:'999'}});
 let opened=0;f.host.openDialog=()=>{opened++;};
 assert.match(ui.render(),/aria-describedby="email-update-tooltip"/);assert.match(ui.render(),/id="email-update-tooltip" class="email-sort-tooltip" role="tooltip">复用已保存正文更新整个范围/);
 await ui.click({dataset:{importAction:'email-update-recognition'}});
 assert.equal(opened,0,'updating outdated results opens no confirmation');
 const request=f.calls.find(c=>c.path==='/api/sources/email/reprocess');
 assert.deepEqual(request.body,{operation_id:request.body.operation_id,range:{limit:100,start_date:null},snapshot_id:'range-100',policy_version:'current-policy',selection_token:'confirmed-targets',include_unknown:false,consent:true});
 assert.doesNotMatch(ui.render(),/data-import-action="email-update-recognition"/,'updates unavailable while a job runs');
});

test('pagination and page-size switches repaginate all loaded mail with zero API calls',async t=>{
 let deny=false;
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},intercept:async(path,body,method)=>{
  if(deny)throw new Error('Navigation must not request the server');
  if(path==='/api/sources/email/preview'&&method==='POST')return {messages:Array.from({length:200},(_,i)=>({input_id:'email:local:'+(i+1),subject:'Mail '+(i+1),state:'unprocessed',excluded:false})),summary:{total:200},next_offset:null,cache:{snapshot_id:'full-local',complete:true,miss:false}};
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 ui.input({target:{dataset:{emailLimit:''},value:'200'}});await ui.click({dataset:{importAction:'email-preview'}});
 const request=f.calls.filter(c=>c.path==='/api/sources/email/preview').at(-1).body;assert.equal(request.load_all,true);assert.equal(request.refresh,true);
 const count=f.calls.length;deny=true;
 assert.equal((ui.render().match(/data-email-message=/g)||[]).length,25);assert.match(ui.render(),/已选 25 封/);
 await ui.click({dataset:{importAction:'email-clear-selection'}});
 ui.input({target:{dataset:{emailMessage:'email:local:200'},checked:true}});
 await ui.click({dataset:{importAction:'email-next'}});assert.match(ui.render(),/第 2 页/);assert.match(ui.render(),/Mail 175/);
 ui.input({target:{dataset:{emailMessage:'email:local:175'},checked:true}});assert.match(ui.render(),/已选 2 封/);
 ui.input({target:{dataset:{emailLimit:''},value:'999'}});
 await ui.click({dataset:{importAction:'email-cycle-page-size'}});assert.match(ui.render(),/每页 50 封/);assert.equal((ui.render().match(/data-email-message=/g)||[]).length,50);
 await ui.click({dataset:{importAction:'email-cycle-page-size'}});assert.match(ui.render(),/每页 100 封/);assert.equal((ui.render().match(/data-email-message=/g)||[]).length,100);
 await ui.click({dataset:{importAction:'email-next'}});assert.match(ui.render(),/第 2 页/);assert.match(ui.render(),/Mail 100/);
 await ui.click({dataset:{importAction:'email-prev'}});assert.match(ui.render(),/第 1 页/);
 await ui.click({dataset:{importAction:'email-cycle-page-size'}});assert.match(ui.render(),/每页 25 封/);assert.match(ui.render(),/已选 2 封/);
 assert.equal(f.calls.length,count,'paging and all size switches make zero HTTP requests');
});

test('local pagination restores its size and offset per account with cache-only loading',async t=>{
 const old=globalThis.localStorage,values=new Map();globalThis.localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};t.after(()=>{if(old===undefined)delete globalThis.localStorage;else globalThis.localStorage=old;});
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:Array.from({length:150},(_,i)=>({input_id:'email:persist:'+i,subject:'Mail '+i,state:'unprocessed',excluded:false})),summary:{total:150},next_offset:null,cache:{snapshot_id:'persist-all',complete:true,miss:false}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const count=f.calls.length;await ui.click({dataset:{importAction:'email-cycle-page-size'}});await ui.click({dataset:{importAction:'email-next'}});assert.equal(f.calls.length,count);
 ui.reset();await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 let request=f.calls.filter(c=>c.path==='/api/sources/email/preview').at(-1).body;assert.equal(request.page_size,50);assert.equal(request.offset,0);assert.equal(request.cache_only,true);assert.equal(request.load_all,true);assert.match(ui.render(),/第 2 页/);assert.match(ui.render(),/Mail 50/);
 await f.login('B');await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 request=f.calls.filter(c=>c.path==='/api/sources/email/preview').at(-1).body;assert.equal(request.page_size,25);assert.match(ui.render(),/第 1 页/);
});

test('fetching mail keeps its mounted controls stable and rejects duplicate clicks',async t=>{
 let release;
 const pending=new Promise(resolve=>release=resolve);
 const original={messages:[{input_id:'email:stable:1',subject:'Existing header remains visible',state:'unprocessed',excluded:false}],summary:{total:1},next_offset:null,cache:{snapshot_id:'stable',miss:false,complete:true}};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:original,intercept:async(path,body,method)=>{if(path==='/api/sources/email/preview'&&method==='POST'&&body.refresh)return pending;}});
 let renders=0;const busyUpdates=[];f.host.render=()=>renders++;f.host.setEmailPreviewBusy=(busy,options)=>{busyUpdates.push({busy,...options});return true;};
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const before=renders;
 const loading=ui.click({dataset:{importAction:'email-preview'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(renders,before,'starting a fetch updates busy attributes without rebuilding the page');
 assert.deepEqual(busyUpdates.at(-1),{busy:true,fetching:true});
 const button=ui.render().match(/<button[^>]*data-import-action="email-preview"[^>]*>.*?<\/button>/)[0];
 assert.match(button,/aria-busy="true"/);assert.match(button,/aria-disabled="true"/);assert.match(button,/>获取邮件<\/button>/);assert.doesNotMatch(button,/正在获取/);
 assert.doesNotMatch(button,/\sdisabled(?:\s|=|>)/,'loading does not dim the button or drop its focus');
 assert.match(ui.render(),/Existing header remains visible/);
 await ui.click({dataset:{importAction:'email-preview'}});
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/preview'&&c.body?.refresh).length,1);
 release({...original,messages:[{...original.messages[0],subject:'Updated header'}]});await loading;
 assert.equal(renders,before+1,'only finished results trigger a page render');
 assert.match(ui.render(),/Updated header/);
 assert.match(ui.render().match(/<button[^>]*data-import-action="email-preview"[^>]*>/)[0],/aria-busy="false"/);
});

test('a partial restored mail cache never hydrates missing headers during local navigation',async t=>{
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages:Array.from({length:30},(_,i)=>({input_id:'email:partial:'+i,subject:'Cached '+i,state:'unprocessed',excluded:false})),summary:{total:100},next_offset:null,cache:{snapshot_id:'partial-cache',complete:false,miss:true}}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const request=f.calls.filter(c=>c.path==='/api/sources/email/preview').at(-1).body;assert.equal(request.cache_only,true);assert.equal(request.load_all,true);
 assert.match(ui.render(),/已加载 30 \/ 100/);const count=f.calls.length;
 await ui.click({dataset:{importAction:'email-next'}});assert.equal((ui.render().match(/data-email-message=/g)||[]).length,5);
 await ui.click({dataset:{importAction:'email-cycle-page-size'}});assert.equal((ui.render().match(/data-email-message=/g)||[]).length,30);
 await ui.click({dataset:{importAction:'email-cycle-page-size'}});assert.equal((ui.render().match(/data-email-message=/g)||[]).length,30);
 assert.equal(f.calls.length,count,'missing headers remain missing until explicit retrieval');
 scenario.emailPreview={...scenario.emailPreview,messages:Array.from({length:100},(_,i)=>({input_id:'email:partial:'+i,subject:'Cached '+i,state:'unprocessed',excluded:false})),cache:{snapshot_id:'complete-cache',complete:true,miss:false}};
 await ui.click({dataset:{importAction:'email-preview'}});
 const explicit=f.calls.filter(c=>c.path==='/api/sources/email/preview').at(-1).body;assert.equal(explicit.refresh,true);assert.equal(explicit.cache_only,false);assert.equal(explicit.load_all,true);
 assert.equal((ui.render().match(/data-email-message=/g)||[]).length,100);
});

test('job status updates remain visible when navigating to another cached page',async t=>{
 const job={job_id:'local-status',state:'running',counts:{selected:40,processed:1,pending:39},items:[{input_id:'email:status:30',state:'has_tasks',task_count:2,request_id:'recognized-30'}]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[job],emailJob:job,emailPreview:{messages:Array.from({length:40},(_,i)=>({input_id:'email:status:'+i,subject:'Status mail '+i,state:'unprocessed',excluded:false})),summary:{total:40},next_offset:null,cache:{snapshot_id:'status-cache',complete:true,miss:false}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-poll'}});const count=f.calls.length;
 await ui.click({dataset:{importAction:'email-next'}});assert.match(ui.render(),/Status mail 30/);assert.match(ui.render(),/2项任务/);assert.equal(f.calls.length,count);
});

test('unchanged email job summaries skip the full item fetch and rerender',async t=>{
 const job={job_id:'steady',state:'running',revision:4,counts:{selected:3,processed:1,pending:2},items:[{input_id:'email:steady',state:'has_tasks',task_count:1}]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[job],emailJob:job,emailPreview:{messages:[],summary:{total:0},next_offset:null}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-poll'}});
 const fullGets=f.calls.filter(c=>c.path==='/api/sources/email/jobs/steady').length, renders=f.renderCalls;
 await ui.click({dataset:{importAction:'email-poll'}});
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/jobs/steady').length,fullGets);
 assert.ok(f.calls.some(c=>c.path==='/api/sources/email/jobs/steady?summary=1'));
 assert.equal(f.renderCalls,renders+2,'the manual poll action renders only its busy state and completion, with no unchanged-response render');
});

test('email job revision changes fetch item states and visibility resumes one poll loop',async t=>{
 const previous=globalThis.document,doc=new EventTarget();doc.hidden=true;let visibilityAdds=0,visibilityRemoves=0;
 const add=doc.addEventListener.bind(doc),remove=doc.removeEventListener.bind(doc);
 doc.addEventListener=(type,...args)=>{if(type==='visibilitychange')visibilityAdds++;return add(type,...args);};
 doc.removeEventListener=(type,...args)=>{if(type==='visibilitychange')visibilityRemoves++;return remove(type,...args);};
 globalThis.document=doc;
 t.after(()=>{globalThis.document=previous;});
 const job={job_id:'moving',state:'running',revision:2,counts:{selected:1,processed:0,pending:1},items:[{input_id:'email:moving',state:'unprocessed'}]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[job],emailJob:{...job,revision:3,items:[{input_id:'email:moving',state:'has_tasks',task_count:2}]},emailPreview:{messages:[{input_id:'email:moving',subject:'Moving mail',state:'unprocessed',excluded:false}],summary:{total:1},next_offset:null}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/jobs/moving?summary=1').length,0,'hidden tab does not poll');
 doc.hidden=false;doc.dispatchEvent(new Event('visibilitychange'));await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/jobs/moving').length,1,'new revision retrieves details once');
 assert.match(ui.render(),/2项任务/);
 const before=f.calls.filter(c=>c.path==='/api/sources/email/jobs/moving?summary=1').length;
 doc.dispatchEvent(new Event('visibilitychange'));await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/jobs/moving?summary=1').length,before,'visible event does not add a second timer while a timer is scheduled');
 const calls=f.calls.length;ui.reset();
 assert.equal(visibilityRemoves,visibilityAdds,'reset removes the listener from the same document it was attached to');
 doc.dispatchEvent(new Event('visibilitychange'));await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(f.calls.length,calls,'a reset controller does not resume polling');
});

test('email job stores the full response revision when it advances after the summary read',async t=>{
 const job={job_id:'revision-race',state:'running',revision:4,counts:{selected:1,processed:0,pending:1},items:[]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[job],emailJob:job,emailJobFull:{...job,revision:5},emailSummaryRevision:4,emailPreview:{messages:[],summary:{total:0},next_offset:null}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-poll'}});
 const fullGets=()=>f.calls.filter(c=>c.path==='/api/sources/email/jobs/revision-race').length;
 assert.equal(fullGets(),1);
 f.scenario.emailSummaryRevision=5;
 await ui.click({dataset:{importAction:'email-poll'}});
 assert.equal(fullGets(),1,'the revision returned with the full details is used for the next summary comparison');
});

test('waiting reason changes render without fetching item details at an unchanged revision',async t=>{
 const job={job_id:'waiting-reason',state:'queued',revision:7,waiting_reason:'global_capacity',counts:{selected:1,processed:0,pending:1},items:[]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[job],emailJob:job,emailSummaryRevision:7,emailWaitingReason:'global_capacity',emailPreview:{messages:[],summary:{total:0},next_offset:null}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-poll'}});
 const fullGets=f.calls.filter(c=>c.path==='/api/sources/email/jobs/waiting-reason').length;
 assert.match(ui.render(),/等待系统处理名额/);
 f.scenario.emailWaitingReason='provider_backoff';
 await ui.click({dataset:{importAction:'email-poll'}});
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/jobs/waiting-reason').length,fullGets);
 assert.match(ui.render(),/等待邮件服务恢复/,'visible waiting metadata changes rerender the status');
});

test('a stale full job response cannot replace a newer selected job or surface its error',async t=>{
 let entered,rejectFull;
 const reached=new Promise(resolve=>entered=resolve),fullWait=new Promise((_,reject)=>rejectFull=reject);
 const oldJob={job_id:'old-job',state:'running',revision:1,counts:{selected:1,processed:0,pending:1},items:[]};
 const newJob={job_id:'new-job',state:'running',revision:9,counts:{selected:9,processed:0,pending:9},items:[]};
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[oldJob],emailJob:oldJob,emailPreview:{messages:[],summary:{total:0},next_offset:null},intercept:async path=>{if(path==='/api/sources/email/jobs/old-job'){entered();await fullWait;}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const oldPoll=ui.click({dataset:{importAction:'email-poll'}});await reached;
 f.scenario.emailJobs=[newJob];
 await ui.click({dataset:{importSource:'text'}});await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 rejectFull(new Error('stale full read failed'));await oldPoll;
 assert.match(ui.render(),/0 \/ 9/,'the new job remains selected after the old full request settles');
 assert.doesNotMatch(ui.render(),/继续读取状态/,'a stale request error does not set the new job poll error');
});

test('whole-range selection checks nonexcluded rows consistently across local pages',async t=>{
 const messages=Array.from({length:60},(_,i)=>({input_id:'email:bulk-check:'+i,subject:'Bulk row '+i,state:i===2?'excluded':'unprocessed',excluded:i===2}));
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages,summary:{total:60},next_offset:null,cache:{snapshot_id:'bulk-checkbox',complete:true,miss:false}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const inputs=()=>[...ui.render().matchAll(/<input type="checkbox" data-email-message="([^"]+)"([^>]*)>/g)];
 await ui.click({dataset:{importAction:'email-clear-selection'}});const calls=f.calls.length;
 await ui.click({dataset:{importAction:'email-select-range'}});
 assert.match(ui.render().match(/<button[^>]*data-import-action="email-select-range"[^>]*>/)[0],/disabled/);
 assert.match(ui.render(),/整个范围已选择/);
 for(const row of inputs())assert.equal(/\bchecked\b/.test(row[2]),row[1]!=='email:bulk-check:2');
 await ui.click({dataset:{importAction:'email-next'}});assert.equal(inputs().length,25);assert.ok(inputs().every(row=>/\bchecked\b/.test(row[2])));
 await ui.click({dataset:{importAction:'email-cycle-page-size'}});await ui.click({dataset:{importAction:'email-cycle-page-size'}});
 assert.equal(inputs().length,60);assert.equal(inputs().filter(row=>/\bchecked\b/.test(row[2])).length,59);
 assert.equal(f.calls.length,calls);
 await ui.click({dataset:{importAction:'email-clear-selection'}});assert.ok(inputs().every(row=>!/\bchecked\b/.test(row[2])));
 for(const message of messages.filter(m=>!m.excluded))ui.input({target:{dataset:{emailMessage:message.input_id},checked:true}});
 assert.match(ui.render().match(/<button[^>]*data-import-action="email-select-range"[^>]*>/)[0],/disabled/);
});

test('sort and filter cover the entire fetched range before pagination with no requests',async t=>{
 const messages=Array.from({length:60},(_,i)=>({input_id:`email:global:${i+1}`,subject:`Global ${i+1}`,state:i===0?'has_tasks':'no_tasks',pending_review_count:i===0?1:0,excluded:false}));
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailPreview:{messages,summary:{total:60},next_offset:null}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await ui.click({dataset:{importAction:'email-preview'}});
 const ids=()=>[...ui.render().matchAll(/data-email-message="email:global:(\d+)"/g)].map(m=>Number(m[1]));
 const calls=f.calls.length, selection=ui.render().match(/已选 \d+ 封/)[0];
 assert.deepEqual(ids(),[1,...Array.from({length:24},(_,i)=>60-i)],'oldest pending review takes priority across pages');
 await ui.click({dataset:{importAction:'email-next'}});assert.match(ui.render(),/第 2 页/);
 await ui.click({dataset:{importAction:'email-cycle-sort'}});
 assert.deepEqual(ids(),Array.from({length:25},(_,i)=>60-i));assert.match(ui.render(),/第 1 页/);
 await ui.click({dataset:{importAction:'email-next'}});assert.deepEqual(ids(),Array.from({length:25},(_,i)=>35-i));
 await ui.click({dataset:{importAction:'email-cycle-sort'}});assert.deepEqual(ids(),Array.from({length:25},(_,i)=>i+1));
 await ui.click({dataset:{importAction:'email-next'}});assert.deepEqual(ids(),Array.from({length:25},(_,i)=>i+26));
 await ui.click({dataset:{importAction:'email-cycle-sort'}});
 assert.deepEqual(ids(),[1,...Array.from({length:24},(_,i)=>60-i)],'third click restores attention ordering across the fetched range');
 ui.input({target:{dataset:{emailFilter:''},value:'pending_review'}});
 assert.deepEqual(ids(),[1],'filter finds mail outside the previously visible page');assert.match(ui.render(),/匹配 1 封/);assert.doesNotMatch(ui.render(),/data-import-action="email-next"/);
 assert.ok(ui.render().includes(selection),'reordering preserves selected identities');
 assert.equal(f.calls.length,calls,'all sort, filter, and pagination changes stay local');
});

test('email job states render progress and exactly one applicable action',async t=>{
 const {icon}=await import('../src/icons.js');
 for(const [state,kind,failed,pending,outdated,expected] of [
  ['queued','import',0,38,3,'email-stop-job'],
  ['running','reprocess',3,35,3,'email-stop-job'],
  ['partial_success','reprocess',3,0,3,'email-reprocess-retry'],
  ['failed','import',42,0,3,'email-retry-job'],
  ['interrupted','reprocess',0,3,3,'email-reprocess-retry'],
  ['succeeded','reprocess',0,0,3,'email-update-recognition'],
  ['succeeded','reprocess',0,0,0,null],
 ]){
  const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailJobs:[{job_id:'unified',kind,state,counts:{selected:42,processed:42-failed-pending,cached:0,excluded:0,failed,pending},items:[]}],emailPreview:{messages:[],summary:{total:42},recognition_updates:{available:true,outdated_count:outdated,unknown_count:7}}});
  f.host.icon=icon;await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
  const html=ui.render(), actions=[...html.matchAll(/data-import-action="(email-(?:stop-job|reprocess-retry|retry-job|update-recognition))"/g)];
  assert.deepEqual(actions.map(match=>match[1]),expected?[expected]:[],state);
  if(expected)assert.match(html,/<button class="btn quiet email-job-action[^>]*>.*?<svg/);
  if(['queued','running'].includes(state)){
   assert.match(html,new RegExp(`role="progressbar"[^>]*aria-valuemax="42"[^>]*aria-valuenow="${42-pending}"`));
   assert.match(html,new RegExp(`email-job-progress-count">${42-pending} / 42`));
   assert.doesNotMatch(html,/已处理/);
  }else{
   assert.doesNotMatch(html,/role="progressbar"/);assert.match(html,/已处理/);
  }
  ui.reset();
 }
});

test('direct reprocess retry shows inline errors and reuses its operation id without a popup',async t=>{
 const id='email:direct:1';let attempts=0;
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[{job_id:'direct-failed',kind:'reprocess',state:'failed',counts:{selected:1,failed:1,pending:0},items:[{input_id:id,state:'failed'},{input_id:'email:direct:2',state:'no_tasks'}]}],intercept:async(path)=>{if(path==='/api/sources/email/reprocess'&&++attempts===1)throw new Error('temporary');}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 let opened=0;f.host.openDialog=()=>{opened++;};
 assert.match(ui.render(),/aria-describedby="email-retry-tooltip"/);assert.match(ui.render(),/id="email-retry-tooltip" class="email-sort-tooltip" role="tooltip">仅重试失败或中断邮件/);
 await ui.click({dataset:{importAction:'email-reprocess-retry'}});assert.match(ui.render(),/role="alert"/);assert.equal(opened,0);
 await ui.click({dataset:{importAction:'email-reprocess-retry'}});
 const calls=f.calls.filter(c=>c.path==='/api/sources/email/reprocess');assert.equal(calls.length,2);assert.equal(calls[0].body.operation_id,calls[1].body.operation_id);assert.deepEqual(calls[1].body.input_ids,[id]);assert.equal(opened,0);
 assert.equal(calls[1].body.consent,true);
 assert.equal(f.calls.some(call=>call.path.endsWith('/retry')&&call.method==='POST'),false);
});

test('direct retry still requires persistent body-processing consent',async t=>{
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:false,process_all:false},emailJobs:[{job_id:'no-consent',kind:'reprocess',state:'failed',counts:{failed:1},items:[{input_id:'email:consent:1',state:'failed'}]}]});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 await ui.click({dataset:{importAction:'email-reprocess-retry'}});assert.match(f.dialog,/data-email-settings-dialog/);assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/reprocess').length,0);
});

test('email notice candidates display and save as notices through review',async t=>{
 const f=fixture();await f.login();const b=f.batches.A[0];
 b.request.records[0].platform='email';b.items[0].candidate.content_type='notice';
 const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});
 assert.match(ui.render(),/新增通知/);
 await ui.click({dataset:{importReview:'candidate-A'}});
 assert.match(f.dialog,/核对通知/);assert.match(f.dialog,/通知名称/);assert.match(f.dialog,/确认加入通知/);
 await ui.submit(fakeForm('review',{action:'create',title:'上课地点调整',description:'理教408',course:'',date:'',time:''},{candidate:'candidate-A'}));
 const call=f.calls.find(c=>c.path==='/api/recognition/candidates/candidate-A/resolve');
 assert.equal(call.body.draft.content_type,'notice');assert.equal(call.body.reviewed,true);
});

test('direct outdated-cache update reports errors inline and retains its operation id',async t=>{
 let attempts=0;
 const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[{job_id:'done',state:'succeeded',counts:{},items:[]}],emailPreview:{messages:[],summary:{total:100},cache:{snapshot_id:'direct-update'},recognition_updates:{available:true,outdated_count:3,unknown_count:1,policy_version:'new-policy',selection_token:'stable-targets'}},intercept:async(path)=>{if(path==='/api/sources/email/reprocess'&&++attempts===1)throw new Error('temporary');}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 let opened=0;f.host.openDialog=()=>{opened++;};
 await ui.click({dataset:{importAction:'email-update-recognition'}});assert.match(ui.render(),/role="alert"/);
 await ui.click({dataset:{importAction:'email-update-recognition'}});
 const requests=f.calls.filter(c=>c.path==='/api/sources/email/reprocess');assert.equal(requests.length,2);assert.equal(requests[0].body.operation_id,requests[1].body.operation_id);assert.equal(requests[1].body.include_unknown,false);assert.equal(opened,0);
});

test('direct outdated-cache update blocks duplicates and still checks saved consent',async t=>{
 let release;const pending=new Promise(resolve=>release=resolve);
 const scenario={emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},emailJobs:[{job_id:'done',state:'succeeded',counts:{},items:[]}],emailPreview:{messages:[],summary:{total:50},cache:{snapshot_id:'direct-busy'},recognition_updates:{available:true,outdated_count:2,policy_version:'new-policy',selection_token:'targets'}},intercept:async path=>{if(path==='/api/sources/email/reprocess')await pending;}};
 const f=fixture(scenario);await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const first=ui.click({dataset:{importAction:'email-update-recognition'}});await new Promise(resolve=>setTimeout(resolve,0));await ui.click({dataset:{importAction:'email-update-recognition'}});
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/reprocess').length,1);release();await first;
 ui.reset();scenario.emailPreferences.consent_granted=false;await ui.load();await ui.click({dataset:{importSource:'email'}});await new Promise(resolve=>setTimeout(resolve,0));
 const before=f.calls.filter(c=>c.path==='/api/sources/email/reprocess').length;
 await ui.click({dataset:{importAction:'email-update-recognition'}});assert.equal(f.calls.filter(c=>c.path==='/api/sources/email/reprocess').length,before);assert.match(f.dialog,/data-email-settings-dialog/);
});

test('candidate confirmation stages different items continuously and rejects duplicate clicks',async t=>{
 const gate=heldRequest();let held=false;
 const f=fixture({async intercept(path){if(path.endsWith('/resolve')&&!held){held=true;await gate.wait();}}});
 const other=f.batch('A','second-request');other.items[0].id='candidate-second';f.batches.A.push(other);
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());t.after(gate.release);await ui.load();
 const draft={title:'立即显示的任务',notes:'合成内容',course:'合成课程',due:'2026-10-25',dueTime:'',reviewVersion:3};
 const first=ui.confirmTask('candidate-A',draft,true);
 const staged=await first.staged;await gate.entered;
 assert.equal(staged.id,first.taskId);assert.equal(staged.status,'done');
 assert.equal(f.state.tasks[0].id,first.taskId);assert.equal(ui.isReviewBusy('candidate-A'),false);assert.equal(ui.isReviewBusy(),false);
 await assert.rejects(ui.confirmTask('candidate-A',draft),/候选已变化/);
 const second=ui.confirmTask('candidate-second',{...draft,title:'连续确认的另一项'},false);
 const next=await second.staged;
 assert.equal(next.id,second.taskId);assert.notEqual(next.id,staged.id);
 assert.equal(f.state.tasks.length,2);assert.equal(ui.getReviewItems().length,0);
 assert.equal(f.calls.filter(call=>call.path.endsWith('/resolve')).length,1,'the second operation stages while the first network request waits');
 gate.release();const saved=await Promise.all([first,second]);
 assert.deepEqual(saved.map(task=>task.id),[first.taskId,second.taskId]);
 assert.equal(f.state.tasks.length,2);assert.equal(ui.isReviewBusy('candidate-A'),false);
});

test('a review form closes after durable staging and another candidate remains usable',async t=>{
 const gate=heldRequest();let held=false;
 const f=fixture({async intercept(path){if(path.endsWith('/resolve')&&!held){held=true;await gate.wait();}}});
 const other=f.batch('A','second-request');other.items[0].id='candidate-second';f.batches.A.push(other);
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());t.after(gate.release);await ui.load();
 await ui.click({dataset:{importReview:'candidate-A'}});
 const form=fakeForm('review',{action:'create',title:'第一项',description:'',course:'',date:'',time:''},{candidate:'candidate-A'});
 const first=ui.submit(form);await gate.entered;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.dialog,'');assert.equal(f.state.tasks[0].title,'第一项');
 await ui.click({dataset:{importReview:'candidate-second'}});
 assert.match(f.dialog,/data-candidate="candidate-second"/);
 const second=ui.submit(fakeForm('review',{action:'create',title:'第二项',description:'',course:'',date:'',time:''},{candidate:'candidate-second'}));
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(ui.isReviewBusy(),false);
 gate.release();await Promise.all([first,second]);
 assert.deepEqual(f.state.tasks.map(task=>task.title),['第一项','第二项']);
});

test('candidate state overlays a stale reread after acknowledgement',async t=>{
 const gate=heldRequest();
 const f=fixture({async intercept(path){if(path.endsWith('/resolve'))await gate.wait();}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());t.after(gate.release);await ui.load();
 const saving=ui.confirmTask('candidate-A',{title:'保留已经核对的任务',reviewVersion:3});await saving.staged;await gate.entered;
 const stale=f.defer(),reading=ui.loadTaskReviews();t.after(stale.release);await stale.entered;
 gate.release();await saving;stale.release();await reading;
 assert.equal(ui.getReviewItems().length,0);assert.equal(f.state.tasks.length,1);
 assert.doesNotMatch(ui.render(),/data-import-review="candidate-A"/);
});

test('failed review acknowledgement retains the staged task and retries its same operation identity',async t=>{
 let failed=false;
 const f=fixture({intercept(path){if(path.endsWith('/resolve')&&!failed){failed=true;throw new TypeError('synthetic network failure');}}});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const saving=ui.confirmTask('candidate-A',{title:'网络恢复后保存',reviewVersion:3});
 const staged=await saving.staged;await assert.rejects(saving,/无法连接服务/);
 assert.equal(f.state.tasks[0].id,staged.id);assert.equal(ui.getReviewItems().length,0);assert.equal(ui.isReviewBusy('candidate-A'),true);
 assert.equal(store.session.sync,'error');
 await store.retrySave();
 const calls=f.calls.filter(call=>call.path.endsWith('/resolve'));
 assert.equal(calls.length,2);assert.equal(calls[0].body.operation.id,calls[1].body.operation.id);
 assert.equal(calls[1].body.operation.intent.newTaskId,staged.id);
 assert.equal(f.state.tasks.length,1);assert.equal(store.session.sync,'saved');assert.equal(ui.isReviewBusy('candidate-A'),false);
});

test('a local staging failure leaves the candidate available and sends no resolve request',async t=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();
 const available=globalThis.indexedDB;globalThis.indexedDB=undefined;
 try {
  const saving=ui.confirmTask('candidate-A',{title:'不可丢失的操作',reviewVersion:3});
  await assert.rejects(saving.staged,/无法保存本地/);await assert.rejects(saving,/无法保存本地/);
 } finally {globalThis.indexedDB=available;}
 assert.equal(f.calls.some(call=>call.path.endsWith('/resolve')),false);
 assert.equal(f.state.tasks.length,0);assert.equal(ui.getReviewItems().length,1);assert.equal(ui.isReviewBusy('candidate-A'),false);
});


test('editing a target before review submission retains the draft and offers rereading',async t=>{
 const f=fixture();f.seedTask({id:'target',title:'原目标',description:'原说明',course:'合成课程',due:null,status:'not_started',plans:[],subtasks:[]});
 Object.assign(f.batches.A[0].items[0].candidate,{notice_type:'update',target_reference:'target'});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
 f.state.tasks.find(task=>task.id==='target').notes='另一个编辑刚修改的说明';
 const form=fakeForm('review',{action:'update',target_id:'target',title:'拟更新名称',description:'拟更新说明',course:'合成课程',date:'',time:''},{candidate:'candidate-A'});
 await ui.submit(form);
 assert.equal(f.calls.some(call=>call.path.endsWith('/resolve')),false);
 assert.equal(form.error.hidden,false);assert.equal(form.retry.hidden,false);assert.match(f.dialog,/核对任务/);
});

test('manual update postpone and cancel all use the durable review queue',async t=>{
 for(const action of ['update','postpone','cancel']){
  const f=fixture();f.seedTask({id:'target',title:'原目标',description:'原说明',course:'合成课程',due:{date:'2026-10-24',precision:'date'},status:'not_started',plans:[],subtasks:[]});
  Object.assign(f.batches.A[0].items[0].candidate,{notice_type:action,target_reference:'target'});
  await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
  await ui.submit(fakeForm('review',{action,target_id:'target',title:'已核对名称',description:'已核对说明',course:'合成课程',date:'2026-10-26',time:''},{candidate:'candidate-A'}));
  const call=f.calls.find(call=>call.path.endsWith('/resolve'));
  assert.equal(call.body.operation.intent.kind,'review');assert.equal(call.body.operation.intent.action,action);
  assert.equal(call.body.operation.intent.targetId,'target');assert.equal(call.body.operation.intent.expectedTarget.title,'原目标');
  if(action==='cancel')assert.equal(f.state.tasks.length,0);
  else {
   assert.equal(f.state.tasks[0].due,'2026-10-26');
   assert.equal(f.state.tasks[0].title,action==='update'?'已核对名称':'原目标');
  }
  assert.equal(ui.getReviewItems().length,0);assert.equal(f.calls.filter(call=>call.path==='/api/workspace').length,1);
 }
});


test('mail history retries use durable jobs with stable operation IDs and never call generic recognition',async t=>{
 for(const kind of ['import','reprocess']){
  let attempts=0;
  const endpoint=kind==='import'?'/api/sources/email/jobs':'/api/sources/email/reprocess';
  const id='email:history:'+kind;
  const f=fixture({emailCredentials:{configured:true,address:'student@stu.pku.edu.cn',username:'student'},emailPreferences:{consent_granted:true,process_all:false},intercept:async(path,body,method)=>{if(path===endpoint&&method==='POST'&&++attempts===1)throw new Error('synthetic retryable connection failure');}});
  const batch=f.batch();batch.request.request_id='email:'+kind+':history';batch.request.records[0].platform='email';batch.request.records[0].input_id=id;
  batch.result={status:'failed',record_results:[{input_id:id,status:'failed'}],errors:[{code:'agent_timeout'}]};batch.items=[];
  f.batches.A.splice(0,f.batches.A.length,batch);
  await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.click({dataset:{importSource:'email'}});
  await ui.click({dataset:{importHistoryRetry:batch.request.request_id}});
  await ui.click({dataset:{importHistoryRetry:batch.request.request_id}});
  const posts=f.calls.filter(call=>call.path===endpoint&&call.method==='POST');
  assert.equal(posts.length,2);assert.deepEqual(posts[1].body.input_ids,[id]);assert.equal(posts[0].body.operation_id,posts[1].body.operation_id);
  assert.equal(f.calls.some(call=>call.path==='/api/recognition/retry'),false);
  await ui.click({dataset:{importHistoryRetry:batch.request.request_id}});
  assert.equal(f.calls.filter(call=>call.path===endpoint&&call.method==='POST').length,2,'active job blocks duplicate history retries');
  ui.reset();
 }
});

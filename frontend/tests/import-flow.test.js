import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setMaxListeners} from 'node:events';

globalThis.window=new EventTarget();
setMaxListeners(0,globalThis.window);
globalThis.CustomEvent ||= class extends Event {constructor(type,options){super(type);this.detail=options?.detail;}};
globalThis.FormData=class {constructor(form){this.values=form.values;}*[Symbol.iterator](){yield* Object.entries(this.values);}};

const store=await import('../src/team-store.js');
const {createImportUI,buildTextImport,buildCandidateResolution,candidateDue,shanghaiNow,buildRecognitionRequest,describeImportError}=await import('../src/import-ui.js');
const {createAccountUI}=await import('../src/account-ui.js');
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

test('review retains date precision and requires explicit confirmation for changes',()=>{
 const values={action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:'',reviewed:'on'};
 const result=buildCandidateResolution(item,values,[],7);
 assert.deepEqual(result.draft.due,{date:'2026-10-20',precision:'date'});
 assert.equal(result.version,3);assert.equal(result.tasks_version,7);
 assert.throws(()=>buildCandidateResolution(item,{...values,reviewed:''},[],7),/核对/);
 assert.throws(()=>buildCandidateResolution(item,{...values,date:'',time:'18:00'},[],7),/截止日期/);
 const task={id:'target',title:'原任务',notes:'已有说明',course:'原课程'};
 const cancelled={...item,candidate:{...candidate,notice_type:'cancel'}};
 assert.throws(()=>buildCandidateResolution(cancelled,{action:'cancel',target_id:'missing',reviewed:'on'},[task],7),/选择/);
 const cancel=buildCandidateResolution(cancelled,{action:'cancel',target_id:'target',reviewed:'on'},[task],7);
 assert.equal(cancel.target_id,'target');assert.equal(cancel.draft,undefined);
 const postpone=buildCandidateResolution({...item,candidate:{...candidate,notice_type:'postpone'}},{...values,action:'postpone',target_id:'target'},[task],7);
 assert.equal(postpone.draft.title,'原任务');assert.equal(postpone.draft.description,'已有说明');
 assert.deepEqual(candidateDue({...deadline,precision:'datetime',value:'2026-10-19T18:30:00Z'}),{date:'2026-10-20',time:'02:30'});
 assert.deepEqual(candidateDue({...deadline,precision:'datetime',value:'2026-10-20T12:30:45+08:00'}),{date:'2026-10-20',time:''});
});

test('candidate confirmation retains notice classification and never changes the selected target type',()=>{
 const values={action:'create',title:'地点调整',description:'上课地点更改',course:'',date:'',time:'',reviewed:'on'};
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
function fixture(scenario={}){
 let account='A',state,dialog='',calls=[],messages=[],batches={A:[],B:[]},taskVersion=7,deferred=null,workspaces={A:[],B:[]},teachingFailed=false,runs={A:[],B:[]};
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
  if(path==='/api/auth/login'||path==='/api/auth/register'){account=body.email.split('@')[0];return respond({});}
  if(path==='/api/auth/me')return respond({user:{id:account,name:account,email:account+'@test.local'}});
  if(path==='/api/auth/logout')return respond({});
  if(path==='/api/workspace')return respond({tasks:{version:taskVersion,value:workspaces[account]}});
  if(path==='/api/sources/credentials'){if(method==='POST'&&scenario.credentialError)return reject(scenario.credentialError);return respond({configured:true,username:account+'-campus'});}
  if(path==='/api/recognition/batches'&&method==='GET'){
   if(deferred){const value=structuredClone(batches[account]);const pending=deferred;deferred=null;pending.ready();await pending.wait;return respond(value);}
   return respond(batches[account]);
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
  if(path.includes('/resolve')){
   const found=batches[account].flatMap(b=>b.items).find(item=>decodeURIComponent(path).includes(item.id));
   if(found){
    if(found.state==='pending'&&body.action==='create'){
     const due=candidateDue(found.candidate.deadline);
     workspaces[account].push({id:'task-'+found.id,title:found.candidate.title,description:found.candidate.description,course:found.candidate.course_or_project,source:'教学网',due:due.date?{date:due.date,precision:due.time?'datetime':'date',...(due.time?{time:due.time}:{})}:null,status:'not_started',plans:[],subtasks:[]});taskVersion++;
    }
    found.state=body.action==='ignore'?'ignored':body.action==='reopen'?'pending':'applied';found.version++;
   }
   return respond({batches:batches[account]});
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
 const host={escapeHtml,icon:()=>'',toast:message=>messages.push(message),render(){},openDialog(html){dialog=html;},closeDialog(){dialog='';},getState:()=>state,setState:value=>state=value,refresh:async()=>state=await store.refreshStore(),isDemo:()=>false};
 return {host,batch,batches,calls,messages,get dialog(){return dialog;},get state(){return state;},async login(who='A'){state=await store.authenticate('login',{email:who+'@test.local',password:'test-password'});},setVersion(value){taskVersion=value;},defer(){let resolve,ready;const wait=new Promise(r=>resolve=r),entered=new Promise(r=>ready=r);deferred={wait,ready};return {entered,release:resolve};}};
}

test('adding a reviewed candidate sends both versions without scheduling or fabricated time',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importReview:'candidate-A'}});
 const form=fakeForm('review',{action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:'',reviewed:'on'},{candidate:'candidate-A'});
 await ui.submit(form);
 const call=f.calls.find(call=>call.path.endsWith('/resolve'));
 assert.deepEqual(call.body.draft.due,{date:'2026-10-20',precision:'date'});
 assert.equal(call.body.version,3);assert.equal(call.body.tasks_version,7);
 assert.equal(f.calls.some(call=>call.path.includes('/planning/')),false);
 assert.equal(f.dialog,'');assert.ok(f.messages.includes('已加入任务'));
});

test('a task version change prevents a stale confirmation and offers rereading',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importReview:'candidate-A'}});
 f.setVersion(8);await f.host.refresh();
 const form=fakeForm('review',{action:'create',title:'报告',date:'2026-10-20',time:'',reviewed:'on'},{candidate:'candidate-A'});
 await ui.submit(form);
 assert.equal(f.calls.some(call=>call.path.endsWith('/resolve')),false);
 assert.match(form.error.textContent,/刷新/);assert.equal(form.error.hidden,false);assert.equal(form.retry.hidden,false);
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
 const draft=fakeForm('review',{action:'create',title:'保留这份新草稿',description:'仍在编辑',course:'概率论',date:'2026-10-20',time:'',reviewed:''});
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
 const form=fakeForm('review',{action:'create',title:'提交报告',description:'原文要求',course:'概率论',date:'2026-10-20',time:'',reviewed:'on'},{candidate:'candidate-A'});form.isConnected=true;
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
 assert.deepEqual(calls.map(call=>call.body),[{action:'ignore',version:3},{action:'reopen',version:4}]);
 assert.match(ui.render(),/data-import-review="candidate-A"/);
});

test('teaching collection starts a background run and reads its server-created tasks without planning',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});ui.input({target:{id:'teaching-sync-consent',checked:true}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
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
 const values={action:'create',title:'核对后的任务',description:'',course:'',reviewed:'on'};
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
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});ui.input({target:{id:'teaching-sync-consent',checked:true}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
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
 assert.match(html,/<details class="import-history"><summary>历史导入 · 识别未完成 1<\/summary>/);
 assert.ok(html.includes(`data-import-retry="${source.run.request_id}"`));
 assert.match(html,/识别服务连接失败或超时/);
 assert.doesNotMatch(html.split('class="import-results"')[1],/import-failure|data-import-retry/);
 await new Promise(r=>setImmediate(r));
 await ui.click({dataset:{importRetry:source.run.request_id}});
 const calls=f.calls.filter(call=>['/api/recognition/batches','/api/recognition/retry'].includes(call.path)&&call.method==='POST');
 assert.deepEqual(calls.map(call=>({path:call.path,body:call.body})),[{path:'/api/recognition/retry',body:source.run}]);
 const automatic=f.calls.filter(call=>call.path.endsWith('/resolve'));
 assert.equal(automatic.length,1);assert.deepEqual(automatic[0].body,{action:'create',auto_apply:true,version:1});
 assert.equal(f.host.getState().tasks.length,1);assert.equal(f.calls.some(call=>call.path.includes('/planning/')),false);
 await ui.click({dataset:{importRetry:source.run.request_id}});
 assert.equal(f.calls.filter(call=>call.path.endsWith('/resolve')).length,1);assert.equal(f.host.getState().tasks.length,1);
});

test('configuration, credentials, and connection failures give different next steps',()=>{
 const cases=[
  ['教学网采集依赖未安装；请先在 crawler/ 执行 pnpm install','crawler_dependency',/缺少教学网采集组件/],
  ['凭据加密依赖未安装；请安装 server/requirements.txt','credential_dependency',/缺少连接保存组件/],
  ['无法解密教学网凭据；请重新保存账号和密码','credential_storage',/重新保存连接/],
  ['文本识别配置不可用，请检查当前环境的模型配置文件或环境变量中的模型配置与密钥','model_configuration',/完成配置/],
  ['教学网识别配置不可用，请检查当前环境的七牛云模型配置并重启后端','teaching_model_configuration',/完成配置/],
  ['教学网采集失败；请检查账号、密码、浏览器及校园网连接','teaching_connection',/核对账号密码及校园网/],
  ['无法连接服务，请检查后端','service_connection',/服务正在运行/],
  ['模型请求排队已满或等待超时，请稍后重试；未开始模型调用','model_busy',/稍后重试/],
 ];
 for(const [message,kind,expected] of cases){const report=describeImportError({message,status:503});assert.equal(report.kind,kind);assert.match(report.message,expected);}
 assert.match(describeImportError({status:401,message:'请先登录'}).message,/重新登录/);
 const raw='调用失败 API_KEY=private-model-key Authorization: Bearer private-token password=campus-password';
 assert.doesNotMatch(JSON.stringify(describeImportError({status:502,message:raw})),/private-model-key|private-token|campus-password/);
});

test('a missing crawler component is visible with technical steps collapsed',async()=>{
 const f=fixture({syncError:{status:503,message:'教学网采集依赖未安装；请先在 crawler/ 执行 pnpm install'}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});ui.input({target:{id:'teaching-sync-consent',checked:true}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
 const html=ui.render(),main=html.match(/<p class="error" role="alert">([^<]+)<\/p>/)?.[1];
 assert.match(main,/缺少教学网采集组件.*安装后重新采集/);
 assert.doesNotMatch(main,/crawler|pnpm|Node/);
 assert.match(html,/<details class="import-error-details"[^>]*><summary>查看原因<\/summary>/);
 assert.doesNotMatch(html,/<details class="import-error-details"[^>]*\bopen\b/);
 assert.match(html,/pnpm install/);
 assert.doesNotMatch(html,/采集服务尚未就绪/);
});

test('model configuration failures are actionable and never echo model credentials',async()=>{
 const f=fixture({recognitionError:{status:503,message:'文本识别配置不可用，请检查模型密钥 API_KEY=private-test-key'}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.submit(fakeForm('text',{text:'提交作业',published:'2026-10-14T12:00:00'}));
 const html=ui.render();assert.match(html,/任务识别的模型配置不可用.*完成配置后重试/);
 assert.match(html,/查看原因/);assert.doesNotMatch(html,/private-test-key/);
});

test('credential storage errors remain visible in the connection dialog',async()=>{
 const f=fixture({credentialError:{status:503,message:'凭据加密依赖未安装；请安装 server/requirements.txt'}});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importAction:'credentials'}});
 const form=fakeForm('credentials',{username:'campus-user',password:'campus-password'});
 await ui.submit(form);assert.match(form.error.textContent,/缺少连接保存组件/);assert.equal(form.error.hidden,false);
 assert.doesNotMatch(form.error.textContent,/campus-user|campus-password/);
});

test('terminal reprocessing refresh keeps text and teaching imports blocked until results are current',{timeout:3000},async(t)=>{
 const root='/api/recognition/reprocessing/',version='reprocessing/0.1';
 const saved={input_id:'saved-notice',label:'合成保存通知',course_or_project:'测试课程',result_status:'success',policy_version:null};
 let state='queued',created=false,operationId,blockRefresh=false,entered,release;
 const ready=new Promise(resolve=>entered=resolve),waiting=new Promise(resolve=>release=resolve);
 const run=()=>({
  contract_version:version,run_id:'saved-run',operation_id:operationId,source_account_id:'saved-source',
  state,revision:state==='queued'?1:2,created_at:'2026-10-14T12:00:00Z',updated_at:'2026-10-14T12:00:01Z',
  counts:{total:1,queued:state==='queued'?1:0,running:0,succeeded:state==='succeeded'?1:0,failed:0,cancelled:0,interrupted:0,superseded:0},
  records:[{input_id:saved.input_id,state,previous_available:true,old_result_retained:true,decision_summary:{preserved:0,replaced:0,new_pending:0,unresolved:0},policy_version:null,error:null}],
 });
 const f=fixture({async intercept(path,body,method){
  if(path===root+'sources')return {contract_version:version,accounts:[{source_account_id:'saved-source',label:'已保存来源',records:[saved]}],next_cursor:null};
  if(path===root+'runs'&&method==='GET')return {contract_version:version,runs:created?[run()]:[],next_cursor:null};
  if(path===root+'runs'&&method==='POST'){created=true;operationId=body.operation_id;return run();}
  if(path==='/api/workspace'&&blockRefresh){blockRefresh=false;entered();await waiting;}
 }});
 await f.login();
 const ui=createImportUI(f.host);t.after(()=>{release();ui.reset();});await ui.load();
 await ui.toggle({target:{open:true,isConnected:true,matches:selector=>selector==='[data-reprocess-panel]'}});
 ui.input({target:{dataset:{reprocessRecord:saved.input_id},checked:true}});
 await ui.click({dataset:{reprocessAction:'start'}});
 assert.match(ui.render(),/排队中/);
 state='succeeded';blockRefresh=true;
 const refreshing=ui.click({dataset:{reprocessAction:'load'}});
 await ready;
 const before=f.calls.length;
 await ui.submit(fakeForm('text',{text:'刷新期间的合成通知',published:'2026-10-14T12:00:00'}));
 ui.input({target:{id:'teaching-sync-consent',checked:true}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
 assert.equal(f.calls.length,before,'the completion refresh must retain the shared import lock');
 release();await refreshing;
 assert.match(ui.render(),/识别结果已刷新/);
 await ui.submit(fakeForm('text',{text:'刷新完成后的合成通知',published:'2026-10-14T12:00:00'}));
 await ui.click({dataset:{importSource:'teaching'}});ui.input({target:{id:'teaching-sync-consent',checked:true}});await new Promise(r=>setImmediate(r));await ui.click({dataset:{importAction:'sync'}});
 const writes=f.calls.slice(before).filter(call=>call.method==='POST');
 assert.equal(writes.filter(call=>call.path==='/api/recognition/batches'&&call.body.contract_version==='0.2').length,1);
 assert.equal(writes.filter(call=>call.path==='/api/sources/sync-runs').length,1);
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
 }});
 await f.login();const ui=createImportUI(f.host);t.after(()=>ui.reset());await ui.load();await ui.loadSourceSync();
 await ui.click({dataset:{importSource:'teaching'}});await new Promise(r=>setImmediate(r));
 let html=ui.render();
 assert.match(html,/data-import-action="sync" disabled/);
 assert.match(html,/data-import-action="credentials" disabled/);
 assert.doesNotMatch(html,/data-import-(?:review|ignore)="candidate-A" disabled/);
 for(const name of ['text','files','public','teaching']) {
  await ui.click({dataset:{importSource:name}});
  if(name==='text')assert.match(ui.render(),/data-import-form="text"/);
  if(name==='files')assert.match(ui.render(),/id="material-files"/);
  if(name==='public')assert.match(ui.render(),/data-public-action="start"/);
 }
 await ui.click({dataset:{importReview:'candidate-A'}});assert.match(f.dialog,/核对任务/);
 const form=fakeForm('review',{action:'create',title:'提交报告',description:'明确要求',course:'概率论',date:'2026-10-20',time:'',reviewed:'on'},{candidate:'candidate-A'});
 await ui.submit(form);assert.equal(f.state.tasks.length,1);assert.equal(f.dialog,'');
 await ui.submit(fakeForm('text',{text:'独立文本识别',published:'2026-10-14T12:00:00'}));
 ui.input({target:{id:'teaching-sync-consent',checked:true}});await ui.click({dataset:{importAction:'sync'}});
 assert.equal(f.calls.filter(c=>c.path==='/api/recognition/batches'&&c.method==='POST').length,1);
 assert.equal(f.calls.filter(c=>c.path==='/api/sources/sync-runs'&&c.method==='POST').length,0);
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

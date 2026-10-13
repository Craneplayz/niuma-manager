import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.CustomEvent ||= class extends Event {constructor(type,options){super(type);this.detail=options?.detail;}};
globalThis.FormData=class {constructor(form){this.values=form.values;}*[Symbol.iterator](){yield* Object.entries(this.values);}};

const store=await import('../src/team-store.js');
const {createImportUI,buildTextImport,buildCandidateResolution,candidateDue,shanghaiNow}=await import('../src/import-ui.js');
const {createAccountUI}=await import('../src/account-ui.js');
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const deadline={kind:'deadline',status:'explicit',precision:'date',value:'2026-10-20'};
const candidate={title:'提交报告',description:'原文要求',course_or_project:'概率论',notice_type:'create',deadline,evidence:[{quote:'请在10月20日前提交报告。'}],sources:[],review_reasons:[]};
const item={id:'candidate',state:'pending',version:3,candidate};

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

function fakeForm(kind,values,extra={}){
 const error={textContent:'',hidden:true},retry={hidden:true},submit={textContent:'确认',disabled:false,dataset:{}};
 const fields=[submit,{disabled:false,dataset:{}}];
 return {dataset:{importForm:kind,...extra},values,error,retry,submit,querySelector(selector){if(selector.includes('refresh-review'))return retry;if(selector.includes('error'))return error;if(selector==='[type="submit"]')return submit;return null;},querySelectorAll(){return fields;}};
}
function fixture(){
 let account='A',state,dialog='',calls=[],messages=[],batches={A:[],B:[]},taskVersion=7,deferred=null,workspaces={A:[],B:[]};
 const respond=value=>({ok:true,json:async()=>structuredClone(value)});
 const batch=(who='A',id='request-'+who)=>({request:buildTextImport(who+' 私有通知','2026-10-14T12:00:00',id),result:{status:'success'},items:[{...structuredClone(item),id:'candidate-'+who,candidate:{...structuredClone(candidate),title:who+' 私有任务'}}]});
 batches.A=[batch('A')];batches.B=[batch('B')];
 globalThis.fetch=async(path,options={})=>{
  const method=options.method||'GET',body=options.body?JSON.parse(options.body):undefined;
  calls.push({path,method,body,account,signal:options.signal});
  if(path==='/api/auth/login'||path==='/api/auth/register'){account=body.email.split('@')[0];return respond({});}
  if(path==='/api/auth/me')return respond({user:{id:account,name:account,email:account+'@test.local'}});
  if(path==='/api/auth/logout')return respond({});
  if(path==='/api/workspace')return respond({tasks:{version:taskVersion,value:workspaces[account]}});
  if(path==='/api/sources/credentials')return respond({configured:true,username:account+'-campus'});
  if(path==='/api/recognition/batches'&&method==='GET'){
   if(deferred){const value=structuredClone(batches[account]);const pending=deferred;deferred=null;pending.ready();await pending.wait;return respond(value);}
   return respond(batches[account]);
  }
  if(path==='/api/recognition/batches'&&method==='POST'){
   const next={request:body,result:{status:'success'},items:[]};batches[account].push(next);return respond(batches[account]);
  }
  if(path.includes('/resolve')){
   const found=batches[account].flatMap(b=>b.items).find(item=>path.includes(item.id));
   if(found){found.state=body.action==='ignore'?'ignored':body.action==='reopen'?'pending':'applied';found.version++;}
   return respond({batches:batches[account]});
  }
  if(path==='/api/sources/sync')return respond({requests:[buildTextImport('采集一','2026-10-14T12:00:00','teaching-one'),buildTextImport('采集二','2026-10-14T12:00:00','teaching-two')],summary:{skipped:0}});
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

test('ignored candidates can be restored using the newest candidate version',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importIgnore:'candidate-A'}});
 assert.match(ui.render(),/已忽略/);
 await ui.click({dataset:{importReopen:'candidate-A'}});
 const calls=f.calls.filter(call=>call.path.endsWith('/resolve'));
 assert.deepEqual(calls.map(call=>call.body),[{action:'ignore',version:3},{action:'reopen',version:4}]);
 assert.match(ui.render(),/data-import-review="candidate-A"/);
});

test('teaching collection recognizes each request without automatic task application',async()=>{
 const f=fixture();await f.login();const ui=createImportUI(f.host);await ui.load();
 await ui.click({dataset:{importSource:'teaching'}});await ui.click({dataset:{importAction:'sync'}});
 const sync=f.calls.find(call=>call.path==='/api/sources/sync');assert.deepEqual(sync.body,{sources:['teaching_web']});
 assert.deepEqual(f.calls.filter(call=>call.path==='/api/recognition/batches'&&call.method==='POST').map(call=>call.body.request_id),['teaching-one','teaching-two']);
 assert.equal(f.calls.some(call=>call.path.endsWith('/resolve')||call.path.includes('/planning/')),false);
 assert.match(ui.render(),/采集完成/);
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
 assert.equal(store.session.user.display_name,'新昵称');assert.ok(f.messages.includes('昵称已更新'));
});

test('password mismatch is visible and does not send a request',async()=>{
 const f=fixture();await f.login();const ui=createAccountUI(f.host),form=fakeForm('',{currentPassword:'old',newPassword:'new-password',confirmPassword:'different'});
 form.dataset={accountForm:'password'};const before=f.calls.length;await ui.submit(form);
 assert.equal(f.calls.length,before);assert.match(form.error.textContent,/不一致/);assert.equal(form.error.hidden,false);
});

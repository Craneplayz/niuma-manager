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

function fakeForm(kind,values,extra={}){
 const error={textContent:'',hidden:true},retry={hidden:true},submit={textContent:'确认',disabled:false,dataset:{}};
 const fields=[submit,{disabled:false,dataset:{}}];
 return {dataset:{importForm:kind,...extra},values,error,retry,submit,querySelector(selector){if(selector.includes('refresh-review'))return retry;if(selector==='[data-import-error-details]')return null;if(selector.includes('error'))return error;if(selector==='[type="submit"]')return submit;return null;},querySelectorAll(){return fields;}};
}
function fixture(scenario={}){
 let account='A',state,dialog='',calls=[],messages=[],batches={A:[],B:[]},taskVersion=7,deferred=null,workspaces={A:[],B:[]},teachingFailed=false;
 const respond=value=>({ok:true,json:async()=>structuredClone(value)});
 const reject=error=>({ok:false,status:error.status||503,json:async()=>({error:error.message})});
 const syncCases=scenario.syncCases||[teachingCase('date-only'),teachingCase('no-deadline')];
 const batch=(who='A',id='request-'+who)=>({request:buildTextImport(who+' 私有通知','2026-10-14T12:00:00',id),result:{status:'success'},items:[{...structuredClone(item),id:'candidate-'+who,candidate:{...structuredClone(candidate),title:who+' 私有任务'}}]});
 batches.A=[batch('A')];batches.B=[batch('B')];
 globalThis.fetch=async(path,options={})=>{
  const method=options.method||'GET',body=options.body?JSON.parse(options.body):undefined;
  calls.push({path,method,body,account,signal:options.signal});
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
    const failed=scenario.teachingFailOnce&&!teachingFailed;
    if(failed)teachingFailed=true;
    const result=failed?{...fixture.result,status:'failed',tasks:[],errors:[{code:'agent_timeout',message:'agent_timeout',retryable:true}]}:fixture.result;
    next={request:fixture.request,result,items:result.tasks.map((candidate,index)=>({id:body.request_id+':storage:'+index,state:'pending',version:1,candidate}))};
   }else next={request:body,result:{status:'success'},items:[]};
   batches[account]=batches[account].filter(batch=>batch.request.request_id!==next.request.request_id);batches[account].push(next);return respond(batches[account]);
  }
  if(path.includes('/resolve')){
   const found=batches[account].flatMap(b=>b.items).find(item=>path.includes(item.id));
   if(found){found.state=body.action==='ignore'?'ignored':body.action==='reopen'?'pending':'applied';found.version++;}
   return respond({batches:batches[account]});
  }
  if(path==='/api/sources/sync')return scenario.syncError?reject(scenario.syncError):respond({requests:syncCases.map(item=>item.request),summary:{skipped:0}});
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
 assert.deepEqual(f.calls.filter(call=>call.path==='/api/recognition/batches'&&call.method==='POST').map(call=>call.body),[teachingCase('date-only').run,teachingCase('no-deadline').run]);
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
 assert.equal(store.session.user.name,'新昵称');assert.ok(f.messages.includes('昵称已更新'));
});

test('password mismatch is visible and does not send a request',async()=>{
 const f=fixture();await f.login();const ui=createAccountUI(f.host),form=fakeForm('',{currentPassword:'old',newPassword:'new-password',confirmPassword:'different'});
 form.dataset={accountForm:'password'};const before=f.calls.length;await ui.submit(form);
 assert.equal(f.calls.length,before);assert.match(form.error.textContent,/不一致/);assert.equal(form.error.hidden,false);
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

test('a real teaching request with two tasks exposes independent review controls',async()=>{
 const source=teachingCase('two-tasks'),f=fixture({syncCases:[source]});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});await ui.click({dataset:{importAction:'sync'}});
 const html=ui.render();
 for(const candidate of source.result.tasks)assert.ok(html.includes(escapeHtml(candidate.title)));
 assert.ok(html.includes(`data-import-review="${source.run.request_id}:storage:0"`));
 assert.ok(html.includes(`data-import-review="${source.run.request_id}:storage:1"`));
 assert.equal(f.calls.some(call=>call.path.endsWith('/resolve')),false);
});

test('retrying a real teaching failure also sends only the server run identity',async()=>{
 const source=teachingCase('date-only'),f=fixture({syncCases:[source],teachingFailOnce:true});await f.login();
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});await ui.click({dataset:{importAction:'sync'}});
 assert.ok(ui.render().includes(`data-import-retry="${source.run.request_id}"`));
 assert.match(ui.render(),/识别服务连接失败或超时/);
 await ui.click({dataset:{importRetry:source.run.request_id}});
 const calls=f.calls.filter(call=>['/api/recognition/batches','/api/recognition/retry'].includes(call.path)&&call.method==='POST');
 assert.deepEqual(calls.map(call=>({path:call.path,body:call.body})),[{path:'/api/recognition/batches',body:source.run},{path:'/api/recognition/retry',body:source.run}]);
 assert.equal(f.calls.some(call=>call.path.endsWith('/resolve')),false);
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
 const ui=createImportUI(f.host);await ui.load();await ui.click({dataset:{importSource:'teaching'}});await ui.click({dataset:{importAction:'sync'}});
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

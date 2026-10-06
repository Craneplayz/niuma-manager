import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.window=new EventTarget();
globalThis.location={hash:'#calendar'};
globalThis.requestAnimationFrame=()=>{};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');
const {calendarPage}=await import('../src/team-calendar-ui.js');
const {dayKey}=await import('../src/data.js');

test('F3 auto-apply accepts clear external actions and rejects uncertain or manual input',async()=>{
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batch={request:fixture.request,demo:false};
 const byInput=id=>fixture.expected.tasks.find(task=>task.input_id==='synthetic:f3:'+id);
 assert.equal(recognition.canAutoApplyCandidate(byInput('dated'),batch),true);
 assert.equal(recognition.canAutoApplyCandidate(byInput('undated'),batch),true);
 assert.equal(recognition.canAutoApplyCandidate(byInput('yearless-current'),batch),true);
 for(const id of ['unknown-age','recent-overdue','postpone','cancel','optional'])assert.equal(recognition.canAutoApplyCandidate(byInput(id),batch),false);
 assert.equal(recognition.canAutoApplyCandidate({...byInput('dated'),deadline:{...byInput('dated').deadline,status:'inferred'}},batch),false);
 assert.equal(recognition.canAutoApplyCandidate({...byInput('dated'),deadline:{...byInput('dated').deadline,value:'2026-10-01T18:00:30+08:00'}},batch),false);
 assert.equal(recognition.canAutoApplyCandidate({...byInput('dated'),review_reasons:['需要核对']},batch),false);
 assert.equal(recognition.canAutoApplyCandidate(byInput('dated'),{...batch,demo:true}),false);
 assert.equal(recognition.canAutoApplyCandidate(byInput('dated'),{request:{records:[{input_id:byInput('dated').input_id,platform:'unknown'}]}}),false);
 for(const platform of ['manual','email','web'])assert.equal(recognition.canAutoApplyCandidate(byInput('dated'),{request:{records:[{input_id:byInput('dated').input_id,platform}]}}),false);
});

test('manual text stays pending even when it has an explicit deadline',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const candidate={...fixture.expected.tasks[0],input_id:'manual:one'};
 let resolved=0;
 recognition.configureAssistant({render(){}});
 globalThis.fetch=async(path,options)=>{
  if(path==='/api/recognition/batches'){
   const body=JSON.parse(options.body);assert.equal(body.records[0].platform,'manual');
   return {ok:true,json:async()=>[{request:{request_id:body.request_id,records:body.records},result:{status:'success',record_results:[],errors:[]},demo:false,items:[{id:'manual-candidate',version:1,state:'pending',candidate}]}]};
  }
  if(path.includes('/resolve'))resolved++;
  throw new Error('unexpected request '+path);
 };
 await recognition.submitRecognitionText('10月1日提交报告');
 assert.equal(resolved,0);
 assert.match(recognition.assistantPage(),/待核对 <span>1/);
 assert.match(recognition.assistantPage(),/data-agent-confirm="manual-candidate"/);
});

test('review sections appear only after import and dismissed items can be restored',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batches=[],resolutions=[];
 recognition.configureAssistant({render(){}});
 const initial=recognition.assistantPage();
 assert.doesNotMatch(initial,/import-review-section|import-ignored-section/);
 assert.match(initial,/data-agent-source-mode="manual"[^>]*aria-selected="true"|aria-selected="true"[^>]*data-agent-source-mode="manual"/);
 assert.doesNotMatch(initial,/data-agent-source-mode="teaching_web"/);
 assert.ok(initial.indexOf('data-agent-source-mode="manual"')<initial.indexOf('data-agent-source-mode="wechat"'));
 globalThis.fetch=async(path,options)=>{
  if(path==='/api/recognition/batches'){
   const body=JSON.parse(options.body),index=batches.length+1,item={id:'candidate-'+index,version:1,state:'pending',candidate:{...fixture.expected.tasks[0],input_id:body.records[0].input_id,title:'本次任务 '+index}};
   batches.push({request:{request_id:body.request_id,records:body.records},result:{status:'success',record_results:[],errors:[]},demo:false,items:[item]});
   return {ok:true,json:async()=>batches};
  }
  if(path.includes('/resolve')){
   const item=batches.flatMap(batch=>batch.items).find(candidate=>path.includes(candidate.id)),body=JSON.parse(options.body);resolutions.push(body);
   item.state=body.action==='reopen'?'pending':'ignored';item.version++;
   return {ok:true,json:async()=>({batches})};
  }
  throw new Error('unexpected request '+path);
 };
 await recognition.submitRecognitionText('第一条通知');
 assert.match(recognition.assistantPage(),/本次任务 1/);
 await recognition.submitRecognitionText('第二条通知');
 const current=recognition.assistantPage();
 assert.doesNotMatch(current,/本次任务 1/);
 assert.match(current,/本次任务 2/);
 await recognition.handleAssistantClick({dataset:{agentReviewScope:'all'}});
 assert.match(recognition.assistantPage(),/本次任务 1/);
 assert.match(recognition.assistantPage(),/本次任务 2/);
 await recognition.handleAssistantClick({dataset:{agentReviewScope:'current'}});
 await recognition.handleAssistantClick({dataset:{agentIgnore:'candidate-2'}});
 const ignored=recognition.assistantPage();
 assert.match(ignored,/<details class="import-ignored-section"><summary>不加入任务 <span>1<\/span>/);
 assert.match(ignored,/import-review-card ignored/);
 assert.match(ignored,/data-agent-restore="candidate-2"[^>]*>撤回<\/button>/);
 await recognition.handleAssistantClick({dataset:{agentRestore:'candidate-2'}});
 assert.deepEqual(resolutions.at(-1),{action:'reopen',version:2});
 const restored=recognition.assistantPage();
 assert.match(restored,/待核对 <span>1/);
 assert.match(restored,/data-agent-confirm="candidate-2"/);
 assert.match(restored,/不加入任务 <span>0/);
});

test('manual quick confirmation saves without triggering planning',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 let batch,planned=0,resolution;
 recognition.configureAssistant({render(){},refresh:async()=>{},toast(){},proposeForTasks:async()=>{planned++;}});
 globalThis.fetch=async(path,options)=>{
  if(path==='/api/recognition/batches'){
   const body=JSON.parse(options.body),item={id:'quick-candidate',version:2,state:'pending',candidate:{...fixture.expected.tasks[0],input_id:body.records[0].input_id,title:'快速创建任务'}};
   batch={request:{request_id:body.request_id,records:body.records},result:{status:'success',record_results:[],errors:[]},demo:false,items:[item]};
   return {ok:true,json:async()=>[batch]};
  }
  if(path.includes('/resolve')){resolution=JSON.parse(options.body);batch.items[0].state='applied';return {ok:true,json:async()=>({batches:[batch]})};}
  throw new Error('unexpected request '+path);
 };
 await recognition.submitRecognitionText('需要核对的通知');
 await recognition.handleAssistantClick({dataset:{agentConfirm:'quick-candidate'}});
 assert.equal(resolution.action,'create');
 assert.equal(resolution.reviewed,true);
 assert.equal(resolution.target_id,null);
 assert.equal(resolution.draft.title,'快速创建任务');
 assert.equal(planned,0);
 assert.match(recognition.assistantPage(),/待核对 <span>0/);
});

test('external F3 output auto-adds dated and undated actions but leaves review candidates pending',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batch={request:fixture.request,result:fixture.expected,demo:false,items:fixture.expected.tasks.map(task=>({id:task.candidate_id,version:1,state:'pending',candidate:task}))};
 const applied=[],planned=[];let refreshed=0,tasks=[];
 recognition.configureAssistant({render(){},refresh:async()=>{refreshed++;tasks=batch.items.filter(item=>item.state==='applied').map(item=>({id:'task-'+item.id,due:item.candidate.deadline.value,status:'todo',plans:[],recognitionHistory:[{storageId:item.id}]}));},getState:()=>({tasks}),proposeForTasks:async ids=>{planned.push(ids);}});
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'source.json',size:100,text:async()=>JSON.stringify(fixture.request)}]}});
 await new Promise(resolve=>setTimeout(resolve,0));
 recognition.handleAssistantInput({target:{id:'agent-import-consent',checked:true}});
 globalThis.fetch=async(path,options)=>{
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[]};
  if(path==='/api/recognition/retry')return {ok:true,json:async()=>[batch]};
  if(path.includes('/resolve')){
   const body=JSON.parse(options.body),item=batch.items.find(item=>path.includes(item.id));
   assert.deepEqual(body,{action:'create',auto_apply:true,version:1});
   applied.push(item.candidate.input_id);
   item.state='applied';
   return {ok:true,json:async()=>({batches:[batch]})};
  }
  throw new Error('unexpected request '+path);
 };
 await recognition.handleAssistantClick({dataset:{agentImportIndex:'0'}});
 assert.deepEqual(applied.sort(),['dated','mixed','old-but-future','undated','yearless-current'].map(id=>'synthetic:f3:'+id).sort());
 assert.equal(refreshed,1);
 assert.equal(planned.length,1);
 assert.deepEqual(planned[0].sort(),batch.items.filter(item=>item.state==='applied'&&item.candidate.deadline.value).map(item=>'task-'+item.id).sort());
 assert.match(recognition.assistantPage(),/待核对 <span>6/);
 assert.match(recognition.assistantPage(),/data-agent-confirm=/);
});

test('refresh does not auto-apply old unfiltered batches',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batch={request:fixture.request,result:fixture.expected,demo:false,items:[{id:'old-candidate',version:1,state:'pending',candidate:fixture.expected.tasks[0]}]};
 let resolved=0;
 recognition.configureAssistant({render(){}});
 globalThis.fetch=async(path)=>{
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[batch]};
  if(path.includes('/resolve'))resolved++;
  throw new Error('unexpected request '+path);
 };
 recognition.assistantPage();
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(resolved,0);
 assert.match(recognition.assistantPage(),/import-review-section/);
 assert.match(recognition.assistantPage(),/data-agent-confirm="old-candidate"/);
});

test('reviewed external creates with a DDL automatically propose; manual, undated and updates do not',async()=>{
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const originalFormData=globalThis.FormData;
 try{
  for(const [platform,due,action] of [['teaching_web','2026-10-11','create'],['wechat','2026-10-11','create'],['manual','2026-10-11','create'],['teaching_web','','create'],['teaching_web','2026-10-11','update']]){
   recognition.resetAssistant();session.user={id:'test'};
   const candidate={...fixture.expected.tasks[0],input_id:'reviewed',needs_review:true,review_reasons:['核对正文']};
   const item={id:'reviewed-candidate',version:1,state:'pending',candidate};
   const batch={request:{request_id:'reviewed-batch',records:[{input_id:'reviewed',platform}]},result:{status:'success',record_results:[],errors:[]},demo:false,items:[item]};
   const tasks=[{id:'saved-task',due,status:'todo',plans:[],recognitionHistory:[{storageId:item.id}]}];
   const calls=[];
   recognition.configureAssistant({render(){},getState:()=>({tasks}),closeDialog(){},toast(){},refresh:async()=>{calls.push('refresh');},proposeForTasks:async ids=>{calls.push(ids);}});
   globalThis.fetch=async path=>{
    if(path==='/api/recognition/batches')return {ok:true,json:async()=>[batch]};
    if(path.includes('/resolve')){calls.push('save');item.state='applied';return {ok:true,json:async()=>({batches:[batch]})};}
    throw new Error('unexpected request '+path);
   };
   recognition.assistantPage();await new Promise(resolve=>setTimeout(resolve,0));
   globalThis.FormData=class {*[Symbol.iterator](){yield ['action',action];yield ['reviewed','on'];yield ['title','Reviewed task'];yield ['date',due];if(action==='update')yield ['target_id','saved-task'];}};
   const error={textContent:''},submit={textContent:'保存'},retry={hidden:true};
   const form={id:'candidate-form',dataset:{candidate:item.id},querySelector:selector=>selector==='#candidate-error'?error:selector==='[type="submit"]'?submit:retry,querySelectorAll:()=>[]};
   recognition.handleAssistantSubmit(form);
   await new Promise(resolve=>setTimeout(resolve,0));
   assert.equal(error.textContent,'');
   assert.deepEqual(calls.slice(0,2),['save','refresh']);
   assert.deepEqual(calls.slice(2),platform!=='manual'&&due&&action==='create'?[['saved-task']]:[]);
  }
 }finally{globalThis.FormData=originalFormData;}
});

test('retry auto-applies only candidates created by that retry',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const old={id:'old-candidate',version:1,state:'pending',candidate:fixture.expected.tasks[0]};
 const fresh={id:'fresh-candidate',version:1,state:'pending',candidate:{...fixture.expected.tasks[0],candidate_id:'fresh',input_id:'synthetic:f3:undated',deadline:{...fixture.expected.tasks[0].deadline,status:'not_provided',kind:'unknown',precision:'unknown',value:null}}};
 const batch={request:fixture.request,result:{status:'partial_success',record_results:[],errors:[{input_id:fresh.candidate.input_id,code:'agent_unavailable',message:'暂时失败'}]},demo:false,items:[old]};
 recognition.configureAssistant({render(){},refresh:async()=>{}});
 globalThis.fetch=async path=>{
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[batch]};
  if(path==='/api/recognition/retry'){batch.items=[old,fresh];batch.result={status:'success',record_results:[],errors:[]};return {ok:true,json:async()=>[batch]};}
  if(path.includes('/resolve')){assert.match(path,/fresh-candidate/);fresh.state='applied';return {ok:true,json:async()=>({batches:[batch]})};}
  throw new Error('unexpected request '+path);
 };
 recognition.assistantPage();await new Promise(resolve=>setTimeout(resolve,0));
 await recognition.handleAssistantClick({dataset:{agentRetry:fixture.request.request_id}});
 assert.equal(old.state,'pending');
 assert.equal(fresh.state,'applied');
});

test('source import does not auto-apply a replayed batch before history is loaded',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const old={id:'old-candidate',version:1,state:'pending',candidate:fixture.expected.tasks[0]};
 const batch={request:fixture.request,result:{status:'success',record_results:[],errors:[]},demo:false,items:[old]};
 let resolved=0;
 recognition.configureAssistant({render(){}});
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'source.json',size:100,text:async()=>JSON.stringify(fixture.request)}]}});
 await new Promise(resolve=>setTimeout(resolve,0));
 recognition.handleAssistantInput({target:{id:'agent-import-consent',checked:true}});
 globalThis.fetch=async path=>{
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[batch]};
  if(path==='/api/recognition/retry')return {ok:true,json:async()=>[batch]};
  if(path.includes('/resolve'))resolved++;
  throw new Error('unexpected request '+path);
 };
 await recognition.handleAssistantClick({dataset:{agentImportIndex:'0'}});
 assert.equal(resolved,0);
 assert.equal(old.state,'pending');
});

test('source management shows import, consent, credential, and selection errors',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 recognition.configureAssistant({render(){}});
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'bad.json',size:8,text:async()=>'{broken'}]}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(recognition.sourceManagementPage(),/role="alert"/);
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'valid.json',size:200,text:async()=>JSON.stringify(fixture.request)}]}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(recognition.sourceManagementPage(),/role="status"/);
 await recognition.handleAssistantClick({dataset:{agentImportIndex:'0'}});
 assert.match(recognition.sourceManagementPage(),/请先确认所选来源/);
 globalThis.fetch=async()=>({ok:false,status:500,json:async()=>({error:'保存失败'})});
 const originalFormData=globalThis.FormData;
 globalThis.FormData=class {*[Symbol.iterator](){yield ['username','test'];yield ['password','secret'];}};
 recognition.handleAssistantSubmit({id:'source-credential-form'});
 await new Promise(resolve=>setTimeout(resolve,0));
 globalThis.FormData=originalFormData;
 assert.match(recognition.sourceManagementPage(),/role="alert"/);
 recognition.handleAssistantInput({target:{dataset:{agentSyncSource:'teaching_web'},checked:true}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(recognition.sourceManagementPage(),/来源选择保存失败/);
});

test('existing undated tasks stay in the task center instead of the current import review',async()=>{
 recognition.resetAssistant();
 session.user=null;
 const task={id:'existing-task',title:'查看讨论课分班情况',source:'教学网',due:'',status:'not_started',recognitionHistory:[{storageId:'candidate-old',action:'create'}]};
 recognition.configureAssistant({getState:()=>({tasks:[task]}),render(){}});
 const page=recognition.assistantPage();
 assert.doesNotMatch(page,/import-review-section|import-ignored-section/);
 assert.doesNotMatch(page,/existing-task|查看讨论课分班情况/);
 assert.doesNotMatch(calendarPage({tasks:[task],schedule:[]},0),/查看讨论课分班情况|待补日期/);
 task.due=dayKey();
 assert.match(calendarPage({tasks:[task],schedule:[]},0),/查看讨论课分班情况/);
});

test('refresh and two successive imports retain account pending and ignored candidates',async()=>{
 recognition.resetAssistant();session.user={id:'recovery'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batches=[];
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/sources/credentials')return {ok:true,json:async()=>({configured:false})};
  if(path==='/api/workspace')return {ok:true,json:async()=>({})};
  if(path==='/api/recognition/batches'){
   if(options.body){const body=JSON.parse(options.body);batches.push({request:body,result:{status:'success',record_results:[],errors:[]},items:[{id:'recover-'+batches.length,version:1,state:'pending',candidate:{...fixture.expected.tasks[0],input_id:body.records[0].input_id,title:'保留通知 '+batches.length}}]});}
   return {ok:true,json:async()=>structuredClone(batches)};
  }
  if(path.includes('/resolve')){batches[0].items[0].state='ignored';return {ok:true,json:async()=>({batches:structuredClone(batches)})};}
  throw new Error('unexpected '+path);
 };
 recognition.configureAssistant({render(){},refresh:async()=>{}});
 await recognition.submitRecognitionText('一');
 await recognition.handleAssistantClick({dataset:{agentIgnore:'recover-0'}});
 await recognition.submitRecognitionText('二');
 await recognition.submitRecognitionText('三');
 assert.doesNotMatch(recognition.assistantPage(),/data-agent-confirm="recover-1"/);
 await recognition.handleAssistantClick({dataset:{agentReviewScope:'all'}});
 assert.match(recognition.assistantPage(),/data-agent-confirm="recover-1"/);
 assert.match(recognition.assistantPage(),/data-agent-confirm="recover-2"/);
 assert.match(recognition.assistantPage(),/data-agent-restore="recover-0"/);
 recognition.resetAssistant();recognition.assistantPage();await new Promise(resolve=>setImmediate(resolve));
 const restored=recognition.assistantPage();
 assert.match(restored,/待核对 <span>2/);
 assert.match(restored,/data-agent-restore="recover-0"/);
 assert.match(restored,/data-agent-confirm="recover-1"/);
 assert.match(restored,/data-agent-confirm="recover-2"/);
 window.dispatchEvent(new Event('niuma:account-reset'));
 batches.length=0;session.user={id:'other'};
 recognition.assistantPage();await new Promise(resolve=>setImmediate(resolve));
 assert.doesNotMatch(recognition.assistantPage(),/recover-[012]/);
});

test('teaching web auto-import opens a real planning preview and writes schedules only after confirmation',async()=>{
 globalThis.document ||= new EventTarget();
 const store=await import('../src/team-store.js'),planner=await import('../src/team-planner-ui.js');
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batch={request:fixture.request,result:fixture.expected,items:[{id:'imported',version:1,state:'pending',candidate:fixture.expected.tasks[0]}]};
 const workspace={tasks:{version:0,value:[]}};let state,opened=0;const calls=[];
 const reply=value=>({ok:true,json:async()=>structuredClone(value)});
 globalThis.fetch=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
  if(path==='/api/auth/me')return reply({user:{id:'planning-test'}});
  if(path==='/api/workspace')return reply(workspace);
  if(path==='/api/recognition/batches')return reply([]);
  if(path==='/api/recognition/retry')return reply([batch]);
  if(path.includes('/resolve')){
   batch.items[0].state='applied';workspace.tasks.version++;
   workspace.tasks.value=[{id:'homework',title:'作业',status:'not_started',due:{date:'2026-10-11',precision:'date'},subtasks:[],plans:[],recognitionHistory:[{storageId:'imported'}]}];return reply({batches:[batch]});
  }
  if(path==='/api/data/availability')return reply({version:1});
  if(path==='/api/planning/proposals')return reply({requestId:'plan',estimates:{homework:60},blocks:[{taskId:'homework',title:'写作业',startAt:'2026-10-07T19:00:00+08:00',endAt:'2026-10-07T20:00:00+08:00'}],warnings:[]});
  if(path==='/api/planning/apply'){workspace.tasks.value[0].plans=[{id:'block',title:'写作业',startAt:'2026-10-07T19:00:00+08:00',endAt:'2026-10-07T20:00:00+08:00'}];workspace.tasks.version++;return reply({});}
  throw new Error('unexpected '+path);
 };
 state=await store.initSession();window.dispatchEvent(new Event('niuma:account-reset'));
 const host={getState:()=>state,render(){},toast(){},refresh:async()=>{state=await store.refreshStore();}};
 planner.configurePlanner({...host,openPreview(){opened++;}});
 recognition.configureAssistant({...host,proposeForTasks:planner.proposeForTasks});
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'teaching.json',size:100,text:async()=>JSON.stringify(fixture.request)}]}});
 await new Promise(resolve=>setImmediate(resolve));
 recognition.handleAssistantInput({target:{id:'agent-import-consent',checked:true}});
 await recognition.handleAssistantClick({dataset:{agentImportIndex:'0'}});
 assert.equal(opened,1);
 assert.deepEqual(calls.find(call=>call.path==='/api/planning/proposals').body,{taskIds:['homework']});
 assert.match(planner.plannerPage(),/排程提案/);
 assert.equal(state.schedule.length,0);
 assert.equal(calls.some(call=>call.path==='/api/planning/apply'),false);
 await planner.plannerClick({dataset:{planAction:'apply'}});
 assert.equal(state.schedule.length,1);
});

test('quick confirmation of an external candidate opens planning only for eligible saved tasks',async()=>{
 recognition.resetAssistant();session.user={id:'quick-planning'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const item={id:'review-external',version:1,state:'pending',candidate:{...fixture.expected.tasks[0],needs_review:true,review_reasons:['核对正文']}};
 const batch={request:fixture.request,result:fixture.expected,items:[item]};
 let tasks=[];const planned=[];
 globalThis.fetch=async(path)=>{
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[batch]};
  if(path.endsWith('/resolve')){item.state='applied';return {ok:true,json:async()=>({batches:[batch]})};}
  throw new Error('unexpected '+path);
 };
 recognition.configureAssistant({render(){},toast(){},getState:()=>({tasks}),refresh:async()=>{
  const base={due:'2026-10-11',status:'todo',plans:[],recognitionHistory:[{storageId:item.id}]};
  tasks=[{...base,id:'eligible'},{...base,id:'completed',status:'done'},{...base,id:'excluded',planningExcluded:true},{...base,id:'scheduled',plans:[{id:'block'}]},{...base,id:'undated',due:''}];
 },proposeForTasks:async ids=>planned.push(ids)});
 recognition.assistantPage();await new Promise(resolve=>setImmediate(resolve));
 await recognition.handleAssistantClick({dataset:{agentConfirm:item.id}});
 assert.deepEqual(planned,[['eligible']]);
 await recognition.handleAssistantClick({dataset:{agentConfirm:item.id}});
 assert.deepEqual(planned,[['eligible']]);
});

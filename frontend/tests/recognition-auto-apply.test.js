import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.window=new EventTarget();
globalThis.location={hash:'#calendar'};
globalThis.requestAnimationFrame=()=>{};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');
const {calendarPage}=await import('../src/team-calendar-ui.js');

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
 globalThis.fetch=async(path)=>{
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[{request:{request_id:'manual',records:[]},result:{status:'success',record_results:[],errors:[]},demo:false,items:[{id:'manual-candidate',version:1,state:'pending',candidate}]}]};
  if(path.includes('/resolve'))resolved++;
  throw new Error('unexpected request '+path);
 };
 await recognition.submitRecognitionText('10月1日提交报告');
 assert.equal(resolved,0);
 assert.match(recognition.assistantPage(),/待核对 <span>1/);
 assert.match(recognition.assistantPage(),/data-agent-review="manual-candidate"/);
});

test('external F3 output auto-adds dated and undated actions but leaves review candidates pending',async()=>{
 recognition.resetAssistant();session.user={id:'test'};
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-classification-v02.json',import.meta.url)));
 const batch={request:fixture.request,result:fixture.expected,demo:false,items:fixture.expected.tasks.map(task=>({id:task.candidate_id,version:1,state:'pending',candidate:task}))};
 const applied=[];let refreshed=0;
 recognition.configureAssistant({render(){},refresh:async()=>{refreshed++;}});
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'source.json',size:100,text:async()=>JSON.stringify(fixture.request)}]}});
 await new Promise(resolve=>setTimeout(resolve,0));
 recognition.handleAssistantInput({target:{id:'agent-import-consent',checked:true}});
 globalThis.fetch=async(path,options)=>{
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
 assert.match(recognition.assistantPage(),/待核对 <span>6/);
 assert.doesNotMatch(recognition.assistantPage(),/data-agent-review="synthetic:f3:old-yearless/);
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
 assert.match(recognition.assistantPage(),/待核对 <span>1/);
});

test('existing undated tasks appear on the workbench and open the same task for editing',async()=>{
 recognition.resetAssistant();
 session.user=null;
 const task={id:'existing-task',title:'查看讨论课分班情况',source:'教学网',due:'',status:'not_started',recognitionHistory:[{storageId:'candidate-old',action:'create'}]};
 let edited='';
 recognition.configureAssistant({getState:()=>({tasks:[task]}),editTask:id=>{edited=id;},render(){}});
 const page=recognition.assistantPage();
 assert.match(page,/待补日期 <span>1/);
 assert.match(page,/data-agent-edit-task="existing-task"/);
 assert.doesNotMatch(calendarPage({tasks:[task],schedule:[]},0),/查看讨论课分班情况|待补日期/);
 await recognition.handleAssistantClick({dataset:{agentEditTask:'existing-task'}});
 assert.equal(edited,task.id);
 task.due='2026-10-01';
 assert.match(recognition.assistantPage(),/待补日期 <span>0/);
 assert.match(calendarPage({tasks:[task],schedule:[]},0),/查看讨论课分班情况/);
});

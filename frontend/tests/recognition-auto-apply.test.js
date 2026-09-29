import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.window=new EventTarget();
globalThis.location={hash:'#calendar'};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');

test('dated create tasks are eligible, including inferred dates, but undated tasks wait',async()=>{
 session.user={id:'test'};
 const clear={notice_type:'create',needs_review:false,deadline:{kind:'deadline',status:'explicit',precision:'date',value:'2026-09-30'}};
 const uncertain={notice_type:'uncertain',needs_review:true,deadline:{kind:'unknown',status:'unknown',precision:'unknown'}};
 assert.equal(recognition.canAutoApplyCandidate(clear),true);
 assert.equal(recognition.canAutoApplyCandidate({...clear,deadline:{...clear.deadline,status:'inferred'}}),true);
 assert.equal(recognition.canAutoApplyCandidate({...clear,deadline:{status:'not_provided'}}),false);
 assert.equal(recognition.canAutoApplyCandidate(uncertain),false);
 let resolved=0,refreshed=0;
 recognition.configureAssistant({render(){},refresh:async()=>{refreshed++;}});
 globalThis.fetch=async(path,options)=>{
  const request=JSON.parse(options.body);
  if(path==='/api/recognition/batches')return {ok:true,json:async()=>[{
   request,result:{status:'success'},demo:false,items:[{id:'candidate-1',version:1,state:'pending',candidate:clear}],
  }]};
  if(path==='/api/recognition/candidates/candidate-1/resolve'){
   resolved++;
   assert.deepEqual(request,{action:'create',auto_apply:true,version:1});
   return {ok:true,json:async()=>({batches:[{request:{request_id:request.request_id},items:[]}]})};
  }
  throw new Error('unexpected request '+path);
 };
 await recognition.submitRecognitionText('9月30日提交报告');
 assert.equal(resolved,1);
 assert.equal(refreshed,1);
});

test('external source recognition leaves undated tasks for manual completion',async()=>{
 const fixture=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-contract-v0.2.json',import.meta.url)));
 const request=fixture.request;
 request.request_id='external-auto-test';
 request.records.forEach(record=>record.platform='teaching_web');
 let resolved=0,refreshed=0;
 recognition.configureAssistant({render(){},refresh:async()=>{refreshed++;}});
 recognition.handleAssistantInput({type:'change',target:{id:'agent-source-file',files:[{name:'source.jsonl',size:100,text:async()=>JSON.stringify(request)}]}});
 await new Promise(resolve=>setTimeout(resolve,0));
 recognition.handleAssistantInput({target:{id:'agent-import-consent',checked:true}});
 globalThis.fetch=async(path,options)=>{
  if(path==='/api/recognition/retry')return {ok:true,json:async()=>[{request,result:{status:'success',record_results:[],errors:[]},demo:false,items:[{id:'candidate-2',version:1,state:'pending',candidate:fixture.expected.tasks.find(task=>task.candidate_id==='c2')},{id:'unknown-date',version:1,state:'pending',candidate:{...fixture.expected.tasks[0],deadline:{kind:'deadline',status:'unknown',precision:'unknown',value:null}}}]},{request:{request_id:'empty',records:[]},result:{status:'no_tasks',record_results:[],errors:[]},demo:false,items:[]}]};
  if(path==='/api/recognition/candidates/candidate-2/resolve'){resolved++;throw new Error('undated candidate must not auto-apply');}
  throw new Error('unexpected request '+path);
 };
 await recognition.handleAssistantClick({dataset:{agentImportIndex:'0'}});
 assert.equal(resolved,0);
 assert.equal(refreshed,0);
 assert.match(recognition.assistantPage(),/待补日期 <span>1/);
 assert.doesNotMatch(recognition.assistantPage(),/unknown-date|未发现任务/);
});

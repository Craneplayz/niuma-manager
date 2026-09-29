import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

globalThis.window=new EventTarget();
globalThis.location={hash:'#calendar'};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');

test('manual recognition waits for confirmation while external clear tasks are eligible',async()=>{
 session.user={id:'test'};
 const clear={notice_type:'create',needs_review:false,deadline:{kind:'deadline',status:'explicit',precision:'date'}};
 const uncertain={notice_type:'uncertain',needs_review:true,deadline:{kind:'unknown',status:'unknown',precision:'unknown'}};
 assert.equal(recognition.canAutoApplyCandidate(clear),true);
 assert.equal(recognition.canAutoApplyCandidate({...clear,needs_review:true,deadline:{status:'not_provided'}}),true);
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
 assert.equal(resolved,0);
 assert.equal(refreshed,0);
});

test('external source recognition resolves create candidates automatically',async()=>{
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
  if(path==='/api/recognition/retry')return {ok:true,json:async()=>[{request,result:{status:'success'},demo:false,items:[{id:'candidate-2',version:1,state:'pending',candidate:{notice_type:'create',needs_review:true,deadline:{kind:'unknown',status:'not_provided',precision:'unknown'}}}]}]};
  if(path==='/api/recognition/candidates/candidate-2/resolve'){
   assert.equal(JSON.parse(options.body).auto_apply,true);resolved++;
   return {ok:true,json:async()=>({batches:[{request,items:[]}]})};
  }
  throw new Error('unexpected request '+path);
 };
 await recognition.handleAssistantClick({dataset:{agentImportIndex:'0'}});
 assert.equal(resolved,1);
 assert.equal(refreshed,1);
});

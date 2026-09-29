import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.location={hash:'#calendar'};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');

test('clear new task is automatically resolved, uncertain task is not',async()=>{
 session.user={id:'test'};
 const clear={notice_type:'create',needs_review:false,deadline:{kind:'deadline',status:'explicit',precision:'date'}};
 const uncertain={notice_type:'uncertain',needs_review:true,deadline:{kind:'unknown',status:'unknown',precision:'unknown'}};
 assert.equal(recognition.canAutoApplyCandidate(clear),true);
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

import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.location={hash:'#agent'};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');

test('partial source and failed model records show readable review messages',async()=>{
 session.user={id:'test'};
 const record={input_id:'private-id',text:'通知标题',course_or_project:'数据库课'};
 globalThis.fetch=async path=>({ok:true,json:async()=>path==='/api/recognition/batches'?[{
  request:{request_id:'sample',records:[record]},
  result:{status:'failed',record_results:[{input_id:record.input_id,status:'failed',warnings:['incomplete_text']}],
   errors:[{input_id:record.input_id,code:'agent_timeout',message:'原始错误'}]},demo:false,items:[],
 }]:{configured:false}});
 recognition.configureAssistant({render(){}});
 recognition.assistantPage();
 await new Promise(resolve=>setImmediate(resolve));
 const html=recognition.assistantPage();
 assert.match(html,/正文不完整/);
 assert.match(html,/数据库课：识别超时/);
 assert.match(html,/重试失败记录/);
 assert.doesNotMatch(html,/incomplete_text|private-id|原始错误/);
});

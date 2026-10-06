import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.location={hash:'#agent'};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');

test('historical failed batches do not leak into a new import session',async()=>{
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
 assert.doesNotMatch(html,/import-review-section|import-ignored-section/);
 assert.doesNotMatch(html,/正文不完整|数据库课|重试失败记录|incomplete_text|private-id|原始错误/);
});

test('notification import only shows teaching web after it is selected as a sync source',async()=>{
 session.user={id:'test'};
 let sources=[];
 globalThis.fetch=async path=>({ok:true,json:async()=>{
  if(path==='/api/workspace')return {sources:{value:sources,version:1}};
  if(path==='/api/sources/credentials')return {configured:false};
  if(path==='/api/recognition/batches')return [];
  throw new Error('unexpected request '+path);
 }});
 recognition.configureAssistant({render(){}});
 recognition.resetAssistant();recognition.assistantPage();
 await new Promise(resolve=>setImmediate(resolve));
 let html=recognition.assistantPage();
 assert.match(html,/data-agent-source-mode="manual"/);
 assert.match(html,/data-agent-source-mode="wechat"/);
 assert.doesNotMatch(html,/data-agent-source-mode="teaching_web"/);

 sources=['teaching_web'];recognition.resetAssistant();recognition.assistantPage();
 await new Promise(resolve=>setImmediate(resolve));
 html=recognition.assistantPage();
 assert.match(html,/data-agent-source-mode="teaching_web"/);
});

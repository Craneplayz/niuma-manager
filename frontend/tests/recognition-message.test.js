import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.location={hash:'#agent'};
const {session}=await import('../src/team-store.js');
const recognition=await import('../src/team-recognition-ui.js');

test('pending review uses the saved full original and preserves attachment locations',async()=>{
 recognition.resetAssistant();session.user={id:'evidence-review'};location.hash='#agent';
 const original='作业通知\n完整要求 <script>\n请提交报告。\n末尾补充说明';
 const candidate={input_id:'source-1',notice_type:'create',title:'提交报告',description:'不重复展示的模型摘要',
  deadline:{status:'not_provided',value:null,basis:'不重复展示的日期分析'},review_reasons:['附件尚未完整读取'],
  sources:[{input_id:'source-1',platform:'teaching_web',label:'作业通知',url:'https://course.example.test/notice'}],
  evidence:[{input_id:'source-1',quote:'请提交报告。'},
   {input_id:'source-1',quote:'附件中的补充要求',asset_id:'attachment-1',locator:{page:2},verification:'model_reported'}]};
 const item={id:'review-1',state:'pending',candidate};
 globalThis.fetch=async path=>({ok:true,json:async()=>path==='/api/recognition/batches'?[{
  request:{request_id:'batch-1',records:[{input_id:'source-1',text:original,platform:'teaching_web'}]},
  result:{status:'success',record_results:[],errors:[]},items:[item],demo:false,
 }]:path==='/api/workspace'?{sources:{value:[],version:1}}:{configured:false}});
 recognition.configureAssistant({render(){}});recognition.assistantPage();
 await new Promise(resolve=>setImmediate(resolve));
 const html=recognition.candidateCard(item);
 assert.match(html,/打开教学网原页面/);assert.match(html,/查看完整通知原文/);
 assert.match(html,/完整要求 &lt;script&gt;[\s\S]*末尾补充说明/);assert.doesNotMatch(html,/<script>/);
 assert.doesNotMatch(html,/不重复展示的模型摘要|不重复展示的日期分析|draft-warning|<strong>/);
 assert.equal((html.match(/请提交报告。/g)||[]).length,1);
 assert.match(html,/附件 attachment-1 · 第 2 页 · 模型报告（model_reported），未经逐字核验/);
 assert.match(html,/<details class="task-evidence-quotes"><summary>查看补充证据<\/summary>/);
 assert.match(html,/核对说明/);assert.match(html,/附件尚未完整读取/);
 recognition.resetAssistant();
});

test('historical failed batches stay in collapsible history outside the current import',async()=>{
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
 assert.match(html,/import-history-batch/);
 assert.match(html,/重试失败记录/);
 assert.doesNotMatch(html,/private-id|原始错误/);
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

test('source management retains attachment sync and targeted reprocessing',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'integration'};location.hash='#settings';
 globalThis.fetch=async path=>({ok:true,json:async()=>{
  if(path==='/api/workspace')return {sources:{value:[],version:1}};
  if(path.includes('/reprocessing/sources'))return {accounts:[],next_cursor:null};
  if(path.includes('/reprocessing/runs'))return {runs:[],next_cursor:null};
  if(path.endsWith('/imports'))return {jobs:[]};
  return {configured:false};
 }});
 recognition.configureAssistant({render(){},refresh:async()=>{}});
 const html=recognition.sourceManagementPage(true);
 assert.match(html,/微信附件同步/);
 assert.match(html,/重新识别已有通知/);
 assert.match(html,/导入来源文件/);
 await new Promise(resolve=>setImmediate(resolve));location.hash='#agent';
});

test('attachment failures retry through attachment management',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'integration'};location.hash='#agent';
 const paths=[];
 globalThis.fetch=async path=>{paths.push(path);return {ok:true,json:async()=>{
  if(path==='/api/recognition/batches')return [{request:{request_id:'attachment-failure',contract_version:'0.3.1',records:[]},result:{status:'failed',record_results:[],errors:[]},items:[],demo:false}];
  if(path==='/api/workspace')return {sources:{value:[],version:1}};
  return {configured:false};
 }};};
 recognition.configureAssistant({render(){}});recognition.assistantPage();
 await new Promise(resolve=>setImmediate(resolve));
 const before=paths.length;
 await recognition.handleAssistantClick({dataset:{agentRetry:'attachment-failure'}});
 assert.equal(location.hash,'#settings');assert.equal(paths.length,before);location.hash='#agent';
});

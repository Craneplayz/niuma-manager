import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {operationsUrl,renderOperations,formatBeijingTime} from '../src/admin-operations.js';

test('operations defaults to the server 24-hour range and encodes explicit usage dates',()=>{
 assert.equal(operationsUrl(),'/api/admin/operations');
 assert.equal(operationsUrl('2026-10-01','2026-10-20'),'/api/admin/operations?start=2026-10-01&end=2026-10-20');
 assert.equal(operationsUrl('2026-10-01'),'/api/admin/operations');
 assert.ok(!operationsUrl('x&extra=1','y').includes('&extra='));
});
test('missing fields and unavailable historical resources render without invented zeros',()=>{
 for(const data of [undefined,null,{}, {requests:[null],resources:null}, {resources:{available:false,cpuPercent:0,memoryUsedBytes:0,restarts:0}}]){
  const html=renderOperations(data);
  assert.match(html,/暂无可用采样/);
  assert.doesNotMatch(html,/0\.0%|0\.00 GB|NaN|undefined|Infinity/);
  assert.match(html,/只读追踪/);
 }
});
test('request totals, percentiles, queue, run outcomes and real resource zeros are preserved',()=>{
 const html=renderOperations({requests:[{operation:'recognition',count:10,success:8,failures:2,p50Ms:20,p95Ms:50,errors:[{code:'timeout',count:2}]}],stages:[{stage:'model',count:10,success:8,failures:2,p50Ms:15,p95Ms:40}],queue:{queued:2,modelRunning:1},runs:{partial_success:2},resources:{available:true,cpuPercent:0,memoryUsedBytes:1024**3,memoryTotalBytes:2*1024**3,restarts:0},outcomes:[{code:'no_tasks',count:3}],droppedEvents:0});
 assert.match(html,/80\.0%/);assert.match(html,/20 ms/);assert.match(html,/50 ms/);
 assert.match(html,/timeout \/ 2/);assert.match(html,/1\.00 GB \/ 2\.00 GB/);assert.match(html,/0\.0%/);
 assert.match(html,/class="quality-bar"><i style="width:80%"><\/i><i class="failed" style="width:20%"/);
 assert.match(html,/<td>部分成功<\/td><td>2<\/td>/);assert.match(html,/<td>无任务<\/td><td>3<\/td>/);
 assert.doesNotMatch(html,/暂无可用采样/);
});
test('Unix seconds and zoned ISO timestamps render as Beijing time, with invalid values suppressed',()=>{
 const seconds=Date.parse('2026-10-20T08:00:00Z')/1000;
 for(const value of [seconds,'2026-10-20T08:00:00Z','2026-10-20T16:00:00+08:00'])assert.equal(formatBeijingTime(value),'2026-10-20 16:00:00');
 assert.equal(formatBeijingTime(0),'1970-01-01 08:00:00');
 for(const value of [null,undefined,'',String(seconds),'<script>',NaN,Infinity,1e100,{},-1,'2026-10-20T08:00:00'])assert.equal(formatBeijingTime(value),'—');
 const html=renderOperations({resources:{observedAt:seconds,available:false},trace:[{at:seconds}],alerts:[{firstSeen:seconds,lastSeen:seconds,lastNotified:seconds}]},seconds*1000);
 assert.equal((html.match(/2026-10-20 16:00:00/g)||[]).length,5);
 assert.doesNotMatch(html,new RegExp(String(seconds)));
});
test('sampling expires strictly after 180 seconds and unavailable resources never show retained values',()=>{
 const observedAt=Date.parse('2026-10-20T08:00:00Z')/1000;
 const resources={available:true,observedAt,cpuPercent:12.5,memoryUsedBytes:1024**3,memoryTotalBytes:4*1024**3,diskUsedBytes:2*1024**3,diskTotalBytes:8*1024**3,restarts:9};
 const fresh=renderOperations({resources},(observedAt+180)*1000);
 assert.match(fresh,/12\.5%/);assert.doesNotMatch(fresh,/采样已过期/);
 for(const available of [true,false]){
  const stale=renderOperations({resources:{...resources,available}},(observedAt+180)*1000+1);
  assert.match(stale,/采样已过期/);assert.doesNotMatch(stale,/12\.5%|1\.00 GB|2\.00 GB|<strong>9<\/strong>/);
 }
 const unavailable=renderOperations({resources:{...resources,available:false}},observedAt*1000);
 assert.doesNotMatch(unavailable,/12\.5%|1\.00 GB|2\.00 GB|<strong>9<\/strong>|采样已过期/);
});
test('all backend stages, operation verbs, outcomes and states have Chinese labels',()=>{
 const stages=['http','sync_queue','model_queue','collection','attachment_download','attachment_read','model_call','recognition','validation','save','database_lock','commit','outcome','run'];
 const codes=['ok','http_400','unauthorized','forbidden','not_found','conflict','too_large','rate_limited','queue_unavailable','model_configuration','agent_timeout','agent_unavailable','invalid_agent_output','collection_failed','source_changed','source_incomplete','save_failed','database_busy','cache_hit','cache_miss','retry','no_tasks','filtered','succeeded','partial_success','failed','stopped','interrupted','upstream_error','internal_error','cancelled','output_truncated'];
 const operations=['auth.login','auth.email_code','auth.reset_password','sync.get','materials.post','workspace.put','sources.delete'];
 const html=renderOperations({stages:stages.map(stage=>({stage,count:0})),requests:operations.map(operation=>({operation,count:0,success:0,failures:0})),outcomes:codes.map(code=>({code,count:0})),trace:[{stage:'model_queue',operation:'auth.login',result:'cancelled',code:'agent_timeout'}],alerts:[{code:'database_busy',state:'resolved'}]});
 for(const name of [...stages,...codes,...operations])assert.ok(!html.includes(`<td>${name}</td>`),name);
 for(const translated of ['模型排队','同步排队','采集总耗时含下载','数据库写锁等待','登录','发送邮箱验证码','重置密码','材料处理 · 提交','已恢复','模型响应超时'])assert.ok(html.includes(translated),translated);
 assert.match(html,/<strong>0<\/strong>/);assert.doesNotMatch(html,/NaN|Infinity/);
 assert.match(renderOperations({requests:[]}),/请求总数<\/span><strong>0<\/strong>/);
 assert.match(renderOperations({}),/请求总数<\/span><strong>—<\/strong>/);
});
test('alert lifecycle distinguishes resolved history and shows only boolean delivery failures',()=>{
 const states={pending:'未触发',firing:'待通知',active:'告警中',recovering:'待恢复通知',resolved:'已恢复'};
 for(const [state,translated] of Object.entries(states)){
  const html=renderOperations({alerts:[{code:'database_busy',state,deliveryError:true}]});
  assert.match(html,/<h2>告警记录<\/h2>/);
  assert.ok(html.includes(`<td>${translated}</td>`));
  assert.match(html,/通知失败，待重试/);
  if(state==='resolved')assert.doesNotMatch(html,/告警中|当前告警/);
 }
 for(const deliveryError of [false,undefined,null,'true','<img src=x>']){
  const html=renderOperations({alerts:[{state:'resolved',deliveryError}]});
  assert.doesNotMatch(html,/通知失败|<img/);
 }
});
test('service health is independent of resource sampling and restart counts remain nullable',()=>{
 const observedAt=Date.parse('2026-10-20T08:00:00Z')/1000;
 for(const restarts of [0,3,null]){
  const html=renderOperations({resources:{available:true,serviceAvailable:false,observedAt,cpuPercent:12.5,restarts}},observedAt*1000);
  assert.match(html,/12\.5%/);
  assert.ok(html.includes(`重启次数</span><strong>${restarts??'—'}</strong>`));
  assert.doesNotMatch(html,/暂无可用采样|采样已过期/);
 }
 const unavailable=renderOperations({resources:{available:false,serviceAvailable:true,observedAt,cpuPercent:12.5,restarts:3}},observedAt*1000);
 assert.doesNotMatch(unavailable,/12\.5%|<strong>3<\/strong>/);
 assert.match(unavailable,/暂无可用采样/);
});
test('every textual API field is escaped and invalid metrics never enter CSS or numeric output',()=>{
 const attack='<img src=x onerror="alert(1)">';
 const html=renderOperations({environment:attack,revision:attack,generatedAt:attack,start:attack,end:attack,retentionDays:attack,methodology:attack,requests:[{operation:attack,p50Ms:attack,p95Ms:Infinity,errors:[{code:attack,count:attack}]}],stages:[{stage:attack,p95Ms:-1}],trace:[Object.fromEntries(['at','requestId','runId','stage','operation','result','code','durationMs'].map(key=>[key,attack]))],alerts:[Object.fromEntries(['code','state','firstSeen','lastSeen','lastNotified'].map(key=>[key,attack]))],outcomes:[{code:attack,count:attack}]});
 assert.doesNotMatch(html,/<img|onerror="|NaN|Infinity|width:-/);
 assert.match(html,/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});
test('operations is imported only by the standalone admin entry',async()=>{
 const admin=await readFile(new URL('../src/admin-usage.js',import.meta.url),'utf8');
 const app=await readFile(new URL('../src/app.js',import.meta.url),'utf8');
 assert.match(admin,/mountOperations\(root,request\)/);
 assert.match(admin,/id="usage-panel"/);assert.match(admin,/renderErrorReports\(data.errorReports\)/);
 assert.doesNotMatch(app,/admin-operations|运行监控|\/api\/admin\/operations/);
});

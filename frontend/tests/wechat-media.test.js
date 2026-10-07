import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.location={hash:'#sources'};
const {session}=await import('../src/team-store.js');
const media=await import('../src/team-wechat-media-ui.js');
session.user={id:'test'};
media.configureWechatMedia({render(){}});
const response=value=>({ok:true,json:async()=>value});

test('source page distinguishes pending, failed, no tasks and shows validated evidence safely',async()=>{
 const job={job_id:'job-one',request_id:'request-one',status:'partial_failed',progress:{total_records:2,finished_records:2,failed_records:1},
  sources:{'msg-1':{label:'课程群',assets:{'asset-1':'作业.png'}}},
  result:{status:'partial_success',tasks:[{title:'提交作业',description:'完成报告',needs_review:true,deadline:{status:'explicit',value:'2026-10-05T09:00:00+08:00'},evidence:[{input_id:'msg-1',asset_id:'asset-1',field:'attachment_file',quote:'<作业要求>',locator:{page:2}}]}],errors:[{stage:'read',asset_id:'asset-2',message:'文件读取失败',retryable:false}]}};
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:false}):response({jobs:[job]});
 await media.load();
 const html=media.wechatMediaPanel();
 assert.match(html,/部分失败|2\/2 条已处理/);
 assert.match(html,/作业\.png|第 2 页|模型读取/);
 assert.match(html,/&lt;作业要求&gt;/);
 assert.doesNotMatch(html,/file:\/\/|data-media-action="retry"/);
 job.status='succeeded';job.result={status:'no_tasks',tasks:[],errors:[],record_results:[{coverage:{complete:true,uncertain:false},attachments:[]}]};
 await media.load();
 assert.match(media.wechatMediaPanel(),/已完整识别，未发现任务/);
});

test('attachment coverage and quality stay distinct from completed no-task recognition',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'coverage-display'};location.hash='#settings';
 media.configureWechatMedia({render(){}});
 const job={job_id:'coverage-job',status:'partial_failed',progress:{total_records:1,finished_records:1,failed_records:1},
  sources:{message:{assets:{file:'作业图片.png'}}},result:{status:'partial_success',tasks:[],errors:[],record_results:[{
   input_id:'message',coverage:{complete:false,uncertain:true},attachments:[{asset_id:'file',status:'partial',quality:'uncertain',
    coverage:{complete:false,truncated:true,unread_locations:['底部<截止日期>未读取']},quality_notes:['文字模糊']}]}]}};
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):response({jobs:[job]});
 await media.load();let html=media.wechatMediaPanel();
 assert.match(html,/部分内容未读完整|仅部分读取|清晰度需核对|内容被截断/);
 assert.match(html,/作业图片\.png|底部&lt;截止日期&gt;未读取|文字模糊/);
 assert.match(html,/<details><summary>附件读取情况/);
 assert.doesNotMatch(html,/<details open|已完整识别/);
 job.result.status='no_tasks';await media.load();
 assert.match(media.wechatMediaPanel(),/本次未发现任务，仍有内容未读完整/);
 job.result.record_results[0].coverage={complete:true,uncertain:true};await media.load();
 assert.match(media.wechatMediaPanel(),/读取情况仍需核对/);
 assert.doesNotMatch(media.wechatMediaPanel(),/已完整识别/);
 window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
});

test('evidence labels follow each candidate context instead of a global overwritten label',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'context-evidence'};location.hash='#settings';
 media.configureWechatMedia({render(){}});
 const job={job_id:'context-label',status:'succeeded',progress:{total_records:1,finished_records:1,failed_records:0},
  sources:{reply:{label:'其他记录的群名',assets:{file:'报告.png'}}},result:{status:'success',errors:[],tasks:[{
   title:'交报告',description:'课程报告',deadline:{status:'not_provided'},needs_review:true,
   sources:[{input_id:'reply',label:'候选引用的<课程群>'}],
   evidence:[{input_id:'reply',asset_id:'file',field:'attachment_file',quote:'10月9日交报告',locator:{page:null,paragraph:null}}]}]}};
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):response({jobs:[job]});
 await media.load();const html=media.wechatMediaPanel();
 assert.match(html,/候选引用的&lt;课程群&gt; · 报告\.png · 模型读取/);
 assert.doesNotMatch(html,/其他记录的群名|第 1 页/);
 window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
});

test('one-time credential is erased on account reset',async()=>{
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true,token:'private-once'}):response({jobs:[]});
 await media.wechatMediaClick({dataset:{mediaAction:'issue'}});
 assert.match(media.wechatMediaPanel(),/private-once/);
 window.dispatchEvent(new Event('niuma:account-reset'));
 session.user=null;
 assert.doesNotMatch(media.wechatMediaPanel(),/private-once/);
});

test('source limits reflect the configured retention and stay folded by default',async()=>{
 session.user={id:'limits-test'};
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true,limits:{asset_bytes:20971520,account_bytes:209715200,queued_jobs:20,unreferenced_ttl_seconds:86400}}):response({jobs:[]});
 await media.load();
 const html=media.wechatMediaPanel();
 assert.match(html,/<details><summary>附件限制与保留/);
 assert.match(html,/20 MiB|200 MiB|24 小时/);
 assert.doesNotMatch(html,/<details open/);
});

test('settings exposes review and ignore for persisted media candidates',async()=>{
 session.user={id:'review-test'};location.hash='#settings';
 let reviewed='',ignored='';
 const item={id:'stored-media-candidate',candidate_id:'media-task',state:'pending',version:2};
 const job={job_id:'job-review',status:'succeeded',progress:{total_records:1,finished_records:1,failed_records:0},items:[item],result:{status:'success',tasks:[{candidate_id:'media-task',title:'核对附件作业',description:'报告',needs_review:true,deadline:{status:'not_provided',value:null},evidence:[]}],errors:[]}};
 media.configureWechatMedia({render(){},reviewCandidate:async id=>{reviewed=id;}});
 globalThis.fetch=async(path,options)=>{
  if(path.includes('/resolve')){ignored=path;assert.deepEqual(JSON.parse(options.body),{action:'ignore',version:2});item.state='ignored';return response({batches:[]});}
  return path.endsWith('/credential')?response({configured:false}):response({jobs:[job]});
 };
 await media.load();
 assert.match(media.wechatMediaPanel(),/data-media-action="review"|data-media-action="ignore"/);
 await media.wechatMediaClick({dataset:{mediaAction:'review',mediaCandidate:item.id}});
 assert.equal(reviewed,item.id);
 await media.wechatMediaClick({dataset:{mediaAction:'ignore',mediaCandidate:item.id}});
 assert.match(ignored,/stored-media-candidate\/resolve$/);
 assert.match(media.wechatMediaPanel(),/已忽略/);
 assert.doesNotMatch(media.wechatMediaPanel(),/data-media-action="review"/);
});

test('source deletion requires confirmation and discards a late polling response',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'remove-source'};location.hash='#settings';
 const job={job_id:'job-stale',request_id:'request-stale',status:'succeeded',progress:{total_records:1,finished_records:1,failed_records:0},result:{status:'no_tasks',tasks:[],errors:[]}};
 media.configureWechatMedia({render(){}});
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):response({jobs:[job]});
 await media.load();
 let resolveList,removed=0,changed=0;
 const changedListener=()=>{changed++;};window.addEventListener('niuma:recognition-changed',changedListener);
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/remove')){removed++;assert.deepEqual(JSON.parse(options.body),{confirmed:true});return response({configured:false,cleanup_pending:0});}
  if(path.endsWith('/credential'))return response({configured:true});
  return new Promise(resolve=>{resolveList=resolve;});
 };
 await media.wechatMediaClick({dataset:{mediaAction:'remove'}});
 assert.equal(removed,0);
 const poll=media.load();
 await media.wechatMediaClick({dataset:{mediaAction:'prepare-remove'}});
 assert.match(media.wechatMediaPanel(),/无法从本服务恢复/);
 await media.wechatMediaClick({dataset:{mediaAction:'remove'}});
 resolveList(response({jobs:[job]}));await poll;
 assert.equal(removed,1);assert.equal(changed,1);
 assert.doesNotMatch(media.wechatMediaPanel(),/微信导入 · 已完成/);
 assert.match(media.wechatMediaPanel(),/日历中的任务保留/);
 window.removeEventListener('niuma:recognition-changed',changedListener);
});

test('source deletion during workspace refresh does not start a stale planning request',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'refresh-deletion'};location.hash='#settings';
 const job={job_id:'refresh-job',status:'queued',progress:{total_records:1,finished_records:0,failed_records:0}};
 let finishRefresh,startedRefresh,plans=0,tasks=[];
 const started=new Promise(resolve=>{startedRefresh=resolve;});
 media.configureWechatMedia({render(){},getState:()=>({tasks}),refresh:()=>{
  startedRefresh();return new Promise(resolve=>{finishRefresh=()=>{tasks=[{id:'late-task',due:{date:'2026-10-09'},plans:[]}];resolve();};});
 },proposeForTasks:async()=>{plans++;}});
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):
  path.endsWith('/remove')?response({configured:false,cleanup_pending:0}):response({jobs:[structuredClone(job)]});
 await media.load();
 job.status='succeeded';job.result={status:'success',tasks:[{title:'旧任务'}]};
 const poll=media.load();await started;
 await media.wechatMediaClick({dataset:{mediaAction:'prepare-remove'}});
 await media.wechatMediaClick({dataset:{mediaAction:'remove'}});
 finishRefresh();await poll;
 assert.equal(plans,0);
 assert.doesNotMatch(media.wechatMediaPanel(),/旧任务/);
 window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
});

test('an old polling finally cannot clear the loading state of a newer source poll',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'poll-generations'};location.hash='#settings';
 media.configureWechatMedia({render(){}});
 const polls=[];
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):
  path.endsWith('/remove')?response({configured:false,cleanup_pending:0}):new Promise(resolve=>{polls.push(resolve);});
 const old=media.load();
 await media.wechatMediaClick({dataset:{mediaAction:'prepare-remove'}});
 await media.wechatMediaClick({dataset:{mediaAction:'remove'}});
 const next=media.load();assert.equal(polls.length,2);
 polls[0](response({jobs:[]}));await old;
 await media.load();assert.equal(polls.length,2);
 polls[1](response({jobs:[]}));await next;
 assert.match(media.wechatMediaPanel(),/暂无微信附件导入记录/);
 window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
});

test('network failure keeps known credentials and persisted results until reconnection',async()=>{
 window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'network-recovery'};location.hash='#settings';
 media.configureWechatMedia({render(){}});
 const job={job_id:'offline-job',status:'succeeded',progress:{total_records:1,finished_records:1,failed_records:0},
  result:{status:'no_tasks',tasks:[],errors:[],record_results:[{coverage:{complete:true,uncertain:false},attachments:[]}]}};
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):response({jobs:[job]});
 await media.load();
 globalThis.fetch=async()=>{throw new TypeError('合成网络中断');};
 await media.load();let html=media.wechatMediaPanel();
 assert.match(html,/撤销本机导入凭证|已完整识别，未发现任务/);
 assert.doesNotMatch(html,/data-media-action="issue"/);
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true}):response({jobs:[]});
 await media.load();assert.match(media.wechatMediaPanel(),/暂无微信附件导入记录/);
 window.dispatchEvent(new Event('niuma:account-reset'));
 globalThis.fetch=async()=>{throw new TypeError('初次查询失败');};
 await media.load();html=media.wechatMediaPanel();
 assert.match(html,/data-media-action="issue" disabled/);
 window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
});

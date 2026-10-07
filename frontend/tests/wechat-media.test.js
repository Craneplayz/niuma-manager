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
 job.status='succeeded';job.result={status:'no_tasks',tasks:[],errors:[]};
 await media.load();
 assert.match(media.wechatMediaPanel(),/已完整识别，未发现任务/);
});

test('one-time credential is erased on account reset',async()=>{
 globalThis.fetch=async path=>path.endsWith('/credential')?response({configured:true,token:'private-once'}):response({jobs:[]});
 await media.wechatMediaClick({dataset:{mediaAction:'issue'}});
 assert.match(media.wechatMediaPanel(),/private-once/);
 window.dispatchEvent(new Event('niuma:account-reset'));
 session.user=null;
 assert.doesNotMatch(media.wechatMediaPanel(),/private-once/);
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

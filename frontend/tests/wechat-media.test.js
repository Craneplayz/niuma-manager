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

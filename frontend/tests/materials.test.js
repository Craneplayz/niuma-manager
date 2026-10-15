import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();
const {session}=await import('../src/team-store.js');
const {createMaterialsUI,buildMaterialRequest}=await import('../src/materials-ui.js');
const response=value=>({ok:true,json:async()=>structuredClone(value)});
const receipt={contract_version:'manual-materials/0.1',file_name:'合成通知.png',file_ref:'asset:one',mime_type:'image/png',size_bytes:3,sha256:'a'.repeat(64)};
function setup(t,{lost=false}={}){
 session.user={id:'test'};let changed=0,jobs=[],first=lost;const calls=[];
 globalThis.fetch=async(path,options={})=>{
  const body=options.body instanceof FormData?options.body:options.body?JSON.parse(options.body):undefined;
  calls.push({path,body,headers:options.headers});
  if(path.endsWith('/limits'))return response({limits:{asset_bytes:20*1048576,account_bytes:200*1048576}});
  if(path.endsWith('/assets')){assert.ok(body instanceof FormData);assert.equal(options.headers['Content-Type'],undefined);return response(receipt);}
  if(path.endsWith('/imports')&&!body)return response({jobs});
  if(path.endsWith('/imports')&&body){if(first){first=false;throw new Error('lost response');}jobs=[{job_id:'job:one',request_id:body.request_id,status:'queued',sources:{},items:[]}];return response(jobs[0]);}
  throw new Error(path);
 };
 const ui=createMaterialsUI({escapeHtml:s=>String(s??''),render(){},changed:async()=>changed++,isDemo:()=>false,isVisible:()=>false});t.after(()=>ui.reset());
 return {ui,calls,get changed(){return changed;},choose(){ui.input({target:{id:'material-files',files:[new File(['abc'],receipt.file_name,{type:'image/png'})]}});ui.input({target:{id:'material-consent',checked:true}});}};
}
test('manual attachment upload uses receipt metadata and session multipart, then refreshes task results',async t=>{
 const f=setup(t);await f.ui.load();f.choose();await f.ui.click({dataset:{materialAction:'upload'}});
 const sent=f.calls.find(c=>c.path.endsWith('/imports')&&c.body).body;
 assert.equal(sent.contract_version,'0.3.1');assert.equal(sent.records[0].attachments[0].sha256,receipt.sha256);
 assert.equal(sent.records[0].source_label,'用户主动上传的文件');assert.equal(f.changed,2);assert.match(f.ui.render(),/排队中/);
});
test('lost submission response reuses uploaded receipts and the exact request identity',async t=>{
 const f=setup(t,{lost:true});await f.ui.load();f.choose();await f.ui.click({dataset:{materialAction:'upload'}});await f.ui.click({dataset:{materialAction:'upload'}});
 const posts=f.calls.filter(c=>c.path.endsWith('/imports')&&c.body);assert.equal(posts.length,2);assert.deepEqual(posts[0].body,posts[1].body);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/assets')).length,1);
});
test('unsupported file metadata remains an explicit unsupported record',()=>{
 const r=buildMaterialRequest([{...receipt,mime_type:'application/zip'}],'request:one','2026-10-14T00:00:00Z');
 assert.equal(r.records[0].attachments[0].status,'unsupported');assert.equal(r.records[0].attachments[0].file_ref,null);
});

test('failed file jobs use a retry identity accepted by the runtime',async t=>{
 session.user={id:'test'};let retry;
 globalThis.fetch=async(path,options={})=>{
  if(path.endsWith('/limits'))return response({limits:{asset_bytes:20*1048576,account_bytes:200*1048576}});
  if(path.endsWith('/retry')){retry=JSON.parse(options.body);return response({status:'queued'});}
  return response({jobs:[{job_id:'job-synthetic',status:'failed',result:{errors:[{retryable:true,message:'synthetic'}]}}]});
 };
 const ui=createMaterialsUI({escapeHtml:String,render(){},changed:async()=>{},isDemo:()=>false,isVisible:()=>false});t.after(()=>ui.reset());
 await ui.load();await ui.click({dataset:{materialAction:'retry',job:'job-synthetic'}});
 assert.deepEqual(Object.keys(retry),['retry_id']);assert.match(retry.retry_id,/\S/);
});

test('a deferred or failed terminal refresh is retried even when the job is unchanged',async t=>{
 session.user={id:'test'};let changed=0;
 globalThis.fetch=async path=>response(path.endsWith('/limits')?{limits:{asset_bytes:20*1048576}}:{jobs:[{job_id:'terminal',status:'succeeded',items:[]}]});
 const ui=createMaterialsUI({escapeHtml:String,render(){},changed:async()=>{changed++;if(changed===1)return null;if(changed===2)throw Error('temporary refresh failure');return true;},isDemo:()=>false,isVisible:()=>false});t.after(()=>ui.reset());
 await ui.load();await ui.load();await ui.load();await ui.load();
 assert.equal(changed,3);
 assert.doesNotMatch(ui.render(),/temporary refresh failure/);
});

test('exhausted file retries provide recovery instructions instead of a broken retry button',async t=>{
 session.user={id:'test'};
 globalThis.fetch=async path=>response(path.endsWith('/limits')?{limits:{asset_bytes:20971520}}:{jobs:[{job_id:'exhausted',status:'failed',retry_remaining:0,result:{errors:[{retryable:true,message:'synthetic failure'}]}}]});
 const ui=createMaterialsUI({escapeHtml:String,render(){},changed:async()=>{},isDemo:()=>false,isVisible:()=>false});t.after(()=>ui.reset());
 await ui.load();assert.doesNotMatch(ui.render(),/data-material-action="retry"/);assert.match(ui.render(),/重试次数已用完/);
});

test('refresh cannot race file submission and hide its newly accepted job',async t=>{
 const f=setup(t);await f.ui.load();f.choose();
 const fetch=globalThis.fetch;let release,enter;
 const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>enter=resolve);t.after(release);
 globalThis.fetch=async(path,opts)=>{if(path.endsWith('/assets')){enter();await gate;}return fetch(path,opts);};
 const upload=f.ui.click({dataset:{materialAction:'upload'}});await entered;
 const reads=()=>f.calls.filter(c=>c.path.endsWith('/imports')&&!c.body).length;
 const before=reads();await f.ui.click({dataset:{materialAction:'refresh'}});assert.equal(reads(),before);
 release();await upload;assert.match(f.ui.render(),/排队中/);assert.equal(reads(),before+1);
});

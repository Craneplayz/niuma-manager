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

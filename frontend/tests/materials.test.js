import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();
const {buildMaterialRequest}=await import('../src/quick-import-data.js');
const receipt={contract_version:'manual-materials/0.1',file_name:'合成通知.png',file_ref:'asset:one',mime_type:'image/png',size_bytes:3,sha256:'a'.repeat(64)};
test('unsupported file metadata remains an explicit unsupported record',()=>{
 const r=buildMaterialRequest([{...receipt,mime_type:'application/zip'}],'request:one','2026-10-14T00:00:00Z');
 assert.equal(r.records[0].attachments[0].status,'unsupported');assert.equal(r.records[0].attachments[0].file_ref,null);
});

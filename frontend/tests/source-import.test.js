import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseSourceFile,readManualTextFile} from '../src/source-import.js';

const request=id=>({contract_version:'0.2',request_id:id,records:[{input_id:id+':1',text:'课程通知',platform:'teaching_web'}]});
const textFile=(text,name='export.txt')=>{const bytes=new TextEncoder().encode(text);return {name,size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer};};

test('manual text files retain chosen text locally without transmitting it',async()=>{
 const before=globalThis.fetch;globalThis.fetch=()=>assert.fail('preview must not upload');
 try{assert.equal(await readManualTextFile(textFile('\uFEFF自行导出的文字\n截止周五')),'自行导出的文字\n截止周五');}
 finally{globalThis.fetch=before;}
});

test('manual text preview accepts JSON; attachments use their separate upload entry',async()=>{
 assert.equal(await readManualTextFile(textFile('{"text":"未加密文字"}','messages.json')),'{"text":"未加密文字"}');
 for(const name of ['chat.db','picture.png'])await assert.rejects(readManualTextFile(textFile('文字',name)),/JSON/);
 await assert.rejects(readManualTextFile(textFile('{"encrypted":true,"data":"密文"}','messages.json')),/加密/);
 await assert.rejects(readManualTextFile(textFile('-----BEGIN PGP MESSAGE-----\nciphertext')),/加密/);
 await assert.rejects(readManualTextFile(textFile(' ')),/1–4000/);
 await assert.rejects(readManualTextFile(textFile('a'.repeat(4001))),/1–4000/);
 await assert.rejects(readManualTextFile({name:'export.txt',size:1,arrayBuffer:async()=>new Uint8Array([255]).buffer}),/UTF-8/);
 await assert.rejects(readManualTextFile({name:'export.txt',size:16001,arrayBuffer:()=>assert.fail('oversized file read')}),/4000/);
});

test('crawler JSONL batches remain separate and are not submitted during parsing',()=>{
 const result=parseSourceFile([request('first'),request('second')].map(JSON.stringify).join('\n'));
 assert.equal(result.kind,'requests');
 assert.deepEqual(result.requests.map(item=>item.request_id),['first','second']);
 assert.equal(result.sent.size,0);
});

test('arbitrary unencrypted JSON is previewable while malformed JSON is reported',()=>{
 assert.equal(parseSourceFile(JSON.stringify({contract_version:'0.1',records:[]})).kind,'text');
 assert.equal(parseSourceFile(JSON.stringify({contract_version:'0.2',request_id:'x',records:[]})).kind,'text');
 assert.throws(()=>parseSourceFile('not json'),/JSON/);
});

test('published source-to-text contract sample can be previewed as one batch',()=>{
 const raw=readFileSync(new URL('../../contracts/source-to-text/text-frontend-v02-request.json',import.meta.url),'utf8');
 const result=parseSourceFile(raw);
 assert.equal(result.kind,'requests');
 assert.equal(result.requests.length,1);
 assert.equal(result.requests[0].records.length,2);
});

test('deployment accepts self-exported JSON without enabling automatic WeChat access',async()=>{
 const source=readFileSync(new URL('../src/source-import.js',import.meta.url),'utf8')
  .replace("from './id.js'",`from '${new URL('../src/id.js',import.meta.url).href}'`)
  .replace("import.meta.env?.VITE_ENABLE_WECHAT","'0'");
 const deployed=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
 assert.equal(deployed.wechatEnabled,false);
 assert.equal(deployed.parseSourceFile(JSON.stringify(request('course'))).kind,'requests');
 assert.equal(deployed.parseSourceFile(JSON.stringify({schema_version:1,messages:[{type_code:1,content:'合成消息'}]})).kind,'wechat');
 const batch=request('wechat');batch.records[0].platform='wechat';
 assert.equal(deployed.parseSourceFile(JSON.stringify(batch)).kind,'requests');
 assert.throws(()=>deployed.parseSourceFile(JSON.stringify({schema_version:1,encrypted:true,messages:[]})),/加密/);
});

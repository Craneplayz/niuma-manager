import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseSourceFile} from '../src/source-import.js';

const request=id=>({contract_version:'0.2',request_id:id,records:[{input_id:id+':1',text:'课程通知',platform:'teaching_web'}]});

test('crawler JSONL batches remain separate and are not submitted during parsing',()=>{
 const result=parseSourceFile([request('first'),request('second')].map(JSON.stringify).join('\n'));
 assert.equal(result.kind,'requests');
 assert.deepEqual(result.requests.map(item=>item.request_id),['first','second']);
 assert.equal(result.sent.size,0);
});

test('source import rejects unsupported or oversized batches',()=>{
 assert.throws(()=>parseSourceFile(JSON.stringify({contract_version:'0.1',records:[]})),/v0.2/);
 assert.throws(()=>parseSourceFile(JSON.stringify({contract_version:'0.2',request_id:'x',records:[]})),/v0.2/);
 assert.throws(()=>parseSourceFile('not json'),/JSON/);
});

test('published source-to-text contract sample can be previewed as one batch',()=>{
 const raw=readFileSync(new URL('../../contracts/source-to-text/text-frontend-v02-request.json',import.meta.url),'utf8');
 const result=parseSourceFile(raw);
 assert.equal(result.kind,'requests');
 assert.equal(result.requests.length,1);
 assert.equal(result.requests[0].records.length,2);
});

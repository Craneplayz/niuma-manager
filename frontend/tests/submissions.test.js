import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.window=new EventTarget();
const {session}=await import('../src/team-store.js');
const {createSubmissionsUI}=await import('../src/submissions-ui.js');
const sample=JSON.parse(readFileSync(new URL('../../contracts/examples/submission-operations-v01-cases.json',import.meta.url),'utf8')).cases[0];
const response=value=>({ok:true,json:async()=>structuredClone(value)});

test('submission controls show all actions and use current snapshot/revision and stable retry identity',async t=>{
 session.user={id:'synthetic'};let lost=true,changed=0;const writes=[];
 globalThis.fetch=async(path,opts={})=>{
  if(opts.body){writes.push(JSON.parse(opts.body));if(lost){lost=false;throw Error('lost');}return response({});}
  if(path.endsWith('/credentials'))return response({source_account_id:'source:teaching'});
  if(path.includes('?'))return response({records:[sample.record],next_cursor:null});
  if(path.endsWith('/source'))return response({records:[{text:'整份合成原文',text_quality:'complete',source_url:null,upstream_hints:{references:[]}}]});
  return response(sample.record);
 };
 const ui=createSubmissionsUI({escapeHtml:s=>String(s??''),render(){},isDemo:()=>false,refresh:async()=>changed++});t.after(()=>ui.reset());
 await ui.click({dataset:{submissionAction:'load'}});
 await ui.click({dataset:{submissionAction:'detail',record:sample.record.record_id}});
 assert.match(ui.render(),/提交第二次作业/);assert.match(ui.render(),/准备下次讨论/);assert.match(ui.render(),/整份合成原文/);
 ui.input({target:{dataset:{submissionField:'scope'},value:'mixed'}});
 ui.input({target:{dataset:{submissionField:'task:submit'},value:'assignment_submission'}});
 await ui.click({dataset:{submissionAction:'associate_actions'}});
 await ui.click({dataset:{submissionAction:'associate_actions'}});
 assert.deepEqual(writes[0],writes[1]);assert.equal(changed,1);
 const {operation_id,...body}=writes[0],{operation_id:expected,...wanted}=sample.operation;
 assert.deepEqual(body,wanted);assert.ok(operation_id);ui.reset();assert.doesNotMatch(ui.render(),/第二次作业/);
});

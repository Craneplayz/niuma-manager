import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.window=new EventTarget();
const {session}=await import('../src/team-store.js');
const {createSubmissionsUI}=await import('../src/submissions-ui.js');
const sample=JSON.parse(readFileSync(new URL('../../contracts/examples/submission-operations-v01-cases.json',import.meta.url),'utf8')).cases[0];
const response=value=>({ok:true,json:async()=>structuredClone(value)});

test('submission controls show all actions and use current snapshot/revision and stable retry identity',async t=>{
 session.user={id:'synthetic'};let lost=true,changed=0,dialog='';const writes=[];
 globalThis.fetch=async(path,opts={})=>{
  if(opts.body){writes.push(JSON.parse(opts.body));if(lost){lost=false;throw Error('lost');}return response({});}
  if(path.endsWith('/credentials'))return response({source_account_id:'source:teaching'});
  if(path.includes('?'))return response({records:[Object.fromEntries(['record_id','label','course_or_project'].map(key=>[key,sample.record[key]]))],next_cursor:null});
  if(path.endsWith('/source'))return response({records:[{text:'整份合成原文',text_quality:'complete',source_url:null,upstream_hints:{references:[]}}]});
  return response(sample.record);
 };
 const ui=createSubmissionsUI({escapeHtml:s=>String(s??''),render(){},isDemo:()=>false,openDialog:html=>{dialog=html;},closeDialog(){dialog="";},refresh:async()=>changed++});t.after(()=>ui.reset());
 await ui.click({dataset:{submissionAction:'load'}});
 assert.match(ui.render(),/第二次作业及课堂准备/);
 await ui.click({dataset:{submissionAction:'detail',record:sample.record.record_id}});
 assert.match(dialog,/提交第二次作业/);assert.match(dialog,/准备下次讨论/);assert.match(dialog,/整份合成原文/);
 ui.input({target:{dataset:{submissionField:'scope'},value:'mixed'}});
 ui.input({target:{dataset:{submissionField:'task:submit'},value:'assignment_submission'}});
 await ui.click({dataset:{submissionAction:'associate_actions'}});
 await ui.click({dataset:{submissionAction:'associate_actions'}});
 assert.deepEqual(writes[0],writes[1]);assert.equal(changed,1);
 const {operation_id,...body}=writes[0],{operation_id:expected,...wanted}=sample.operation;
 assert.deepEqual(body,wanted);assert.ok(operation_id);ui.reset();assert.doesNotMatch(ui.render(),/第二次作业/);
});

test('a delayed detail read cannot reopen a dismissed dialog',async t=>{
 session.user={id:'synthetic'};let release,opens=0;
 const gate=new Promise(resolve=>release=resolve);t.after(release);
 globalThis.fetch=async path=>{await gate;return response(path.endsWith('/source')?{records:[]}:sample.record);};
 const ui=createSubmissionsUI({escapeHtml:String,render(){},isDemo:()=>false,openDialog(){opens++;},closeDialog(){},refresh:async()=>{}});t.after(()=>ui.reset());
 const reading=ui.click({dataset:{submissionAction:'detail',record:sample.record.record_id}});
 await new Promise(resolve=>setImmediate(resolve));
 await ui.click({dataset:{submissionAction:'close'}});
 release();await reading;
 assert.equal(opens,1);
});

test('switching accounts stops pagination before reusing the previous source identity',async t=>{
 session.user={id:'synthetic'};let release;const paths=[];
 const gate=new Promise(resolve=>release=resolve);t.after(release);
 globalThis.fetch=async path=>{
  paths.push(path);
  if(path.endsWith('/credentials'))return response({source_account_id:'previous-account'});
  await gate;return response({records:[sample.record],next_cursor:'private-cursor'});
 };
 const ui=createSubmissionsUI({escapeHtml:String,render(){},isDemo:()=>false,openDialog(){},closeDialog(){},refresh:async()=>{}});t.after(()=>ui.reset());
 const reading=ui.click({dataset:{submissionAction:'load'}});await new Promise(resolve=>setImmediate(resolve));
 ui.reset();session.user={id:'different'};release();await reading;
 assert.equal(paths.length,2);assert.ok(paths.every(path=>!path.includes('private-cursor')));
 assert.doesNotMatch(ui.render(),/第二次作业/);
});

test('a failed directory read waits for an explicit retry instead of a toggle loop',async t=>{
 session.user={id:'synthetic'};let reads=0;
 globalThis.fetch=async()=>{reads++;return response({});};
 const ui=createSubmissionsUI({escapeHtml:String,render(){},isDemo:()=>false,openDialog(){},closeDialog(){},refresh:async()=>{}});t.after(()=>ui.reset());
 const event={target:{dataset:{submissionDirectory:''},open:true}};
 ui.toggle(event);await new Promise(resolve=>setImmediate(resolve));
 ui.toggle(event);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(reads,1);assert.match(ui.render(),/请先同步教学网/);
 await ui.click({dataset:{submissionAction:'load'}});assert.equal(reads,2);
});

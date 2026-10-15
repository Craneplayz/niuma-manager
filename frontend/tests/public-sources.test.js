import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.window=new EventTarget();
const {session}=await import('../src/team-store.js');
const {createPublicSourcesUI}=await import('../src/public-sources-ui.js');
const examples=JSON.parse(readFileSync(new URL('../../contracts/examples/public-import-v01.json',import.meta.url),'utf8'));
const response=value=>({ok:true,json:async()=>structuredClone(value)});

test('public preview needs an explicit selection and imports notices with task version',async t=>{
 session.user={id:'synthetic'};const calls=[];let changed=0;
 globalThis.fetch=async(path,opts={})=>{
  const body=opts.body?JSON.parse(opts.body):undefined;calls.push({path,body});
  if(path.endsWith('/catalog'))return response(examples.catalog);
  if(path.endsWith('/runs'))return response(examples.runs);
  if(path.endsWith('/import'))return response(examples.imported);
  throw Error(path);
 };
 const ui=createPublicSourcesUI({escapeHtml:s=>String(s??''),render(){},isDemo:()=>false,isVisible:()=>false,refresh:async()=>changed++});t.after(()=>ui.reset());
 await ui.load();assert.match(ui.render(),/合成公开讲座/);
 await ui.click({dataset:{publicAction:'import',run:examples.run.run_id}});
 assert.equal(calls.some(c=>c.body),false);
 ui.input({target:{dataset:{publicEvent:'evt_synthetic',run:examples.run.run_id},checked:true}});
 await ui.click({dataset:{publicAction:'import',run:examples.run.run_id}});
 assert.deepEqual(calls.at(-1).body,examples.import);assert.equal(changed,1);
 assert.match(ui.render(),/已加入 1 条通知/);
});

test('public start retries reuse identity; resetting account discards late results',async t=>{
 session.user={id:'synthetic'};let lost=true;const posts=[],releases=[];
 globalThis.fetch=async(path,opts={})=>{
  if(opts.body){posts.push(JSON.parse(opts.body));if(lost){lost=false;throw Error('lost');}return response({...examples.run,state:'running',result:null});}
  return await new Promise(r=>releases.push(()=>r(response(path.endsWith('/catalog')?examples.catalog:examples.runs))));
 };
 const ui=createPublicSourcesUI({escapeHtml:s=>String(s??''),render(){},isDemo:()=>false,isVisible:()=>false,refresh:async()=>{}});t.after(()=>ui.reset());
 await ui.click({dataset:{publicAction:'start'}});await ui.click({dataset:{publicAction:'start'}});
 assert.deepEqual(posts[0],posts[1]);
 const reading=ui.load();await new Promise(r=>setImmediate(r));ui.reset();session.user=null;
 releases.forEach(release=>release());await reading;
 assert.doesNotMatch(ui.render(),/合成公开讲座/);
});

test('an older public directory read cannot erase a newly started collection',async t=>{
 session.user={id:'synthetic'};let release;
 const pending=new Promise(resolve=>release=resolve);
 globalThis.fetch=async(path,opts={})=>{
  if(opts.body)return response({...examples.run,run_id:'new-run',state:'running',result:null,error:null});
  if(path.endsWith('/catalog'))return response(examples.catalog);
  await pending;return response({runs:[]});
 };
 const ui=createPublicSourcesUI({escapeHtml:String,render(){},isDemo:()=>false,isVisible:()=>false,refresh:async()=>{}});t.after(()=>{release();ui.reset();});
 const reading=ui.load();await ui.click({dataset:{publicAction:'start'}});
 release();await reading;
 assert.match(ui.render(),/后台采集中/);
 assert.match(ui.render(),/data-public-action="start" disabled/);
});

test('a task version conflict refreshes the workspace and waits for explicit reconfirmation',async t=>{
 session.user={id:'synthetic'};let refreshed=0,writes=0;
 globalThis.fetch=async(path,opts={})=>{
  if(path.endsWith('/catalog'))return response(examples.catalog);
  if(path.endsWith('/runs'))return response(examples.runs);
  writes++;return {ok:false,status:409,json:async()=>({error:'任务已变化，请刷新后重试'})};
 };
 const ui=createPublicSourcesUI({escapeHtml:String,render(){},isDemo:()=>false,isVisible:()=>false,refresh:async()=>refreshed++});t.after(()=>ui.reset());
 await ui.load();ui.input({target:{dataset:{publicEvent:'evt_synthetic',run:examples.run.run_id},checked:true}});
 await ui.click({dataset:{publicAction:'import',run:examples.run.run_id}});
 assert.equal(refreshed,1);assert.equal(writes,1);
 assert.match(ui.render(),/重新确认/);
});

test('polling during a pending public start cannot capture a pre-start directory',async t=>{
 session.user={id:'synthetic'};let reads=0,release,enter;
 const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>enter=resolve);t.after(release);
 globalThis.fetch=async(path,opts={})=>{
  if(opts.body){enter();await gate;return response({...examples.run,state:'running',result:null,error:null});}
  reads++;return response(path.endsWith('/catalog')?examples.catalog:{runs:[]});
 };
 const ui=createPublicSourcesUI({escapeHtml:String,render(){},isDemo:()=>false,isVisible:()=>false,refresh:async()=>{}});t.after(()=>ui.reset());
 await ui.load();const start=ui.click({dataset:{publicAction:'start'}});await entered;
 const before=reads;await ui.load();assert.equal(reads,before);
 release();await start;assert.match(ui.render(),/后台采集中/);
});

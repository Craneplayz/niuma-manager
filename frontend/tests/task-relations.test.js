import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();
const {session}=await import('../src/team-store.js');
const {createTaskRelationsUI}=await import('../src/task-relations-ui.js');
const response=value=>({ok:true,json:async()=>structuredClone(value)});
const sample={jobs:[{id:'job',state:'succeeded'}],proposals:[{id:'p',state:'review',before:[{title:'原报告',description:'原说明'}],result:{result:{title:'整合报告<script>',description:'甲乙一并提交',course:'实践',due:{date:'2026-11-08',time:'18:00'}},reason:'同一交付',evidence:[{quote:'原文<script>'}],completion:[{}]}}]};
const escape=s=>String(s??'').replaceAll('<','&lt;').replaceAll('>','&gt;');

test('relation preview, explicit apply, undo and source scope are visible',async t=>{
 session.user={id:'test'};const writes=[];let refreshed=0;
 globalThis.fetch=async(path,opts={})=>{if(opts.body){writes.push([path,JSON.parse(opts.body)]);return response({});}return response(sample);};
 const ui=createTaskRelationsUI({escapeHtml:escape,render(){},isDemo:()=>false,changed:async()=>refreshed++});t.after(()=>ui.reset());
 await ui.load();assert.match(ui.render(),/1 项待核对/);assert.match(ui.render(),/原报告/);assert.match(ui.render(),/2026-11-08 18:00/);
 assert.equal(refreshed,1,'already finished jobs refresh workspace on first load too');
 assert.doesNotMatch(ui.render(),/<script>/);assert.match(ui.render(),/当前账号/);
 await ui.click({dataset:{relationAction:'apply',relationId:'p'}});
 assert.deepEqual(writes[0],['/api/recognition/relations/p/apply',{reviewed:true}]);assert.equal(refreshed,2);
 await ui.click({dataset:{relationAction:'start'}});assert.deepEqual(writes[1],['/api/recognition/relations',{consent:true}]);
 ui.reset();assert.doesNotMatch(ui.render(),/整合报告/);
});

test('account reset drops late results and no mutation follows reset during flush',async t=>{
 session.user={id:'test'};let release;
 globalThis.fetch=async()=>new Promise(resolve=>{release=()=>resolve(response(sample));});
 const ui=createTaskRelationsUI({escapeHtml:escape,render(){},isDemo:()=>false,changed:async()=>{}});t.after(()=>ui.reset());
 const pending=ui.load();ui.reset();release();await pending;assert.doesNotMatch(ui.render(),/整合报告/);
});

test('model failure keeps a retry action without disabling other import panels',async t=>{
 session.user={id:'test'};
 globalThis.fetch=async()=>response({jobs:[{id:'j',state:'failed',error:'已保存任务保留'}],proposals:[]});
 const ui=createTaskRelationsUI({escapeHtml:escape,render(){},isDemo:()=>false,changed:async()=>{}});t.after(()=>ui.reset());
 await ui.load();assert.match(ui.render(),/已保存任务保留/);assert.match(ui.render(),/重新分析已保存内容/);assert.doesNotMatch(ui.render(),/disabled/);
});

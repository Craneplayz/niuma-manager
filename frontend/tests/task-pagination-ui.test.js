import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';

test('task center paginates real event paths, resets filters and preserves cross-page bulk selection',async()=>{
 const dom=new JSDOM('<div id="app"></div><div id="toast"></div><dialog id="dialog"></dialog>',{url:'http://localhost/#tasks'}),win=dom.window;
 const names=['window','document','location','history','MutationObserver','CustomEvent','matchMedia','setInterval','scrollY','innerHeight','requestAnimationFrame','cancelAnimationFrame','fetch'];
 const previous=Object.fromEntries(names.map(name=>[name,globalThis[name]]));
 Object.assign(globalThis,{window:win,document:win.document,location:win.location,history:win.history,MutationObserver:win.MutationObserver,CustomEvent:win.CustomEvent,
  matchMedia:()=>({matches:true,addEventListener(){}}),setInterval:()=>0,scrollY:0,innerHeight:800,requestAnimationFrame:()=>0,cancelAnimationFrame(){}});
 win.scrollTo=()=>{};win.HTMLElement.prototype.scrollIntoView=()=>{};win.HTMLDialogElement.prototype.close=function(){this.open=false;};
 const tasks=Array.from({length:23},(_,i)=>({id:`task-${i}`,title:`任务 ${String(i).padStart(2,'0')}`,course:i%2?'课程甲':'课程乙',due:{date:`2026-10-${String(i+1).padStart(2,'0')}`,precision:'date'},priority:i%2?'high':'low',status:i<2?'completed':'not_started',subtasks:[],plans:[]}));
 globalThis.fetch=async path=>{
  assert.ok(['/api/auth/me','/api/workspace'].includes(path),'pagination must not save account data');
  return {ok:true,json:async()=>path==='/api/auth/me'?{user:{id:'pagination-fixture',name:'测试'}}:{tasks:{version:1,value:tasks},courses:{version:1,value:[]},materials:{version:1,value:[]}}};
 };
 const click=selector=>{const node=win.document.querySelector(selector);assert.ok(node,selector);node.click();};
 const page=()=>win.document.querySelector('.task-page-position')?.textContent;
 const count=()=>win.document.querySelectorAll('[data-motion-task]').length;
 try{
  await import('../src/app.js');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(count(),10);assert.equal(page(),'第 1 / 3 页');
  click('[data-task-page="2"]');assert.equal(count(),10);assert.equal(page(),'第 2 / 3 页');
  click('[data-task-page="3"]');assert.equal(count(),3);
  const search=win.document.querySelector('#search');search.value='课程乙';search.dispatchEvent(new win.Event('input',{bubbles:true}));
  assert.equal(page(),'第 1 / 2 页');assert.equal(count(),10);assert.equal(win.document.querySelector('.task-page-range').textContent,'第 1–10 项 · 共 12 项');
  click('[data-task-page="2"]');click('[data-filter="todo"]');assert.equal(page(),'第 1 / 2 页');
  click('[data-task-page="2"]');const sort=win.document.querySelector('#task-sort');sort.value='priority';sort.dispatchEvent(new win.Event('change',{bubbles:true}));assert.equal(page(),'第 1 / 2 页');
  search.value='';win.document.querySelector('#search').value='';win.document.querySelector('#search').dispatchEvent(new win.Event('input',{bubbles:true}));click('[data-filter="all"]');
  click('[data-action="bulk-toggle"]');click('[data-bulk-action="select-all"]');assert.equal(win.document.querySelector('.task-bulk-count').textContent,'已选择 10 项');
  click('[data-task-page="2"]');assert.equal(win.document.querySelectorAll('.bulk-selected').length,0);
  click('[data-bulk-action="select-all"]');assert.equal(win.document.querySelector('.task-bulk-count').textContent,'已选择 20 项');
  click('[data-task-page="1"]');assert.equal(win.document.querySelectorAll('.bulk-selected').length,10);
  click('[data-bulk-action="select-all"]');assert.equal(win.document.querySelector('.task-bulk-count').textContent,'已选择 10 项');
  win.dispatchEvent(new win.CustomEvent('niuma:account-reset'));assert.equal(count(),0);assert.equal(page(),undefined);
 }finally{dom.window.close();for(const [name,value] of Object.entries(previous))globalThis[name]=value;}
});

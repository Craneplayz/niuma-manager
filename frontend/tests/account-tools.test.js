import test from 'node:test';
import assert from 'node:assert/strict';
import { createAccountTools } from '../../server/environment_tools/account-tools.js';

function fixture(overrides = {}) {
  const dialogs=[], calls=[];
  let reloads=0, closes=0, epoch=0;
  const session={user:{id:'synthetic-alice'}};
  const tools=createAccountTools({session, accountVersion:()=>epoch,
    escapeHtml:value=>String(value).replaceAll('<','&lt;'),
    openDialog:html=>dialogs.push(html), closeDialog:()=>closes++, reload:()=>reloads++,
    flushStore:async()=>{}, api:async(...args)=>{calls.push(args);return {reset:true,pendingFiles:0};}, ...overrides});
  return {tools,session,dialogs,calls,get reloads(){return reloads;},get closes(){return closes;},
    switchAccount(){epoch++;}, click:name=>tools.click({dataset:{accountAction:'test-reset-'+name}})};
}

test('only an authenticated account sees the test tool; cancellation writes nothing',async()=>{
  const f=fixture();assert.match(f.tools.render(),/清空测试数据/);assert.match(f.tools.render(true),/disabled/);
  await f.click('open');assert.match(f.dialogs.at(-1),/无法撤销/);
  await f.click('cancel');assert.equal(f.calls.length,0);assert.equal(f.closes,1);
  f.session.user=null;assert.equal(f.tools.render(),'');
});

test('confirmation drains saves, binds the expected account, and reloads after success',async()=>{
  let drained=false;
  const f=fixture({flushStore:async()=>{drained=true;}});
  await f.click('confirm');assert.equal(f.calls.length,0);
  await f.click('open');await f.click('confirm');
  assert.equal(drained,true);assert.equal(f.calls.length,1);
  assert.deepEqual(f.calls[0],['/api/environment-tools/reset-account',{confirm:true,accountId:'synthetic-alice'},'POST',{timeoutMs:360000}]);
  assert.equal(f.reloads,1);
});

test('an account change after opening requires a fresh confirmation',async()=>{
  const f=fixture();await f.click('open');f.switchAccount();await f.click('confirm');
  assert.equal(f.calls.length,0);assert.equal(f.closes,1);
});

test('failed saves drain before the confirmed reset discards local edits',async()=>{
  const f=fixture({flushStore:async()=>{throw new Error('尚有未保存内容');}});
  await f.click('open');await f.click('confirm');
  assert.equal(f.calls.length,1);assert.equal(f.reloads,1);
});

test('duplicate clicks are ignored and changed accounts ignore late responses',async()=>{
  let release,started;
  const entered=new Promise(resolve=>{started=resolve;});
  const f=fixture({api:()=>{started();return new Promise(resolve=>{release=resolve;});}});
  await f.click('open');const running=f.click('confirm');await entered;
  await f.click('confirm');f.switchAccount();release({reset:true});await running;
  assert.equal(f.reloads,0);
});

test('failed file cleanup offers continuation without another destructive request',async()=>{
  const f=fixture({api:async()=>({reset:true,pendingFiles:1})});
  await f.click('open');await f.click('confirm');
  assert.match(f.dialogs.at(-1),/刷新并继续/);assert.doesNotMatch(f.dialogs.at(-1),/确认清空/);
  await f.click('reload');assert.equal(f.reloads,1);
});

test('a reset request failure is escaped and keeps the confirmation available',async()=>{
  const f=fixture({api:async()=>{throw new Error('<reset failed>');}});
  await f.click('open');await f.click('confirm');
  assert.equal(f.reloads,0);assert.match(f.dialogs.at(-1),/&lt;reset failed>/);
  assert.match(f.dialogs.at(-1),/确认清空/);
});

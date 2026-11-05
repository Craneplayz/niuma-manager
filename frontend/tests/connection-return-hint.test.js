import test from 'node:test';
import assert from 'node:assert/strict';
import { createConnectionReturnHint } from '../src/connection-return-hint.js';

function fixture() {
  let epoch=1, active=true, dialogOpen=false;
  const pending=[], notifications=[], opens=[];
  const hint=createConnectionReturnHint({
    getAccountVersion:()=>epoch,isActiveAccount:()=>active,isDialogOpen:()=>dialogOpen,
    schedule:fn=>pending.push(fn),notify:(...args)=>notifications.push(args),openAccount:section=>opens.push(section),
  });
  return {hint,notifications,opens,flush(){while(pending.length)pending.shift()();},switchAccount(){epoch++;},logout(){active=false;},openOtherDialog(){dialogOpen=true;}};
}

test('dismissing PKU settings offers an optional focused return to account settings',()=>{
  const f=fixture();f.hint.closed('pku-email');assert.equal(f.notifications.length,0);f.flush();
  assert.equal(f.notifications.length,1);assert.equal(f.opens.length,0,'closing never automatically reopens the account panel');
  const [message,action,options]=f.notifications[0];assert.equal(message,'连接设置已关闭');assert.equal(options.actionLabel,'返回账号设置');assert.equal(options.durationMs,10000);
  action();assert.deepEqual(f.opens,['pku-email']);
});

test('save and removal confirmations keep their message and gain the return action',()=>{
  for(const message of ['邮箱设置已保存','北大邮箱已连接','邮箱连接已移除','教学网已连接']){
    const f=fixture();f.hint.closed('pku-email');f.hint.recordToast(message);f.flush();
    assert.equal(f.notifications.length,1);assert.equal(f.notifications[0][0],message);assert.equal(f.notifications[0][2].actionLabel,'返回账号设置');
  }
});

test('teaching-web connection dialogs return to the account overview',()=>{
  const f=fixture();f.hint.closed('teaching');f.flush();f.notifications[0][1]();assert.deepEqual(f.opens,[undefined]);
});

test('late hints do not survive account changes, logout, or another open popup',()=>{
  for(const change of ['switchAccount','logout','openOtherDialog']){
    const f=fixture();f.hint.closed('pku-email');f[change]();f.flush();assert.equal(f.notifications.length,0);
  }
  const f=fixture();f.hint.closed('unrelated');f.flush();assert.equal(f.notifications.length,0);
});

test('an old return action cannot open account settings for a different session',()=>{
  const f=fixture();f.hint.closed('pku-email');f.flush();const action=f.notifications[0][1];f.switchAccount();action();assert.equal(f.opens.length,0);
});

test('existing actionable notifications take precedence over a close hint',()=>{
  const f=fixture();f.hint.closed('pku-email');f.hint.recordToast('已移入回收站',()=>{});f.flush();assert.equal(f.notifications.length,0);
});

test('only the most recent connection dismissal can publish a queued hint',()=>{
  const f=fixture();f.hint.closed('pku-email');f.hint.closed('teaching');f.flush();assert.equal(f.notifications.length,1);f.notifications[0][1]();assert.deepEqual(f.opens,[undefined]);
});

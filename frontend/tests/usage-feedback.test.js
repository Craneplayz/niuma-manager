import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
globalThis.window=new EventTarget();globalThis.document=new EventTarget();
globalThis.CustomEvent=class extends Event{constructor(name){super(name);}};
document.querySelector=()=>null;
const {session,initSession,authenticate}=await import('../src/team-store.js');
const {feedbackBox,usageVisit}=await import('../src/usage-feedback.js');
test('feedback box is voluntary, login-only, and has no dashboard link',()=>{
 session.user=null;assert.equal(feedbackBox(),'');
 session.user={id:'synthetic'};const html=feedbackBox();assert.match(html,/usage-feedback-form/);assert.doesNotMatch(html,/admin|使用数据|评分/);
});
test('visit is claimed once per account and uses no task/account contents',async()=>{
 const calls=[];globalThis.fetch=async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return {ok:true,json:async()=>({offerRating:false})};};
 session.user={id:'visit-synthetic'};await usageVisit();await usageVisit();
 assert.deepEqual(calls,[['/api/usage/visit',{}]]);
});
test('separate admin entry does not import ordinary task UI or store',async()=>{
 const admin=await readFile(new URL('../src/admin-usage.js',import.meta.url),'utf8');
 const app=await readFile(new URL('../src/app.js',import.meta.url),'utf8');
 assert.doesNotMatch(admin,/team-store|task\.title|email.*data\./);
 assert.doesNotMatch(app,/admin\.html|admin-usage/);
 assert.match(admin,/escape\(f\.message/);
});
test('an open tab records the next Beijing day without requiring reload',async()=>{
 const RealDate=globalThis.Date;let clock='2026-10-14T15:59:00Z',count=0;
 globalThis.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[clock]));}};
 globalThis.fetch=async()=>{count++;return {ok:true,json:async()=>({offerRating:false})};};
 session.user={id:'midnight-synthetic'};
 try{await usageVisit();await usageVisit();clock='2026-10-14T16:01:00Z';await usageVisit();assert.equal(count,2);}
 finally{globalThis.Date=RealDate;}
});
test('restoring a session and logging in record read-only visits without clicking',async()=>{
 const calls=[];let uid='read-only-restore';
 globalThis.fetch=async url=>{calls.push(url);return {ok:true,json:async()=>url==='/api/auth/me'?{user:{id:uid,name:'Synthetic'}}:url==='/api/usage/visit'?{offerRating:false}:{}};};
 await initSession();await Promise.resolve();
 assert.equal(calls.filter(url=>url==='/api/usage/visit').length,1);
 uid='read-only-login';await authenticate('login',{email:'synthetic@example.test',password:'Synthetic-only'});await Promise.resolve();
 assert.equal(calls.filter(url=>url==='/api/usage/visit').length,2);
});
test('visible restoration and keyboard-only use claim a single daily visit',async()=>{
 let count=0;session.user={id:'keyboard-only'};
 globalThis.fetch=async()=>{count++;return {ok:true,json:async()=>({offerRating:false})};};
 document.visibilityState='hidden';await usageVisit();assert.equal(count,0);
 document.visibilityState='visible';document.dispatchEvent(new Event('visibilitychange'));
 window.dispatchEvent(new Event('pageshow'));window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('keydown'));
 await Promise.resolve();assert.equal(count,1);
 session.user={id:'only-keyboard-next'};document.dispatchEvent(new Event('keydown'));
 await Promise.resolve();assert.equal(count,2);delete document.visibilityState;
});
test('a failed visit does not fail session initialization and can retry',async()=>{
 let count=0;
 globalThis.fetch=async url=>{
  if(url==='/api/usage/visit'){count++;throw new TypeError('synthetic unavailable');}
  return {ok:true,json:async()=>url==='/api/auth/me'?{user:{id:'visit-failure',name:'Synthetic'}}:{}};
 };
 const result=await initSession();assert.deepEqual(result.tasks,[]);await new Promise(setImmediate);
 await usageVisit();assert.equal(count,2);
});

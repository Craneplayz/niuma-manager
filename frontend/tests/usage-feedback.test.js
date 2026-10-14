import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
globalThis.window=new EventTarget();globalThis.document=new EventTarget();
globalThis.CustomEvent=class extends Event{constructor(name){super(name);}};
document.querySelector=()=>null;
const {session}=await import('../src/store.js');
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

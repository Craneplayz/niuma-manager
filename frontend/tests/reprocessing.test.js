import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();globalThis.location={hash:'#sources'};
const {session}=await import('../src/team-store.js');
const ui=await import('../src/reprocessing-ui.js');
const records=[{input_id:'旧/中文 1',label:'课程通知',course_or_project:'数据库',result_status:'success',policy_version:null}];
const directory={accounts:[{source_account_id:'source:a',label:'教学网',records}],next_cursor:null};
const click=(action,run)=>({target:{closest:()=>({dataset:{reprocessAction:action,run}})}});
function reset(){window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'test'};ui.configureReprocessing({render(){}});}
test('saved source and explicit opaque input selection are sent without credentials',async()=>{
 reset();let request;
 globalThis.fetch=async(path,options)=>{if(options.body)request=JSON.parse(options.body);return {ok:true,json:async()=>path.includes('/sources')?directory:{runs:[],next_cursor:null}};};
 await ui.reprocessingClick(click('load'));
 ui.reprocessingChange({target:{dataset:{reprocessIndex:'0'},checked:true}});
 assert.match(ui.reprocessingPanel(),/重新识别所选 1 条/);
 await ui.reprocessingClick(click('start'));
 assert.deepEqual(request.input_ids,['旧/中文 1']);assert.equal(request.source_account_id,'source:a');assert.equal(request.contract_version,'reprocessing/0.1');assert.equal(Object.hasOwn(request,'password'),false);
});
test('stop calls persistent run endpoint rather than only cancelling the browser wait',async()=>{
 reset();let sent;
 globalThis.fetch=async(path,options)=>{if(options.body)sent={path,body:JSON.parse(options.body)};return {ok:true,json:async()=>({runs:[],next_cursor:null})};};
 await ui.reprocessingClick(click('stop','run:one'));
 assert.equal(sent.path,'/api/recognition/reprocessing/runs/run%3Aone/stop');assert.deepEqual(sent.body,{contract_version:'reprocessing/0.1'});
});
test('account reset removes source labels, selected records and prior progress',async()=>{
 reset();globalThis.fetch=async path=>({ok:true,json:async()=>path.includes('/sources')?directory:{runs:[],next_cursor:null}});
 await ui.reprocessingClick(click('load'));assert.match(ui.reprocessingPanel(),/课程通知/);
 window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
 assert.doesNotMatch(ui.reprocessingPanel(),/课程通知|数据库|旧\/中文/);
});
test('directory pages are combined without losing one source records',async()=>{
 reset();globalThis.fetch=async path=>({ok:true,json:async()=>path.includes('/sources')?path.includes('cursor=next')?{accounts:[{...directory.accounts[0],records:[{...records[0],input_id:'second',label:'第二条通知'}]}],next_cursor:null}:{...directory,next_cursor:'next'}:{runs:[],next_cursor:null}});
 await ui.reprocessingClick(click('load'));assert.match(ui.reprocessingPanel(),/课程通知/);assert.match(ui.reprocessingPanel(),/第二条通知/);
});

test('an unavailable API does not trigger repeated requests on each render',async()=>{
 reset();let requests=0;
 ui.configureReprocessing({render(){ui.reprocessingPanel();}});
 globalThis.fetch=async()=>{requests++;throw new Error('offline');};
 await ui.reprocessingClick(click('load'));
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(requests,1);assert.match(ui.reprocessingPanel(),/offline/);
 globalThis.fetch=async path=>{requests++;return {ok:true,json:async()=>path.includes('/sources')?directory:{runs:[],next_cursor:null}};};
 await ui.reprocessingClick(click('load'));
 assert.equal(requests,3);assert.match(ui.reprocessingPanel(),/课程通知/);
});

test('native teaching records show actual policy and explain automatic task creation',async()=>{
 reset();const native=structuredClone(directory);native.accounts[0].records[0].policy_version='teaching-policy-v1:'+'a'.repeat(64);
 globalThis.fetch=async path=>({ok:true,json:async()=>path.includes('/sources')?native:{runs:[],next_cursor:null}});
 await ui.reprocessingClick(click('load'));
 assert.match(ui.reprocessingPanel(),/teaching-policy-v1:/);
 assert.match(ui.reprocessingPanel(),/明确任务直接加入任务清单/);
 assert.match(ui.reprocessingPanel(),/不重新抓取网页或附件/);
});

test('refresh removes a stale selection instead of submitting a vanished source record',async()=>{
 reset();let current=directory,sent=0;
 globalThis.fetch=async(path,options)=>{if(options.body)sent++;return {ok:true,json:async()=>path.includes('/sources')?current:{runs:[],next_cursor:null}};};
 await ui.reprocessingClick(click('load'));
 ui.reprocessingChange({target:{dataset:{reprocessIndex:'0'},checked:true}});
 current={...directory,accounts:[{...directory.accounts[0],records:[]}]};
 await ui.reprocessingClick(click('load'));await ui.reprocessingClick(click('start'));
 assert.equal(sent,0);
});

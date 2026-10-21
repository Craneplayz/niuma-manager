// Synthetic API only; a temporary static Vite server is closed in finally.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const frontend=fileURLToPath(new URL('..',import.meta.url));
const server=await createServer({configFile:false,root:frontend,server:{host:'127.0.0.1',port:0}});
let browser;
const usage={start:'2026-10-01',end:'2026-10-20',environment:'test',activeUsers:7,metrics:[],daily:[],feedback:[],rating:{samples:0,average:null},methodology:'合成使用数据'};
const observedAt=Date.parse('2026-10-20T08:00:00Z')/1000;
const synthetic={environment:'test',revision:'synthetic-189',generatedAt:'2026-10-20T08:00:00Z',start:'2026-10-19T08:00:00Z',end:'2026-10-20T08:00:00Z',retentionDays:14,
 requests:[{operation:'recognition.post',count:20,success:18,failures:2,p50Ms:125,p95Ms:1800,errors:[{code:'agent_timeout',count:2}]},{operation:'auth.login',count:10,success:10,failures:0,p50Ms:80,p95Ms:400,errors:[]}],
 stages:[{stage:'collection',count:12,success:12,failures:0,p50Ms:50,p95Ms:120},{stage:'model_call',count:20,success:18,failures:2,p50Ms:120,p95Ms:1700}],
 queue:{queued:2,running:1,oldestWaitSeconds:12,modelQueued:1,modelRunning:1,modelOldestWaitSeconds:8},runs:{succeeded:12,partial_success:2,failed:1,stopped:1,interrupted:0,no_tasks:3,filtered:2},outcomes:[{code:'no_tasks',count:3}],
 providerUsage:{observedCalls:20,input_tokens:{value:9000,known:18,unknown:2},output_tokens:{value:2100,known:19,unknown:1},total_tokens:{value:10800,known:18,unknown:2}},
 resources:{available:true,observedAt,cpuPercent:12.5,memoryUsedBytes:1024**3,memoryTotalBytes:4*1024**3,diskUsedBytes:10*1024**3,diskTotalBytes:40*1024**3,restarts:0},
 alerts:[{code:'agent_timeout',state:'resolved',firstSeen:observedAt-3600,lastSeen:observedAt-3300,lastNotified:null,deliveryError:false},{code:'database_busy',state:'recovering',firstSeen:observedAt-600,lastSeen:observedAt-10,lastNotified:null,deliveryError:true}],trace:[{at:observedAt,requestId:'synthetic-request-'+'a'.repeat(80),runId:'synthetic-run',stage:'model_call',operation:'recognition.post',result:'failed',code:'agent_timeout',durationMs:1800}],droppedEvents:0,methodology:'合成验收数据；仅统计有限状态码和耗时，不包含正文。'};
try{
 await server.listen();const origin=server.resolvedUrls.local[0];
 browser=await chromium.launch({headless:true,...(process.env.NIUMA_TEST_BROWSER_PATH?{executablePath:process.env.NIUMA_TEST_BROWSER_PATH}:{})});
 for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844],['compact',320,740]]){
  const page=await browser.newPage({viewport:{width,height}}),calls=[],errors=[];
  let response=synthetic,status=200,usageStatus=200,delay=false,release;
  await page.clock.install({time:new Date(observedAt*1000)});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/admin/auth/login'){
    assert.equal(route.request().method(),'POST');usageStatus=200;return route.fulfill({json:{}});
   }
   assert.equal(route.request().method(),'GET');
   if(url.pathname==='/api/admin/usage')return route.fulfill({status:usageStatus,json:usageStatus===200?usage:{error:'会话失效'}});
   assert.equal(url.pathname,'/api/admin/operations');calls.push(url.pathname+url.search);
   const body=response,code=status;
   if(delay){delay=false;await new Promise(resolve=>{release=resolve;});}
   await route.fulfill({status:code,json:body});
  });
  await page.goto(origin+'admin.html');await page.locator('#usage-panel').waitFor();
  assert.equal(calls.length,0);
  await page.getByRole('button',{name:'运行监控',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#operations-status')?.textContent==='最近 24 小时');
  assert.deepEqual(calls,['/api/admin/operations']);
  assert.equal(await page.locator('#usage-panel').isVisible(),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth).map(el=>({tag:el.tagName,class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right})).slice(0,15))));
  assert.equal(await page.locator('.table-wrap').last().evaluate(el=>el.scrollWidth>el.clientWidth),true,JSON.stringify(await page.locator('.table-wrap').last().evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,html:el.innerHTML.slice(-200),parent:el.parentElement.outerHTML.slice(0,100)}))));
  assert.ok((await page.locator('#operations-content').innerText()).includes('2026-10-20 16:00:00'));
  assert.ok(!(await page.locator('#operations-content').innerText()).includes(String(observedAt)));
  assert.ok(await page.getByRole('cell',{name:'登录',exact:true}).isVisible());
  assert.ok(await page.getByRole('cell',{name:'采集总耗时含下载',exact:true}).isVisible());
  assert.ok(await page.getByRole('cell',{name:'已恢复',exact:true}).isVisible());
  assert.ok(await page.getByRole('cell',{name:'待恢复通知',exact:true}).isVisible());
  assert.equal(await page.getByRole('cell',{name:'通知失败，待重试',exact:true}).count(),1);
  // Screenshots are opt-in; contract changes can be tested without replacing review artifacts.
  if(process.env.NIUMA_TEST_SCREENSHOTS==='1'&&name!=='compact')await page.screenshot({path:fileURLToPath(new URL(`../docs/screenshots/2026-10-20-admin-operations-${name}.png`,import.meta.url)),fullPage:true});
  await page.clock.fastForward(181000);
  await page.getByText('采样已过期',{exact:true}).waitFor();
  assert.ok(!(await page.locator('#operations-content').innerText()).includes('12.5%'));
  const form=page.locator('#operations-range');
  response={...synthetic,resources:null};
  await form.locator('[name=start]').fill('2026-10-02');await form.getByRole('button',{name:'查看',exact:true}).click();
  await page.getByText('已按日期范围更新',{exact:true}).waitFor();
  assert.equal(calls.at(-1),'/api/admin/operations?start=2026-10-02&end=2026-10-20');
  assert.ok(await page.getByText('暂无可用采样',{exact:true}).isVisible());
  await page.getByRole('button',{name:'使用数据',exact:true}).click();
  assert.equal(await page.locator('#range [name=start]').inputValue(),'2026-10-02');
  assert.ok(await page.getByText('合成使用数据').isVisible());
  await page.getByRole('button',{name:'运行监控',exact:true}).click();
  const previousCalls=calls.length;
  await page.getByRole('button',{name:'运行监控',exact:true}).click();
  assert.equal(calls.length,previousCalls);
  for(const code of [503,401,403]){
   status=code;response={error:'PRIVATE diagnostic must not display'};
   await page.getByRole('button',{name:'最近 24 小时',exact:true}).click();
   await page.locator('#operations-status[role=alert]').waitFor();
   assert.equal(await page.locator('#operations-content').innerText(),'');
   assert.ok(!(await page.locator('#operations-panel').innerText()).includes('PRIVATE'));
   await page.getByRole('button',{name:'使用数据',exact:true}).click();assert.ok(await page.getByText('合成使用数据').isVisible());
   await page.getByRole('button',{name:'运行监控',exact:true}).click();
   await page.locator('#operations-status[role=alert]').waitFor();
   assert.equal(await page.locator('#operations-login').isVisible(),code===401||code===403);
  }
  status=200;response={trace:[{requestId:'<img src=x onerror="window.injected=true">'}]};
  await page.getByRole('button',{name:'最近 24 小时',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#operations-status')?.textContent==='最近 24 小时');
  assert.equal(await page.locator('#operations-content img').count(),0);
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  // A slower previous range must not overwrite a newer response.
  delay=true;response={revision:'stale-response'};
  await form.getByRole('button',{name:'查看',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#operations-status').textContent.includes('正在加载'));
  response={revision:'latest-response'};await page.getByRole('button',{name:'最近 24 小时',exact:true}).click();
  await page.getByText(/版本：latest-response/).waitFor();
  release();await page.waitForLoadState('networkidle');
  assert.ok(!(await page.locator('#operations-content').innerText()).includes('stale-response'));
  // Session expiry can re-enter the existing admin login flow and mount fresh tabs.
  status=401;usageStatus=401;response={error:'会话失效'};
  await page.getByRole('button',{name:'最近 24 小时',exact:true}).click();
  await page.locator('#operations-login').waitFor();
  await page.locator('#operations-login').click();
  await page.locator('#admin-login').waitFor();
  assert.equal(await page.locator('#operations-panel').count(),0);
  await page.locator('#admin-login [name=email]').fill('synthetic@example.test');
  await page.locator('#admin-login [name=password]').fill('Synthetic-only');
  status=200;response={revision:'after-login',requests:[]};
  await page.locator('#admin-login button').click();await page.locator('#usage-panel').waitFor();
  assert.equal(await page.locator('#operations-panel').isVisible(),false);
  await page.getByRole('button',{name:'运行监控',exact:true}).click();
  await page.getByText(/版本：after-login/).waitFor();
  assert.deepEqual(errors,[]);await page.close();console.log(`${name}: switching, dates, resource gaps, errors, escaping, races and overflow passed`);
 }
}finally{await browser?.close();await server.close();}

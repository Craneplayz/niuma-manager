import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'vite';
const {chromium}=createRequire(import.meta.url)('playwright');
const cache=await mkdtemp(join(tmpdir(),'niuma-eval-browser-'));
const frontend=fileURLToPath(new URL('..',import.meta.url));
const server=await createServer({configFile:false,root:frontend,cacheDir:cache,server:{host:'127.0.0.1',port:0}});
const id='a'.repeat(64);
const rows=['model/a','model/b'].flatMap((model,m)=>['recognition','estimation'].map((module,i)=>({id:`case-${m}-${i}`,model,family:model,module,origin:i?'real_original':'synthetic',scenario:i?'explicit_minutes':'deadline',family_id:'source-'+i,purpose:'合成界面验收夹具',repeat_index:0,applicable:true,executed:true,status:i&&m?'error':'ok',checks:{contract:!(i&&m),date_0:!m},tp:i?0:2,fp:m,fn:0,seconds:2+m,estimated_yuan:.02+m*.01,total_tokens:100+m*20,input:'<img src=x onerror="window.bad=true">',expected:{count:2},output:{text:'<script>window.bad=true</script>'}})));
const report={version:'benchmark-report/1',id,sourceVersion:'SYNTHETIC-UI-TEST',datasetHash:'b'.repeat(64),rows,budget:{limitYuan:100}};
const usage={start:'2026-11-01',end:'2026-11-16',environment:'test',activeUsers:0,metrics:[],daily:[],feedback:[],rating:{samples:0,average:null},methodology:'合成界面验收'};
let browser;
try{
 await server.listen();const origin=server.resolvedUrls.local[0];
 browser=await chromium.launch({headless:true,executablePath:process.env.NIUMA_TEST_BROWSER_PATH,args:['--no-sandbox']});
 for(const[name,width,height]of[['desktop',1440,1050],['mobile',390,844],['compact',320,760]]){
  const page=await browser.newPage({viewport:{width,height},acceptDownloads:true});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{const url=new URL(route.request().url()),path=url.pathname;let value=path==='/api/admin/usage'?usage:path==='/api/admin/evaluations'?{runs:[{id,sourceVersion:'SYNTHETIC-UI-TEST'}]}:path==='/api/admin/evaluations/'+id?report:{};
   if(url.searchParams.has('case'))value={row:rows.find(r=>r.id===url.searchParams.get('case')&&r.model===url.searchParams.get('model'))};return route.fulfill({json:value})});
  await page.goto(origin+'admin.html');await page.locator('#usage-panel').waitFor();
  await page.getByRole('button',{name:'模型评测',exact:true}).click();await page.locator('#eval-results table').first().waitFor();
  assert.equal(await page.locator('#usage-panel').isVisible(),false);assert.equal(await page.locator('#operations-panel').isVisible(),false);
  assert.equal(await page.locator('#evaluations-panel svg').count(),3);
  await page.locator('[data-eval-filter="module"]').selectOption('estimation');
  assert.equal(await page.locator('#evaluations-panel .cards').innerText().then(s=>s.includes('2 / 2')),true);
  await page.locator('[data-eval-filter="origin"]').selectOption('real_original');
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'导出CSV',exact:true}).click();assert.equal((await download).suggestedFilename(),'model-evaluation.csv');
  await page.locator('[data-eval-filter="module"]').selectOption('');await page.locator('[data-eval-filter="origin"]').selectOption('');
  await page.getByText('查看字段与输出',{exact:true}).first().click();await page.locator('details[open] pre').filter({hasText:'<script>'}).waitFor();assert.equal(await page.evaluate(()=>Boolean(window.bad)),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,JSON.stringify(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))));
  const folder=process.env.NIUMA_SCREENSHOTS||cache;await mkdir(folder,{recursive:true});await page.screenshot({path:join(folder,'model-evaluation-'+name+'.png'),fullPage:true});
  await page.getByRole('button',{name:'使用数据',exact:true}).click();assert.equal(await page.locator('#evaluations-panel').isVisible(),false);
  assert.deepEqual(errors,[]);await page.close();console.log(name+' model dashboard filters/export/escaping/navigation/layout passed');
 }
}finally{await browser?.close();await server.close();await rm(cache,{recursive:true,force:true})}

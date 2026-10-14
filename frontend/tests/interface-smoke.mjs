// Real Chromium -> Vite -> HTTP/SQLite; all external crawler/model calls are synthetic.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname,delimiter} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(join(root,'crawler/package.json'));
const {chromium}=require('playwright');
const frontendRequire=createRequire(join(root,'frontend/package.json'));
const vite=join(dirname(frontendRequire.resolve('vite/package.json')),'bin/vite.js');
const temporary=await mkdtemp(join(tmpdir(),'niuma-interface-'));
const ready=join(temporary,'ready.json');
const children=[];let browser,diagnostic='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await fn();if(value)return value;await sleep(150);}throw Error('Timeout: '+diagnostic);}
function launch(command,args,env){const child=spawn(command,args,{cwd:root,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','pipe']});child.stderr.on('data',data=>diagnostic=(diagnostic+data.toString()).slice(-6000));children.push(child);return child;}
try{
 const port=await new Promise(resolvePort=>{const server=createServer();server.listen(0,'127.0.0.1',()=>{const p=server.address().port;server.close(()=>resolvePort(p));});});
 const origin='http://127.0.0.1:'+port;
 launch(process.env.NIUMA_TEST_PYTHON||'python',['server/run_interface_fixture.py','--ready-file',ready],{PYTHONPATH:['.','server'].join(delimiter),PYTHONUTF8:'1',NIUMA_ALLOWED_ORIGINS:origin});
 const info=await until(async()=>{try{return JSON.parse(await readFile(ready,'utf8'));}catch{return null;}});
 launch(process.execPath,[vite,'--host','127.0.0.1','--config',join(root,'frontend/vite.config.ts'),join(root,'frontend')],{NIUMA_PORT:String(info.port),NIUMA_WEB_PORT:String(port)});
 await until(async()=>{try{return (await fetch(origin)).ok;}catch{return false;}});
 browser=await chromium.launch({headless:true,...(process.env.NIUMA_TEST_BROWSER?{channel:process.env.NIUMA_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const get=async path=>{const r=await page.request.get(origin+'/api'+path);assert.equal(r.status(),200,await r.text());return r.json();};
 await page.goto(origin);await page.locator('[data-account-action="open"]').click();
 await page.locator('input[name="email"]').fill(info.email);await page.locator('input[name="password"]').fill(info.password);
 await page.locator('[data-account-form="login"] button[type="submit"]').click();
 await page.locator('[data-page="import"]').click();await page.locator('[data-import-source="teaching"]').click();
 await page.locator('#teaching-sync-consent').check();await page.locator('[data-import-action="sync"]').click();
 let runs=await until(async()=>{const v=await get('/sources/sync-runs');return v.runs[0]?.state==='partial_success'&&v;});
 assert.equal(runs.runs[0].counts.added,1);assert.equal(runs.runs[0].counts.failed,1);
 await page.reload();await page.locator('[data-page="import"]').click();await page.locator('[data-import-source="teaching"]').click();
 await page.locator('[data-source-sync-action="retry"]').first().click();
 await until(async()=>{const v=await get('/sources/sync-runs');return v.runs[0]?.parent_run_id&&v.runs[0].state==='succeeded';});
 assert.equal((await get('/workspace')).tasks.value.length,2);
 await page.locator('[data-sync-schedule-field="enabled"]').check();await page.locator('[data-sync-schedule-field="minutes"]').fill('60');
 await page.locator('[data-sync-schedule-field="consent"]').check();await page.locator('[data-sync-schedule-action="save"]').click();
 await until(async()=>{const s=await get('/sources/sync-schedule');return s.enabled&&s.interval_minutes===60;});
 await page.getByText('教学网提交状态与任务关联',{exact:true}).click();await page.locator('[data-submission-action="load"]').click();
 await page.locator('[data-submission-action="detail"]').first().click();
 await page.locator('[data-submission-field="scope"]').selectOption('submission_only');
 for(const element of await page.locator('select[data-submission-field]:not([data-submission-field="scope"])').all())await element.selectOption('assignment_submission');
 await page.locator('[data-submission-action="associate_actions"]').click();await page.getByText('已保存；下次同步按新的关联处理。',{exact:true}).waitFor();
 await until(()=>page.locator('[data-import-action="sync"]').isEnabled());
 const screenshots=join(root,'frontend/docs/screenshots');await mkdir(screenshots,{recursive:true});
 await page.screenshot({path:join(screenshots,'integration-teaching-desktop.png'),fullPage:true});
 await page.locator('#reprocessing-toggle').click();await page.locator('#reprocessing-load').click();
 await page.locator('[data-reprocess-record]').first().check();await page.locator('#reprocessing-start').click();
 await until(async()=>{const v=await get('/recognition/reprocessing/runs');return v.runs[0]?.state==='succeeded';});
 await page.locator('[data-import-source="files"]').click();await page.locator('#material-files').setInputFiles(info.image);
 await page.locator('#material-consent').check();await page.locator('[data-material-action="upload"]').click();
 await until(async()=>{const v=await get('/materials/imports');return v.jobs[0]?.status==='failed';});
 const retryResponse=page.waitForResponse(r=>r.url().includes('/api/materials/imports/')&&r.url().endsWith('/retry'),{timeout:10000});
 await page.locator('[data-material-action="retry"]').click();
 const retryResult=await retryResponse;assert.equal(retryResult.status(),202,await retryResult.text());
 const job=await until(async()=>{const v=await get('/materials/imports');diagnostic=JSON.stringify(v.jobs[0]);return v.jobs[0]?.status==='succeeded'&&v.jobs[0];});
 assert.equal(job.result.tasks[0].title,'合成图片作业');
 const row=page.locator('.import-results .candidate-row').filter({has:page.getByRole('heading',{name:'合成图片作业',exact:true})});
 await row.locator('[data-import-review]').click();await page.locator('[name="reviewed"]').check();
 await page.locator('[data-import-form="review"] button[type="submit"]').click();
 await until(async()=> (await get('/workspace')).tasks.value.some(t=>t.title==='合成图片作业'));
 await page.locator('[data-import-source="public"]').click();await page.locator('[data-public-action="start"]').click();
 await page.locator('[data-public-event]').first().check();await page.locator('[data-public-action="import"]').click();
 const tasks=await until(async()=>{const t=(await get('/workspace')).tasks.value;return t.some(x=>x.publicEvent)&&t;});
 const notice=tasks.find(t=>t.publicEvent);assert.equal(notice.content_type,'notice');assert.equal(notice.due,null);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(screenshots,'integration-public-mobile.png'),fullPage:true});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile overflow');
 await page.setViewportSize({width:320,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'320px mobile overflow');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:['background sync','reload and targeted recovery','schedule settings','submission association','native reprocessing','multipart upload and file retry','file candidate confirmation','public preview to notice'],tasks:tasks.length,external_services:'synthetic',browser:'Chromium'},null,2));
}catch(error){if(browser){const pages=browser.contexts().flatMap(c=>c.pages());if(pages[0]){diagnostic+='\n'+(await pages[0].locator('body').innerText()).slice(-6000);await pages[0].screenshot({path:join(temporary,'failure.png'),fullPage:true});}}console.error(diagnostic);throw error;}
finally{
 await browser?.close();await writeFile(join(temporary,'ready.stop'),'stop');
 await sleep(1500);for(const child of children)if(child.exitCode===null)child.kill();
 await Promise.all(children.map(child=>child.exitCode!==null?Promise.resolve():new Promise(r=>child.once('exit',r))));
 // Only this invocation's newly-created temporary directory is removed.
 await rm(temporary,{recursive:true,force:true});
}

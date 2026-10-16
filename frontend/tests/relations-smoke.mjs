// Real browser -> Vite -> HTTP/SQLite. Network/model sources are synthetic.
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
const {chromium}=require(process.env.NIUMA_TEST_PLAYWRIGHT||'playwright');
const frontendRequire=createRequire(join(root,'frontend/package.json'));
const vite=join(dirname(frontendRequire.resolve('vite/package.json')),'bin/vite.js');
const temporary=await mkdtemp(join(tmpdir(),'niuma-relations-')),ready=join(temporary,'ready.json');
const children=[];let browser,diagnostic='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){const end=Date.now()+30000;while(Date.now()<end){const v=await fn();if(v)return v;await sleep(100);}throw Error('Timeout '+diagnostic);}
function launch(command,args,env){const child=spawn(command,args,{cwd:root,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','pipe']});child.stderr.on('data',d=>diagnostic=(diagnostic+d.toString()).slice(-3000));children.push(child);}
try{
 const port=await new Promise(r=>{const s=createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});}),origin='http://127.0.0.1:'+port;
 launch(process.env.NIUMA_TEST_PYTHON||'python',['server/run_relations_fixture.py','--ready-file',ready],{PYTHONPATH:['.','server'].join(delimiter),PYTHONUTF8:'1',NIUMA_ALLOWED_ORIGINS:origin});
 const info=await until(async()=>{try{return JSON.parse(await readFile(ready,'utf8'));}catch{return null;}});
 launch(process.execPath,[vite,'--host','127.0.0.1','--config',join(root,'frontend/vite.config.ts'),join(root,'frontend')],{NIUMA_PORT:String(info.port),NIUMA_WEB_PORT:String(port)});
 await until(async()=>{try{return (await fetch(origin)).ok;}catch{return false;}});
 browser=await chromium.launch({headless:true,...(process.env.NIUMA_TEST_BROWSER?{channel:process.env.NIUMA_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1365,height:1050}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const get=async p=>{const r=await page.request.get(origin+'/api'+p);assert.equal(r.status(),200,await r.text());return r.json();};
 await page.goto(origin);await page.locator('[data-account-action="open"]').click();
 await page.locator('input[name="email"]').fill(info.email);await page.locator('input[name="password"]').fill(info.password);
 await page.locator('[data-account-form="login"] button[type="submit"]').click();
 await page.locator('[data-page="import"]').click();await page.locator('[data-import-source="teaching"]').click();
 assert.equal((await get('/workspace')).tasks.value.length,2);
 await page.locator('[data-import-action="sync"]').click();
 await until(async()=>{const r=await get('/recognition/relations');return r.jobs[0]?.state==='succeeded';});
 const tasks=(await get('/workspace')).tasks.value;assert.equal(tasks.length,1);assert.equal(tasks[0].status,'completed');assert.equal(tasks[0].due.date,'2026-11-08');
 await page.getByText('跨来源整合',{exact:true}).click();await page.locator('[data-relation-action="load"]').click();
 await page.getByRole('heading',{name:'提交实验12和13报告',exact:true}).waitFor();
 await page.getByText('查看关联依据与范围',{exact:true}).click();
 await until(()=>page.locator('[data-import-action="sync"]').isEnabled());
 assert.ok((await (await page.request.post(origin+'/api/recognition/task-evidence',{data:{task_id:tasks[0].id}})).json()).evidence.some(e=>e.quotes.some(q=>q.quote.includes('2026-11-08'))));
 const screenshots=join(root,'frontend/docs/screenshots');await mkdir(screenshots,{recursive:true});
 await page.screenshot({path:join(screenshots,'task-relations-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(screenshots,'task-relations-mobile.png'),fullPage:true});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile overflow');
 await page.locator('[data-relation-action="undo"]').click();
 await until(async()=> (await get('/workspace')).tasks.value.length===2);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:['automatic cross-source integration','receiver-only submission completes whole delivery','one workspace task','evidence preview','undo restores two tasks','mobile layout'],external_services:'synthetic'},null,2));
}catch(error){console.error(diagnostic);if(browser){const p=browser.contexts().flatMap(c=>c.pages())[0];if(p)console.error((await p.locator('body').innerText()).slice(-5000));}throw error;}
finally{
 await browser?.close();await writeFile(join(temporary,'ready.stop'),'stop');await sleep(1200);
 for(const c of children)if(c.exitCode===null)c.kill();
 await Promise.all(children.map(c=>c.exitCode!==null?Promise.resolve():new Promise(r=>c.once('exit',r))));
 // Only this script's newly allocated test directory is removed.
 await rm(temporary,{recursive:true,force:true});
}

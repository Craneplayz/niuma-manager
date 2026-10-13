import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

globalThis.window=new EventTarget();
globalThis.location={hash:'#settings'};
const {session}=await import('../src/store.js');
const source=readFileSync(new URL('../src/team-wechat-media-ui.js',import.meta.url),'utf8')
 .replace("from './id.js'",`from '${new URL('../src/id.js',import.meta.url).href}'`)
 .replace("from './store.js'",`from '${new URL('../src/store.js',import.meta.url).href}'`)
 .replace("import {wechatEnabled} from './source-import.js';","const wechatEnabled=false;");
const media=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
media.configureWechatMedia({render(){}});
const response=value=>({ok:true,json:async()=>value});
function reset(){window.dispatchEvent(new Event('niuma:account-reset'));session.user={id:'synthetic'};}
function choose(name){media.manualMaterialInput({target:{id:'manual-material-files',files:[new File(['synthetic'],name)]}});}
function consent(){media.manualMaterialInput({target:{id:'manual-material-consent',checked:true}});}

test('manual mode offers active uploads without collector credentials',async()=>{
 reset();const paths=[];
 globalThis.fetch=async path=>{paths.push(path);return response(path.endsWith('/limits')?{configured:true}:{jobs:[]});};
 await media.load();
 const html=media.wechatMediaPanel();
 assert.match(html,/主动上传材料/);assert.match(html,/未加密/);
 assert.doesNotMatch(html,/生成本机导入凭证|撤销本机导入凭证|data-media-action="issue"/);
 assert.deepEqual(paths.sort(),['/api/materials/imports','/api/materials/limits']);
 assert.equal(await media.wechatMediaClick({dataset:{mediaAction:'issue'}}),false);
});

test('selection does not upload before consent; JSON attachments use the shared unsupported envelope',async()=>{
 reset();let posted;
 const paths=[];
 globalThis.fetch=async(path,options={})=>{
  paths.push(path);
  if(path.endsWith('/assets'))return response({contract_version:'manual-materials/0.1',file_name:'export.json',mime_type:'application/octet-stream',size_bytes:9,sha256:'a'.repeat(64),file_ref:'asset:synthetic-only'});
  if(path.endsWith('/imports')&&options.method==='POST'){posted=JSON.parse(options.body);return response({job_id:'synthetic-job'});}
  return response(path.endsWith('/limits')?{configured:true}:{jobs:[]});
 };
 choose('export.json');
 await media.wechatMediaClick({dataset:{mediaAction:'upload'}});
 assert.equal(paths.length,0);
 consent();await media.wechatMediaClick({dataset:{mediaAction:'upload'}});
 assert.equal(posted.contract_version,'0.3.1');
 const asset=posted.records[0].attachments[0];
 assert.equal(asset.status,'unsupported');assert.equal(asset.file_ref,null);
 assert.equal(asset.errors[0].code,'unsupported_type');
 assert.match(media.wechatMediaPanel(),/已主动提交/);
 assert.ok(paths.every(path=>path.startsWith('/api/materials/')));
});

test('account reset cancels an active upload without starting a model job',async()=>{
 reset();choose('image.png');consent();let started,imports=0;
 const ready=new Promise(resolve=>started=resolve);
 globalThis.fetch=(path,options={})=>{
  if(path.endsWith('/assets'))return new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('cancelled','AbortError')));started();});
  if(options.method==='POST')imports++;
  return Promise.resolve(response(path.endsWith('/limits')?{configured:true}:{jobs:[]}));
 };
 const pending=media.wechatMediaClick({dataset:{mediaAction:'upload'}});
 await ready;window.dispatchEvent(new Event('niuma:account-reset'));session.user=null;
 await pending;assert.equal(imports,0);
 assert.doesNotMatch(media.wechatMediaPanel(),/image\.png|cancelled/);
});

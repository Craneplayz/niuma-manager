import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {newId} from '../src/id.js';

const originalCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto');
const httpCrypto={getRandomValues:bytes=>webcrypto.getRandomValues(bytes)};
function setCrypto(value){Object.defineProperty(globalThis,'crypto',{configurable:true,value});}
function restoreCrypto(){Object.defineProperty(globalThis,'crypto',originalCrypto);}

test('HTTPS retains the native UUID generator and its receiver',()=>{
 const crypto={randomUUID(){assert.equal(this,crypto);return 'native-id';}};
 try{setCrypto(crypto);assert.equal(newId(),'native-id');}
 finally{restoreCrypto();}
});

test('HTTP without randomUUID produces distinct RFC 4122 version 4 IDs',()=>{
 try{
  setCrypto(httpCrypto);
  const ids=Array.from({length:1000},()=>newId());
  for(const id of ids)assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(new Set(ids).size,ids.length);
 }finally{restoreCrypto();}
});

test('the recognition submit reaches its API on HTTP without randomUUID',async()=>{
 globalThis.window=new EventTarget();globalThis.location={hash:'#agent'};
 globalThis.requestAnimationFrame=()=>{};
 const {session}=await import('../src/team-store.js');
 const recognition=await import('../src/team-recognition-ui.js');
 const originalFetch=globalThis.fetch;
 let submitted;
 try{
  setCrypto(httpCrypto);recognition.resetAssistant();session.user={id:'http-test'};
  recognition.configureAssistant({render(){},getState:()=>({tasks:[]})});
  globalThis.fetch=async(path,options)=>{
   if(path==='/api/recognition/imports')return {ok:true,json:async()=>({saved:true})};
   assert.equal(path,'/api/recognition/batches');
   submitted=JSON.parse(options.body);
   return {ok:true,json:async()=>[{request:submitted,demo:false,items:[],result:{status:'no_tasks',record_results:[],errors:[]}}]};
  };
  await recognition.submitRecognitionText('请于2026年10月20日18:00前提交实验报告。');
  assert.match(submitted.request_id,/^[0-9a-f-]{36}$/);
  assert.equal(submitted.records[0].input_id,'manual:'+submitted.request_id);
  assert.match(recognition.assistantPage(),/识别完成/);
 }finally{restoreCrypto();globalThis.fetch=originalFetch;recognition.resetAssistant();session.user=null;}
});

test('all frontend ID consumers use the HTTP-compatible generator',async()=>{
 for(const name of ['app','team-store','source-import','team-recognition-ui','team-wechat-media-ui','reprocessing-ui']){
  const source=await readFile(new URL(`../src/${name}.js`,import.meta.url),'utf8');
  assert.doesNotMatch(source,/crypto(?:\?\.)?\.?(?:randomUUID)/);
  assert.match(source,/import \{newId\} from '\.\/id\.js'/);
 }
});

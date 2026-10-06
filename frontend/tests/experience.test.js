import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.location={hash:'#plan'};
const results={innerHTML:''};
globalThis.document={querySelector:()=>results};
const {configureExperience,experienceInput,experienceClick}=await import('../src/experience.js');

test('clicking an AI search result survives query blur without replacing the pressed button',()=>{
 const originalInterval=globalThis.setInterval;
 let closed=false;
 try{
  globalThis.setInterval=()=>0;
  configureExperience({getState:()=>({tasks:[]}),closeDialog:()=>{closed=true;}});
 }finally{globalThis.setInterval=originalInterval;}
 const target={id:'command-query',value:'ai 拆解',setAttribute(){}};
 experienceInput({type:'input',target});
 assert.match(results.innerHTML,/AI 拆解/);
 const rendered=results.innerHTML;
 results.innerHTML='pressed button remains mounted';
 experienceInput({type:'change',target});
 assert.equal(results.innerHTML,'pressed button remains mounted');
 results.innerHTML=rendered;
 assert.equal(experienceClick({dataset:{commandIndex:'0'}}),true);
 assert.equal(location.hash,'decompose');
 assert.equal(closed,true);
});

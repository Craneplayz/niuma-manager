import test from 'node:test';
import assert from 'node:assert/strict';

test('layout scroll does not start a boundary snap; an explicit user scroll still does',async()=>{
 const originals=Object.fromEntries(['window','document','location','scrollY','innerHeight','matchMedia'].map(key=>[key,globalThis[key]]));
 const calls=[];globalThis.window=new EventTarget();globalThis.location={hash:'#agent'};globalThis.scrollY=640;globalThis.innerHeight=800;globalThis.matchMedia=()=>({matches:true});
 const review={getBoundingClientRect:()=>({top:720-globalThis.scrollY}),scrollIntoView:()=>calls.push('review')};
 globalThis.document={querySelector:selector=>selector==='#import-review'?review:null};
 try{
  await import('../src/import-journey.js?scroll-regression');
  window.dispatchEvent(new Event('scroll'));await new Promise(resolve=>setTimeout(resolve,230));assert.deepEqual(calls,[]);
  window.dispatchEvent(new Event('wheel'));globalThis.scrollY=680;window.dispatchEvent(new Event('scroll'));
  await new Promise(resolve=>setTimeout(resolve,230));assert.deepEqual(calls,['review']);
 }finally{for(const [key,value] of Object.entries(originals))globalThis[key]=value;}
});

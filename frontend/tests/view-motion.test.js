import test from 'node:test';
import assert from 'node:assert/strict';
import {transitionView} from '../src/view-motion.js';
test('rapid navigation commits immediately and cancels earlier entrance animations',()=>{
 const originalDocument=globalThis.document;let cancelled=0,updates=0;
 const region={querySelectorAll:()=>[],animate(){return {cancel(){cancelled++;}};}};
 globalThis.document={documentElement:{dataset:{}},querySelector:()=>region,startViewTransition(){assert.fail('snapshots must not block input');}};
 try{let page='agent';transitionView(()=>{page='tasks';updates++;});assert.equal(page,'tasks');transitionView(()=>{page='calendar';updates++;},{scope:'calendar'});assert.equal(page,'calendar');assert.equal(updates,2);assert.equal(cancelled,1);assert.deepEqual(document.documentElement.dataset,{});}finally{globalThis.document=originalDocument;}
});
test('reduced motion renders immediately without animations',()=>{
 const originalDocument=globalThis.document,originalMatchMedia=globalThis.matchMedia;
 globalThis.document={documentElement:{dataset:{}},querySelector(){assert.fail('no motion needed');}};globalThis.matchMedia=()=>({matches:true});
 try{let updates=0;transitionView(()=>updates++);assert.equal(updates,1);assert.deepEqual(document.documentElement.dataset,{});}finally{globalThis.document=originalDocument;globalThis.matchMedia=originalMatchMedia;}
});
test('a failed render clears navigation scope so later business updates can animate',()=>{
 const originalDocument=globalThis.document;globalThis.document={documentElement:{dataset:{}},querySelector:()=>null};
 try{assert.throws(()=>transitionView(()=>{throw new Error('render failed');}),/render failed/);assert.deepEqual(document.documentElement.dataset,{});}finally{globalThis.document=originalDocument;}
});

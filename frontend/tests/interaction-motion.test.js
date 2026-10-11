import test from 'node:test';
import assert from 'node:assert/strict';
import {motionUpdate,waitingPanel} from '../src/interaction-motion.js';
test('reduced motion commits once without reading or animating the DOM',()=>{
 const previous=globalThis.matchMedia;globalThis.matchMedia=()=>({matches:true});
 try{let commits=0;motionUpdate(()=>commits++);assert.equal(commits,1);}finally{globalThis.matchMedia=previous;}
});
test('retained task animates from its old position after sorting; update is synchronous',()=>{
 const previousDocument=globalThis.document,previousMedia=globalThis.matchMedia;
 const calls=[];let y=80;
 const node={dataset:{motionTask:'a',motionDone:'todo'},getBoundingClientRect:()=>({left:0,top:y,height:40,width:200}),animate:(frames,options)=>{calls.push({frames,options});return {cancel(){}};}};
 globalThis.matchMedia=()=>({matches:false});globalThis.document={querySelectorAll:()=>[node],querySelector:()=>null};
 try{motionUpdate(()=>{y=20;});assert.equal(y,20);assert.equal(calls.length,1);assert.equal(calls[0].frames[0].transform,'translate(0px,60px)');motionUpdate(()=>{});assert.equal(calls.length,1);}finally{globalThis.document=previousDocument;globalThis.matchMedia=previousMedia;}
});
test('waiting state announces real waiting without a fabricated percentage',()=>{
 const html=waitingPanel('正在规划','等待服务返回');assert.match(html,/role="status"/);assert.match(html,/aria-hidden="true"/);assert.doesNotMatch(html,/%/);
});

test('background imports update synchronously without reading layout or animating cards',()=>{
 const previousDocument=globalThis.document;globalThis.document={querySelectorAll(){assert.fail('background imports must not run FLIP');}};
 try{let updated=false;motionUpdate(()=>{updated=true;},{animate:false});assert.equal(updated,true);}finally{globalThis.document=previousDocument;}
});

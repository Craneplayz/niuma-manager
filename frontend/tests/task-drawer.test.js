import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareDialog} from '../src/task-drawer.js';

test('closing a task drawer releases scroll and returns focus to a replacement task row',()=>{
 const previous=globalThis.document,attributes=new Set(),classes=new Set();let close,focused=0;
 const target={focus:options=>{assert.equal(options.preventScroll,true);focused++;}};
 const row={dataset:{motionTask:'task'},querySelector:()=>target};
 const task={dataset:{id:'task'}},heading={};
 const dialog={open:false,querySelector:selector=>selector==='#task-form'?task:heading,classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name),remove:(...names)=>names.forEach(name=>classes.delete(name))},setAttribute(){},removeAttribute(){},addEventListener:(name,handler)=>{if(name==='close')close=handler;}};
 globalThis.document={activeElement:{isConnected:false},documentElement:{toggleAttribute:(name,on)=>on?attributes.add(name):attributes.delete(name),removeAttribute:name=>attributes.delete(name)},querySelectorAll:()=>[row]};
 try{prepareDialog(dialog);assert.equal(attributes.has('data-task-drawer'),true);close();assert.equal(attributes.size,0);assert.equal(classes.size,0);assert.equal(focused,1);}finally{globalThis.document=previous;}
});

import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();globalThis.document=new EventTarget();
const {editedSchedule,scheduleTimes}=await import('../src/schedule-editor.js');

test('a learning block can move across midnight without changing its task or losing its identity',()=>{
 const item={id:'p',taskId:'t',label:'写报告',date:'2026-10-07',start:19,hours:1};
 const changed=editedSchedule(item,{date:'2026-10-08',start:'23:30',endDate:'2026-10-09',end:'00:45',label:'完成报告'},[item]);
 assert.deepEqual(changed,{...item,date:'2026-10-08',start:23.5,hours:1.25,label:'完成报告'});
 assert.deepEqual(scheduleTimes(changed),{date:'2026-10-08',start:'23:30',endDate:'2026-10-09',end:'00:45'});
 assert.equal(item.start,19);
});

test('time edits reject reversed or overlapping intervals, while adjacent intervals remain valid',()=>{
 const item={id:'p',taskId:'t',date:'2026-10-08',start:19,hours:1,label:'学习'};
 const other={id:'other',taskId:'u',date:'2026-10-08',start:21,hours:1};
 const values={date:item.date,start:'20:00',endDate:item.date,end:'21:00',label:'学习'};
 assert.equal(editedSchedule(item,values,[item,other]).hours,1);
 assert.throws(()=>editedSchedule(item,{...values,end:'20:00'},[item,other]),/结束时间/);
 assert.throws(()=>editedSchedule(item,{...values,end:'21:30'},[item,other]),/重叠/);
 const overnight={...other,date:'2026-10-07',start:23,hours:22};
 assert.throws(()=>editedSchedule(item,values,[item,overnight]),/重叠/);
});

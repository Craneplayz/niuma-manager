import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();globalThis.document=new EventTarget();
const {editedSchedule,scheduleTimes,configureScheduleEditor,scheduleEditorSubmit}=await import('../src/schedule-editor.js');
const {calendarPage,calendarClick}=await import('../src/team-calendar-ui.js');

test('a learning block can move across midnight without changing its task or losing its identity',()=>{
 const item={id:'p',taskId:'t',label:'写报告',date:'2026-10-07',start:19,hours:1};
 const changed=editedSchedule(item,{date:'2026-10-08',start:'23:30',endDate:'2026-10-09',end:'00:45',label:'完成报告'},[item]);
 assert.deepEqual(changed,{...item,date:'2026-10-08',start:23.5,hours:1.25,label:'完成报告'});
 assert.deepEqual(scheduleTimes(changed),{date:'2026-10-08',start:'23:30',endDate:'2026-10-09',end:'00:45'});
 assert.equal(item.start,19);
});

test('a slot hidden by the calendar can be saved through the real editor submit handler',async()=>{
 const originalNow=Date.now,originalFormData=globalThis.FormData,originalAnimationFrame=globalThis.requestAnimationFrame;
 Date.now=()=>Date.parse('2026-10-08T12:00:00+08:00');
 globalThis.requestAnimationFrame=()=>0;
 globalThis.FormData=class {constructor(form){return new Map(Object.entries(form.values));}};
 try{
  const state={tasks:[{id:'done',status:'done'},{id:'active',status:'todo'}],schedule:[
   {id:'released',taskId:'done',date:'2026-10-08',start:19,hours:1,label:'已完成'},
   {id:'active-plan',taskId:'active',date:'2026-10-08',start:21,hours:1,label:'学习'}]};
  calendarClick({dataset:{calendarDay:'2026-10-08'}},()=>{});
  assert.doesNotMatch(calendarPage(state,0),/data-schedule-id="released"/);
  const error={textContent:''},fields=[{disabled:false}];let saved,closed=false,rendered=false;
  const form={id:'schedule-form',dataset:{planId:'active-plan',taskId:'active'},
   values:{date:'2026-10-08',start:'19:00',endDate:'2026-10-08',end:'20:00',label:'学习'},
   querySelector:()=>error,querySelectorAll:()=>fields};
  let finish;const finished=new Promise(resolve=>{finish=resolve;});
  configureScheduleEditor({getState:()=>state,persist:()=>{saved=structuredClone(state);},
   closeDialog:()=>{closed=true;},render:()=>{rendered=true;},toast:finish});
  assert.equal(scheduleEditorSubmit(form),true);
  assert.equal(await finished,'学习时段已保存');
  assert.equal(error.textContent,'');assert.equal(closed,true);assert.equal(rendered,true);
  assert.equal(saved.schedule[1].start,19);
  assert.deepEqual(saved.schedule[0],{id:'released',taskId:'done',date:'2026-10-08',start:19,hours:1,label:'已完成'});
 }finally{Date.now=originalNow;globalThis.FormData=originalFormData;globalThis.requestAnimationFrame=originalAnimationFrame;}
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

test('manual edits use released completed slots without deleting stored plans',()=>{
 const originalNow=Date.now;Date.now=()=>Date.parse('2026-10-08T12:00:00+08:00');
 try{
  const item={id:'p',taskId:'active',date:'2026-10-08',start:21,hours:1,label:'学习'};
  const released={id:'released',taskId:'done',date:item.date,start:19,hours:1};
  const schedule=[item,released],original=structuredClone(schedule);
  const tasks=[{id:'active',status:'todo'},{id:'done',status:'done'}];
  const values={date:item.date,start:'19:00',endDate:item.date,end:'20:00',label:'学习'};
  assert.equal(editedSchedule(item,values,schedule,tasks).start,19);
  assert.deepEqual(schedule,original);
  assert.throws(()=>editedSchedule(item,values,schedule,[{id:'done',status:'todo'}]),/重叠/);
  for(const start of [10,11.5,12]){
   const retained={...released,start,hours:3};
   assert.throws(()=>editedSchedule(item,{...values,start:'12:00',end:'13:00'},[item,retained],tasks),/重叠/);
  }
 }finally{Date.now=originalNow;}
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {calendarClick,calendarStep,calendarPage,calendarToday,focusCalendar,resetCalendarFocus} from '../src/team-calendar-ui.js';
import {dayKey} from '../src/data.js';

test('completed future plans leave the calendar while history and active tasks remain',()=>{
 const originalAnimationFrame=globalThis.requestAnimationFrame;
 globalThis.requestAnimationFrame=()=>0;
 try{
  const date=dayKey(),now=Date.parse(date+'T12:00:00+08:00');
  const state={tasks:[{id:'done',status:'done'},{id:'active',status:'todo'}],schedule:[
   {id:'history',taskId:'done',date,start:10,hours:1,label:'历史安排'},
   {id:'ongoing',taskId:'done',date,start:12,hours:1,label:'已开始安排'},
   {id:'unused',taskId:'done',date,start:19,hours:1,label:'已完成未来安排'},
   {id:'active-plan',taskId:'active',date,start:19,hours:1,label:'未完成安排'}]};
  const original=structuredClone(state),originalNow=Date.now;Date.now=()=>now;
  try{
   for(const view of ['day','week','month']){
    calendarClick({dataset:{calendarView:view}},()=>{});
    const html=calendarPage(state,0);
    assert.doesNotMatch(html,/data-schedule-id="unused"/);
    for(const id of ['history','ongoing','active-plan'])assert.ok(html.includes(`data-schedule-id="${id}"`),view);
   }
   assert.deepEqual(state,original);
  }finally{Date.now=originalNow;}
 }finally{globalThis.requestAnimationFrame=originalAnimationFrame;}
});

test('month navigation from the 31st reaches the immediately adjacent month',()=>{
 const RealDate=Date;
 globalThis.Date=class extends RealDate {constructor(...args){super(...(args.length?args:['2026-01-31T12:00:00']));}};
 try{
  calendarClick({dataset:{calendarView:'month'}},()=>{});
  calendarStep(1,0);
  assert.match(calendarPage({tasks:[],schedule:[]},0),/2026 年 02 月/);
  calendarStep(-1,0);
  assert.match(calendarPage({tasks:[],schedule:[]},0),/2026 年 01 月/);
 }finally{globalThis.Date=RealDate;}
});

test('calendar dates open the day view; plans open their own editor and deadlines open tasks',()=>{
 const originalAnimationFrame=globalThis.requestAnimationFrame;
 globalThis.requestAnimationFrame=()=>0;
 try{
 calendarToday();
 const date=dayKey();
 const state={
  tasks:[{id:'task-1',title:'提交作业',status:'todo',due:date,dueTime:'18:00',source:'教学网'}],
  schedule:[{id:'plan-1',taskId:'task-1',date,start:10,hours:1,label:'完成作业'}]
 };

 calendarClick({dataset:{calendarView:'month'}},()=>{});
 let html=calendarPage(state,0);
 assert.match(html,new RegExp(`data-calendar-day="${date}"[^>]*aria-label="查看 ${date} 日程"`));
 assert.match(html,/class="calendar-month-ddl" data-edit="task-1"/);
 assert.match(html,/class="calendar-month-plan" data-schedule-id="plan-1" data-schedule-task="task-1"/);

 calendarClick({dataset:{calendarView:'week'}},()=>{});
 html=calendarPage(state,0);
 assert.match(html,new RegExp(`<header><button class="calendar-day-link" data-calendar-day="${date}"`));
 assert.match(html,/class="calendar-timed-event plan" data-schedule-id="plan-1" data-schedule-task="task-1"/);
 assert.match(html,/class="calendar-timed-event ddl" data-edit="task-1"/);

 let rendered=0;
 assert.equal(calendarClick({dataset:{calendarDay:date}},()=>{rendered++;}),true);
 html=calendarPage(state,0);
 assert.equal(rendered,1);
 assert.match(html,new RegExp(`<h2>${date}</h2>`));
 assert.match(html,/calendar-time-grid single/);
 }finally{globalThis.requestAnimationFrame=originalAnimationFrame;}
});


test('new plans open their own date, highlight only the applied ids and clear on navigation',()=>{
 const originalAnimationFrame=globalThis.requestAnimationFrame;globalThis.requestAnimationFrame=()=>0;
 try{
  const date='2026-11-16';
  const state={tasks:[{id:'a',status:'todo'}],schedule:[{id:'new',taskId:'a',date,start:19,hours:1},{id:'old',taskId:'a',date,start:21,hours:1}]};
  const week=focusCalendar(date,['new']);
  let html=calendarPage(state,week);assert.match(html,/<h2>2026-11-16<\/h2>/);assert.match(html,/data-schedule-id="new"[^>]*data-schedule-highlight="true"/);assert.doesNotMatch(html,/data-schedule-id="old"[^>]*data-schedule-highlight/);
  calendarStep(1,week);html=calendarPage(state,week);assert.doesNotMatch(html,/data-schedule-highlight/);
 }finally{resetCalendarFocus();calendarToday();globalThis.requestAnimationFrame=originalAnimationFrame;}
});

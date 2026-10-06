import test from 'node:test';
import assert from 'node:assert/strict';
import {calendarClick,calendarStep,calendarPage,calendarToday} from '../src/team-calendar-ui.js';
import {dayKey} from '../src/data.js';

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

test('month and week dates open the day view while every calendar item opens its task',()=>{
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
 assert.match(html,/class="calendar-month-plan" data-edit="task-1"/);

 calendarClick({dataset:{calendarView:'week'}},()=>{});
 html=calendarPage(state,0);
 assert.match(html,new RegExp(`<header><button class="calendar-day-link" data-calendar-day="${date}"`));
 assert.match(html,/class="calendar-timed-event plan" data-edit="task-1"/);
 assert.match(html,/class="calendar-timed-event ddl" data-edit="task-1"/);

 let rendered=0;
 assert.equal(calendarClick({dataset:{calendarDay:date}},()=>{rendered++;}),true);
 html=calendarPage(state,0);
 assert.equal(rendered,1);
 assert.match(html,new RegExp(`<h2>${date}</h2>`));
 assert.match(html,/calendar-time-grid single/);
 }finally{globalThis.requestAnimationFrame=originalAnimationFrame;}
});

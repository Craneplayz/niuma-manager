import test from 'node:test';
import assert from 'node:assert/strict';
import {calendarClick,calendarStep,calendarPage} from '../src/team-calendar-ui.js';

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

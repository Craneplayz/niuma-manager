import test from 'node:test';
import assert from 'node:assert/strict';
import {storyShell,journeyHome} from '../src/story-layout.js';

const shell=page=>storyShell({page,user:null,saveLabel:'未登录',saveAlert:'',content:'',focus:''});

test('each business route stays directly reachable in its chapter or workspace tools',()=>{
 const pages=['overview','agent','tasks','decompose','book','trash','plan','calendar','settings'];
 const navigation=['agent','tasks','calendar'].map(shell).join('');
 for(const page of pages)assert.ok(navigation.includes(`href="#${page}"`),`${page} has a direct entry`);
 assert.match(shell('decompose'),/href="#decompose" class="selected" aria-current="page"/);
 assert.match(shell('calendar'),/href="#calendar" class="selected" aria-current="page"/);
 assert.match(shell('settings'),/data-experience="search"/);
 assert.match(shell('settings'),/data-experience="focus"/);
 assert.match(shell('settings'),/data-action="calendar-export"/);
 assert.match(shell('settings'),/data-action="export"/);
});

test('workspace chrome and today tasks escape user content in labels and attributes',()=>{
 const malicious='"><img src=x onerror=alert(1)>';
 const chrome=storyShell({page:'tasks',user:{display_name:malicious},saveLabel:malicious,saveAlert:'',content:'',focus:''});
 assert.doesNotMatch(chrome,/<img/);
 assert.match(chrome,/&lt;img/);
 const state={tasks:[{id:malicious,title:malicious,course:malicious,status:'todo',due:''}],schedule:[]};
 const home=journeyHome(state,{display_name:'我'});
 assert.doesNotMatch(home,/<img/);
 assert.match(home,/data-edit="&quot;&gt;&lt;img/);
});

test('today puts the earliest incomplete deadline first and displays real minute-level schedule times',()=>{
 const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const state={tasks:[
  {id:'done',title:'已完成的旧作业',status:'done',due:'2020-01-01'},
  {id:'later',title:'晚些再做',status:'todo',due:'2026-12-31'},
  {id:'first',title:'先完成这件事',status:'doing',due:'2026-10-09',dueTime:'18:00',course:'写作'},
  {id:'undated',title:'待补日期',status:'todo',due:''},
 ],schedule:[{id:'block',taskId:'first',date:today,start:19.25,hours:1.5}]};
 const result=journeyHome(state,{display_name:'我'});
 assert.match(result,/data-edit="first"/);
 assert.doesNotMatch(result,/data-edit="done"/);
 assert.match(result,/19:15<span>20:45/);
 assert.match(result,/data-schedule-id="block"/);
 assert.match(result,/90 分钟/);
 assert.deepEqual(state.tasks.map(task=>task.id),['done','later','first','undated']);
});

test('empty or logged-out today has a useful entry without inventing learning data',()=>{
 const result=journeyHome({tasks:[],schedule:[]},null);
 assert.match(result,/data-action="login"/);
 assert.match(result,/data-action="new"/);
 assert.doesNotMatch(result,/data-schedule-id=/);
 assert.match(result,/href="#plan"/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {selectTasks,remainingSeconds,formatTimer,searchCommands} from '../src/productivity.js';
test('无 DDL 不会被当作逾期，未估时任务也不排在最短前',()=>{
 const tasks=[
  {id:'none',title:'无 DDL',course:'',notes:'',status:'todo',priority:'medium',due:'',hours:null},
  {id:'due',title:'明确截止',course:'',notes:'',status:'todo',priority:'medium',due:'2026-09-25',dueTime:'18:00',hours:1}
 ];
 assert.deepEqual(selectTasks(tasks,{today:'2026-09-26',filter:'overdue'}).map(t=>t.id),['due']);
 assert.deepEqual(selectTasks(tasks,{today:'2026-09-26'}).map(t=>t.id),['due','none']);
 assert.deepEqual(selectTasks(tasks,{today:'2026-09-26',sort:'duration'}).map(t=>t.id),['due','none']);
});
const base={title:'写报告',course:'软件工程',notes:'组会',status:'todo',priority:'medium',due:'2026-09-24',hours:2,dependsOn:''};
const tasks=[{...base,id:'a',due:'2026-09-23'},{...base,id:'b',dueTime:'09:00',priority:'high'},{...base,id:'c',dueTime:'18:00',hours:.5},{...base,id:'d',status:'done',due:'2026-09-20'},{...base,id:'e',dependsOn:'a'}];
test('今天和逾期筛选排除已完成任务',()=>{assert.deepEqual(selectTasks(tasks,{filter:'overdue',today:'2026-09-24'}).map(t=>t.id),['a']);assert.equal(selectTasks(tasks,{filter:'today',today:'2026-09-24'}).length,3);});
test('前置阻塞筛选与备注搜索可以叠加',()=>{assert.deepEqual(selectTasks(tasks,{filter:'blocked',query:'组会'}).map(t=>t.id),['e']);});
test('排序按优先级和工时，已完成始终置底',()=>{assert.equal(selectTasks(tasks,{sort:'priority'})[0].id,'b');assert.equal(selectTasks(tasks,{sort:'duration'})[0].id,'c');assert.equal(selectTasks(tasks,{sort:'due'}).at(-1).id,'d');});
test('同日按截止时刻排序，全天任务置于明确时刻之后',()=>{assert.deepEqual(selectTasks(tasks,{filter:'today',today:'2026-09-24'}).map(t=>t.id),['b','c','e']);});
test('计时使用绝对时间，休眠后不会少算或变负数',()=>{assert.equal(remainingSeconds({running:true,endsAt:160000,remaining:1500},100000),60);assert.equal(remainingSeconds({running:true,endsAt:160000,remaining:1500},200000),0);assert.equal(remainingSeconds({running:false,remaining:321},999999),321);});
test('全局搜索限制结果数量并支持课程匹配',()=>{assert.equal(searchCommands(Array.from({length:20},(_,i)=>({...base,id:i})),'软件').length,7);assert.equal(formatTimer(1500),'25:00');});

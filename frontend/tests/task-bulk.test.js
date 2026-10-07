import test from 'node:test';
import assert from 'node:assert/strict';
import {updateTaskCompletion,restoreTaskCompletion,removeTasks,restoreRemovedTasks} from '../src/task-bulk.js';

const tasks=[
 {id:'a',title:'前置任务',status:'todo',completedAt:'',dependsOn:''},
 {id:'b',title:'被阻塞任务',status:'doing',completedAt:'',dependsOn:'a'},
 {id:'c',title:'已完成任务',status:'done',completedAt:'2026-10-01',dependsOn:''},
 {id:'d',title:'依赖待删除任务',status:'todo',completedAt:'',dependsOn:'c'}
];

test('批量完成会记录完成日期并跳过被前置任务阻塞的项目',()=>{
 const result=updateTaskCompletion(tasks,['a','b'],{completed:true,date:'2026-10-06'});
 assert.equal(result.tasks.find(task=>task.id==='a').status,'done');
 assert.equal(result.tasks.find(task=>task.id==='a').completedAt,'2026-10-06');
 assert.equal(result.tasks.find(task=>task.id==='b').status,'doing');
 assert.deepEqual(result.skipped,['b']);
});

test('批量重新打开会清空完成日期，并可恢复操作前状态',()=>{
 const result=updateTaskCompletion(tasks,['c'],{completed:false,date:'2026-10-06'});
 assert.equal(result.tasks.find(task=>task.id==='c').status,'todo');
 assert.equal(result.tasks.find(task=>task.id==='c').completedAt,'');
 assert.deepEqual(restoreTaskCompletion(result.tasks,result.changed),tasks);
});

test('批量删除清理关联日程和依赖，撤销后按原顺序恢复',()=>{
 const schedule=[{id:'s1',taskId:'a'},{id:'s2',taskId:'c'},{id:'s3',taskId:'d'}];
 const removed=removeTasks(tasks,schedule,['a','c']);
 assert.deepEqual(removed.tasks.map(task=>task.id),['b','d']);
 assert.equal(removed.tasks.find(task=>task.id==='b').dependsOn,'');
 assert.equal(removed.tasks.find(task=>task.id==='d').dependsOn,'');
 assert.deepEqual(removed.schedule.map(event=>event.id),['s3']);
 const restored=restoreRemovedTasks(removed.tasks,removed.schedule,removed.snapshot);
 assert.deepEqual(restored.tasks,tasks);
 assert.deepEqual(restored.schedule,schedule);
});

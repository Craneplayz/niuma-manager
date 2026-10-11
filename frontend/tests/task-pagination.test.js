import test from 'node:test';
import assert from 'node:assert/strict';
import {paginateTasks,taskPaginationMarkup} from '../src/task-pagination.js';
const tasks=Array.from({length:23},(_,id)=>({id}));
test('task pages cover every item once and keep the incoming filtered/sorted order',()=>{
 const pages=[1,2,3].map(page=>paginateTasks(tasks,page));
 assert.deepEqual(pages.map(page=>page.items.length),[10,10,3]);
 assert.deepEqual(pages.flatMap(page=>page.items),tasks);assert.deepEqual(pages[2],{items:tasks.slice(20),page:3,pageCount:3,total:23,start:21,end:23});
 const reversed=[...tasks].reverse();assert.deepEqual(paginateTasks(reversed,2).items,reversed.slice(10,20));
});
test('removing the last page or filtering fewer items clamps to an existing page',()=>{
 assert.equal(paginateTasks(tasks.slice(0,20),3).page,2);assert.equal(paginateTasks(tasks.slice(0,5),3).page,1);
 assert.deepEqual(paginateTasks([],3),{items:[],page:1,pageCount:1,total:0,start:0,end:0});
 for(const page of [-1,0,NaN,Infinity])assert.equal(paginateTasks(tasks,page).page,1);
});
test('pagination exposes total, current page and disabled boundaries; empty results have no pager',()=>{
 const first=taskPaginationMarkup(paginateTasks(tasks,1)),last=taskPaginationMarkup(paginateTasks(tasks,3));
 assert.match(first,/aria-label="任务分页"/);assert.match(first,/共 23 项/);assert.match(first,/第 1 \/ 3 页/);
 assert.match(first,/data-task-page="0" disabled/);assert.match(last,/data-task-page="4" disabled/);
 assert.equal(taskPaginationMarkup(paginateTasks([])),'');
});

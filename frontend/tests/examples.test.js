import test from 'node:test';
import assert from 'node:assert/strict';
import {addExamples,examplePack,weekReview} from '../src/examples.js';

test('示例重复加载保留用户内容、编辑结果和已有同名课程',()=>{
 const original={version:1,tasks:[],schedule:[],materials:[],courses:[{id:'mine',name:'线性代数',teacher:'我的老师'}]};
 const first=addExamples(original);
 assert.equal(original.tasks.length,0);
 assert.equal(first.state.courses.filter(c=>c.name==='线性代数').length,1);
 first.state.tasks[0].title='我的修改';
 const again=addExamples(first.state);
 assert.equal(again.added,0);
 assert.equal(again.state.tasks[0].title,'我的修改');
 assert.equal(again.state.courses[0].teacher,'我的老师');
});
test('示例日程和依赖引用有效任务且资料标识为示例',()=>{
 const p=examplePack(),ids=new Set(p.tasks.map(t=>t.id));
 assert.equal(p.tasks.length,5);assert.equal(p.materials.length,3);
 assert.ok(p.schedule.every(e=>ids.has(e.taskId)));
 assert.ok(p.tasks.every(t=>!t.dependsOn||ids.has(t.dependsOn)));
 assert.ok(p.materials.every(m=>m.title.startsWith('【示例】')));
});
test('本周统计使用周一至周日边界并排除周外数据',()=>{
 const tasks=[{due:'2026-09-21',status:'done',hours:2},{due:'2026-09-27',status:'todo',hours:3},{due:'2026-09-28',status:'todo',hours:9}];
 assert.deepEqual(weekReview({tasks,schedule:[{date:'2026-09-24',hours:1.5},{date:'2026-09-28',hours:8}]},new Date(2026,8,24)),{total:2,done:1,remaining:3,scheduled:1.5});
});

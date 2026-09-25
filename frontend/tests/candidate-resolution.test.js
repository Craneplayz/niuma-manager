import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();
const {buildResolution}=await import('../src/team-recognition-ui.js');
const item={version:3};
const values={action:'create',title:'需求报告',description:'提交报告',course:'工程课',date:'2026-09-30',time:'',reviewed:'on'};

test('a date-only candidate can become a task without fabricating a deadline time',()=>{
 const body=buildResolution(item,values,[],7);
 assert.deepEqual(body.draft.due,{date:'2026-09-30',precision:'date'});
 assert.equal(body.target_id,null);
 assert.equal(body.tasks_version,7);
 assert.equal(body.version,3);
 assert.equal(body.reviewed,true);
});
test('precise and absent deadlines remain distinct',()=>{
 assert.deepEqual(buildResolution(item,{...values,time:'15:30'},[],0).draft.due,{date:'2026-09-30',time:'15:30',precision:'datetime'});
 assert.equal(buildResolution(item,{...values,date:''},[],0).draft.due,null);
 assert.throws(()=>buildResolution(item,{...values,date:'',time:'15:30'},[],0),/日期/);
});
test('cancel does not require a new title or deadline; postpone preserves task content',()=>{
 const task={id:'target',title:'已有任务',notes:'已有说明',course:'原课程'};
 const cancelled=buildResolution(item,{action:'cancel',target_id:'target',date:'',time:'',reviewed:'on'},[task],1);
 assert.equal(cancelled.action,'cancel');
 assert.equal(cancelled.draft,undefined);
 const postponed=buildResolution(item,{...values,action:'postpone',target_id:'target'},[task],1);
 assert.equal(postponed.draft.title,task.title);
 assert.equal(postponed.draft.description,task.notes);
 assert.equal(postponed.draft.course,task.course);
 assert.throws(()=>buildResolution(item,{...values,action:'postpone',target_id:'target',date:''},[task],1),/截止日期/);
});
test('confirmation requires review and an explicit existing target for changes',()=>{
 assert.throws(()=>buildResolution(item,{...values,reviewed:''},[],0),/核对/);
 assert.throws(()=>buildResolution(item,{...values,action:'update',target_id:'missing'},[],0),/目标任务/);
 assert.throws(()=>buildResolution(item,{...values,target_id:'other'},[],0),/不能指定/);
 assert.throws(()=>buildResolution(item,{...values,action:''},[],0),/处理方式/);
});

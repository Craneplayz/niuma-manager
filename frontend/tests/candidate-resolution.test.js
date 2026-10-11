import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();
const {buildResolution,buildQuickResolution,deadlineLabel,suggestedDue,candidateCard}=await import('../src/team-recognition-ui.js');
const item={version:3};
const values={action:'create',title:'需求报告',description:'提交报告',course:'工程课',date:'2026-09-30',time:'',reviewed:'on'};

test('deadline display is concise and inferred dates remain reviewable',()=>{
 const d={value:'2026-09-29T09:00:00+08:00',precision:'datetime',timezone:'Asia/Shanghai',kind:'deadline',status:'inferred',basis:'按采集时间 2026-09-28 补全年份。',raw:'9月29日9点'};
 assert.equal(deadlineLabel(d),'截止时间（待确认）：2026/09/29 09:00');
 assert.deepEqual(suggestedDue(d),{date:'2026-09-29',time:'09:00'});
 assert.deepEqual(suggestedDue({...d,kind:'reference'}),{date:'',time:''});
 assert.match(deadlineLabel({...d,kind:'reference'}),/参考时间，不是 DDL/);
 assert.equal(deadlineLabel({...d,status:'explicit',precision:'date',value:'2026-09-29'}),'明确截止日期（时刻待补充）：2026/09/29');
 assert.equal(deadlineLabel({...d,status:'explicit',value:'2026-09-29T09:00:30+08:00'}),'明确截止时间：2026/09/29 09:00:30');
 assert.deepEqual(suggestedDue({...d,value:'2026-09-29T09:00:30+08:00'}),{date:'2026-09-29',time:''});
 const html=candidateCard({id:'sample',state:'pending',candidate:{title:'交报告',description:'报告',notice_type:'create',deadline:d,review_reasons:[d.basis,'正文不完整'],prerequisites:[],evidence:[],sources:[]}});
 assert.equal(html.split(d.basis).length-1,1);
 assert.ok(html.indexOf(d.basis)>html.indexOf('<summary>核对说明</summary>'));
 assert.match(html,/<p>正文不完整<\/p>/);
 assert.doesNotMatch(html,/draft-warning/);
 assert.match(html,/data-agent-confirm="sample"/);
});
test('quick confirmation creates the reviewed task draft directly',()=>{
 const candidate={title:'需求报告',description:'提交报告',course_or_project:'工程课',deadline:{kind:'deadline',status:'explicit',precision:'date',value:'2026-09-30'}};
 assert.deepEqual(buildQuickResolution({version:4,candidate},9),{action:'create',version:4,tasks_version:9,target_id:null,reviewed:true,draft:{title:'需求报告',description:'提交报告',course:'工程课',due:{date:'2026-09-30',precision:'date'}}});
});

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

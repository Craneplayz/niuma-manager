import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.CustomEvent ||= class extends Event {constructor(type){super(type);}};
const store=await import('../src/team-store.js');
const personalization=await import('../src/team-personalization-ui.js');

test('completion feedback is on by default, persists its setting, and saves actual minutes',async()=>{
 const task={id:'done-task',title:'完成报告',status:'completed',due:null,plans:[],subtasks:[],estimatedMinutes:60};
 const workspace={tasks:{version:1,value:[task]}},writes=[];
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return {ok:true,json:async()=>({user:{id:'u',name:'测试',email:'u@test.local'}})};
  if(path==='/api/workspace')return {ok:true,json:async()=>structuredClone(workspace)};
  if(path.startsWith('/api/data/')){
   const name=path.split('/').at(-1),body=JSON.parse(options.body);writes.push({name,body});
   workspace[name]={value:body.value,version:(workspace[name]?.version||0)+1};
   return {ok:true,json:async()=>({version:workspace[name].version})};
  }
  throw new Error('unexpected request '+path);
 };
 const state=await store.initSession();
 let dialog='',closed=0,message='';
 personalization.configurePersonalization({render(){},openDialog:html=>{dialog=html;},closeDialog:()=>{closed++;},toast:value=>{message=value;},dialogHeader:title=>`<h2>${title}</h2>`});
 assert.equal(personalization.asksForCompletionMinutes(),true);
 assert.match(personalization.personalizationSettings(),/data-feedback-setting="completion-minutes" checked/);
 assert.equal(personalization.promptForCompletionMinutes(state.tasks[0]),true);
 assert.match(dialog,/completion-feedback-form/);
 assert.match(dialog,/value="60"/);

 const originalFormData=globalThis.FormData;
 globalThis.FormData=class {get(){return '48';}};
 const error={textContent:''},submit={disabled:false};
 await personalization.personalizationSubmit({id:'completion-feedback-form',dataset:{taskId:'done-task'},querySelector:selector=>selector==='[type="submit"]'?submit:error});
 globalThis.FormData=originalFormData;
 const feedback=writes.find(write=>write.name==='feedback').body.value;
 assert.equal(feedback[0].taskId,'done-task');
 assert.equal(feedback[0].actualMinutes,48);
 assert.equal(closed,1);
 assert.match(message,/后续规划/);

 const target={dataset:{feedbackSetting:'completion-minutes'},checked:false,disabled:false};
 assert.equal(personalization.personalizationChange({target}),true);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(personalization.asksForCompletionMinutes(),false);
 assert.equal(personalization.promptForCompletionMinutes(state.tasks[0]),false);
 assert.deepEqual(writes.find(write=>write.name==='preferences').body.value,{askCompletionMinutes:false});
});

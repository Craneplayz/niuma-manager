import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window=new EventTarget();
globalThis.CustomEvent ||= class extends Event {constructor(type){super(type);}};
const store=await import('../src/team-store.js');
const decomposition=await import('../src/team-decomposition-ui.js');

const response=value=>({ok:true,json:async()=>structuredClone(value)});

test('AI decomposition asks questions, previews steps, and only writes after confirmation',async()=>{
 let workspace={tasks:{version:1,value:[{
  id:'project',title:'完成课程项目',description:'实现任务页并准备展示',course:'软件工程',
  status:'not_started',due:null,plans:[],subtasks:[]
 }]}};
 const calls=[];
 globalThis.fetch=async(path,options={})=>{
  if(path==='/api/auth/me')return response({user:{id:'u',name:'测试',email:'u@test.local'}});
  if(path==='/api/workspace')return response(workspace);
  const body=JSON.parse(options.body);calls.push({path,body});
  if(path==='/api/decomposition/proposals'&&calls.filter(call=>call.path===path).length===1)return response({
   status:'needs_clarification',assessment:{summary:'还需要当前进度'},questions:[{
    id:'progress',prompt:'目前完成到哪一步？',why:'决定剩余步骤',answer_options:['尚未开始','已有初稿']
   }],plan:null
  });
  if(path==='/api/decomposition/proposals')return response({
   status:'ready_to_plan',requestId:'proposal',assessment:{summary:'可以拆解'},questions:[],
   plan:{goal:'完成课程项目',strategy:'先实现再展示',warnings:[],subtasks:[
    {id:'one',title:'完成任务页',outcome:'页面可运行',duration_minutes:80,depends_on:[],assumption:false},
    {id:'two',title:'制作演示文稿',outcome:'文稿可展示',duration_minutes:40,depends_on:['one'],assumption:false}
   ]}
  });
  if(path==='/api/decomposition/apply'){
   workspace={tasks:{version:2,value:[{...workspace.tasks.value[0],estimatedMinutes:120,
    subtasks:[{id:'s1',title:'完成任务页',status:'pending'},{id:'s2',title:'制作演示文稿',status:'pending'}]}]}};
   return response({value:workspace.tasks.value,version:2});
  }
  throw new Error('unexpected request '+path);
 };
 let state=await store.initSession(),renders=0,message='';
 decomposition.configureDecomposition({
  getState:()=>state,render(){renders++;},toast:value=>{message=value;},requireLogin(){throw new Error('unexpected login');},
  refresh:async()=>{state=await store.refreshStore();}
 });
 assert.match(decomposition.decompositionPage(),/AI 任务拆解/);
 decomposition.decompositionClick({dataset:{decompositionAction:'select',taskId:'project'}});
 assert.match(decomposition.decompositionPage(),/开始 AI 拆解/);
 decomposition.decompositionClick({dataset:{decompositionAction:'start'}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(decomposition.decompositionPage(),/目前完成到哪一步/);

 const handled=decomposition.decompositionSubmit({
  id:'decomposition-questions-form',querySelectorAll:()=>[{value:'登录已完成，任务页尚未开始'}]
 });
 assert.equal(handled,true);
 await new Promise(resolve=>setTimeout(resolve,0));
 const preview=decomposition.decompositionPage();
 assert.match(preview,/完成任务页/);
 assert.match(preview,/约 120 分钟/);
 assert.equal(calls[1].body.conversation[1].role,'user');
 assert.equal(state.tasks[0].subtasks.length,0);

 decomposition.decompositionClick({dataset:{decompositionAction:'apply',requestId:'proposal'}});
 await new Promise(resolve=>setTimeout(resolve,0));
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(state.tasks[0].subtasks.length,2);
 assert.match(decomposition.decompositionPage(),/步骤已经写入任务/);
 assert.match(message,/已写入任务/);
 assert.ok(renders>=5);
});

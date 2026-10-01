// Adapts the collaborator's page state to the shared team workspace API.
import {dayKey} from './data.js';
export const session={user:null,backend:true,aiConfigured:false,sync:'saved',error:'',conflict:false,changing:false};
const empty=()=>({version:1,tasks:[],courses:[],schedule:[],materials:[]});
let workspace={},versions={},canonicalTasks=[],canonicalCourses=[],queue=Promise.resolve(),latest=null,failed=false;
const clone=value=>structuredClone(value);
let accountEpoch=0;
const requests=new Set();
export const accountVersion=()=>accountEpoch;
function resetAccount(){
 accountEpoch++;
 for(const controller of requests)controller.abort();
 requests.clear();
 session.user=null;workspace={};versions={};canonicalTasks=[];canonicalCourses=[];
 latest=null;failed=false;session.conflict=false;
 window.dispatchEvent(new CustomEvent('niuma:account-reset'));
}
function report(sync,error=''){session.sync=sync;session.error=error;window.dispatchEvent(new CustomEvent('niuma:sync'));}
export async function api(path,body,method='POST',options={}){
 const {timeoutMs=60000,...fetchOptions}=options;
 const epoch=accountEpoch,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
 const abort=()=>controller.abort();
 requests.add(controller);
 if(options.signal?.aborted)controller.abort();
 options.signal?.addEventListener('abort',abort,{once:true});
 try{
  const response=await fetch(path,{method:body===undefined?'GET':method,credentials:'same-origin',
   headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),
    ...fetchOptions,signal:controller.signal});
  const data=await response.json();
  if(epoch!==accountEpoch)throw new Error('账号已切换，请重新操作');
  if(!response.ok){const error=new Error(typeof data.error==='object'?data.error.message:data.error||'请求失败');error.status=response.status;throw error;}
  return data;
 }catch(error){
  if(error.name==='AbortError')throw new Error('请求超时，请重试');
  if(error instanceof TypeError)throw new Error('无法连接服务，请检查后端');
  throw error;
 }finally{clearTimeout(timer);requests.delete(controller);options.signal?.removeEventListener('abort',abort);}
}
export function readStore(){return empty();}
const status={completed:'done',in_progress:'doing',not_started:'todo',cancelled:'todo'};
const canonicalStatus={done:'completed',doing:'in_progress',todo:'not_started'};
export function planDateTime(iso){
 const parts=Object.fromEntries(new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso)).map(p=>[p.type,p.value]));
 return {date:parts.year+'-'+parts.month+'-'+parts.day,time:parts.hour+':'+parts.minute,hour:Number(parts.hour)+Number(parts.minute)/60};
}
function toView(task){
 return {...task,due:task.due?.date||'',dueTime:task.due?.time||'',course:task.course||'',
  notes:task.description||'',hours:task.estimatedMinutes?task.estimatedMinutes/60:null,
  status:status[task.status]||'todo',location:task.location||'',dependsOn:task.dependsOn||'',
  subtasks:(task.subtasks||[]).map(s=>({...s,done:s.status==='completed'}))};
}
function toCanonical(task,previous){
 const old=previous||{},oldSteps=new Map((old.subtasks||[]).map(s=>[s.title,s])),stepsById=new Map((old.subtasks||[]).map(s=>[s.id,s]));
 return {...old,id:task.id,title:task.title,description:task.notes||'',course:task.course||null,
  category:old.category||'课程作业',priority:task.priority||'medium',
  status:canonicalStatus[task.status]||'not_started',
  planningExcluded:!!task.planningExcluded,
  completedAt:task.status==='done'?(task.completedAt||(old.status==='completed'?(old.completedAt||''):dayKey())):'',
  due:task.due?{date:task.due,precision:task.dueTime?'datetime':'date',...(task.dueTime?{time:task.dueTime}:{})}:null,
  source:old.source||(task.id.startsWith('example-')?'模拟数据':'手动创建'),createdAt:old.createdAt||new Date().toISOString(),
  ...(task.id.startsWith('example-')&&Number.isFinite(task.hours)?{estimatedMinutes:task.hours*60}:{}),
  location:task.location||'',owner:task.owner||'',dependsOn:task.dependsOn||'',
  subtasks:(task.subtasks||[]).map(s=>{const previous=stepsById.get(s.id)||oldSteps.get(s.title)||{};return {...previous,id:s.id||previous.id||crypto.randomUUID(),title:s.title,status:s.done?'completed':'pending'};}),
  plans:old.plans||[]};
}
function view(){
 canonicalTasks=clone(workspace.tasks?.value||[]);
 canonicalCourses=clone(workspace.courses?.value||[]);
 const tasks=canonicalTasks.filter(t=>t.status!=='cancelled').map(toView);
 const schedule=canonicalTasks.filter(t=>t.status!=='cancelled').flatMap(t=>(t.plans||[]).map(p=>({
  id:p.id,taskId:t.id,date:planDateTime(p.startAt).date,start:planDateTime(p.startAt).hour,
  hours:p.endAt?(Date.parse(p.endAt)-Date.parse(p.startAt))/3600000:1,label:p.title})));
 return {version:1,tasks,courses:canonicalCourses.map(c=>({...c,color:c.color||'blue',description:c.notes||''})),
  schedule,materials:clone(workspace.materials?.value||[])};
}
export async function initSession(){
 let epoch=accountEpoch;
 try{
  const result=await api('/api/auth/me');
  if(epoch!==accountEpoch)throw new Error('账号已切换');
  if(session.user&&session.user.id!==result.user.id){resetAccount();epoch=accountEpoch;}
  const next=await api('/api/workspace');
  if(epoch!==accountEpoch)throw new Error('账号已切换');
  session.user={...result.user,display_name:result.user.name};workspace=next;
  versions=Object.fromEntries(Object.entries(workspace).map(([key,row])=>[key,row.version]));
  report('saved');return view();
 }catch(error){
  if(epoch!==accountEpoch)throw error;
  if(error.status===401){resetAccount();report('saved');return empty();}
  report('error',error.message);throw error;
 }
}
export async function refreshStore(){const epoch=accountEpoch;await flushStore();if(epoch!==accountEpoch)throw new Error('账号已切换');return initSession();}
function payload(data){
 const old=new Map(canonicalTasks.map(t=>[t.id,t]));
 const tasks=[...canonicalTasks.filter(t=>t.status==='cancelled'),...data.tasks.map(t=>toCanonical(t,old.get(t.id)))];
 const byId=new Map(tasks.map(t=>[t.id,t]));
 for(const item of data.schedule){
  const task=byId.get(item.taskId);if(!task)continue;
  if(task.plans.some(p=>p.id===item.id))continue;
  const h=String(Math.floor(item.start)).padStart(2,'0'),m=String(Math.round((item.start%1)*60)).padStart(2,'0');
  const startAt=`${item.date}T${h}:${m}:00+08:00`;
  task.plans.push({id:item.id||crypto.randomUUID(),title:item.label||task.title,startAt,
   endAt:new Date(Date.parse(startAt)+Number(item.hours)*3600000).toISOString(),origin:'manual'});
 }
 const courses=data.courses.map(c=>{const old=canonicalCourses.find(x=>x.id===c.id)||{};return {...old,id:c.id,name:c.name,
  teacher:c.teacher||'',notes:c.description||'',url:c.url||'',color:c.color||'blue'};});
 return {tasks,courses,materials:data.materials};
}
function enqueue(snapshot){
 const epoch=accountEpoch;
 queue=queue.then(async()=>{
  if(failed||epoch!==accountEpoch)return;
  report('saving');
  try{
   const next=payload(snapshot);
   for(const name of ['tasks','courses','materials']){
    if(JSON.stringify(next[name])===JSON.stringify(workspace[name]?.value||[]))continue;
    const result=await api('/api/data/'+name,{value:next[name],version:versions[name]||0},'PUT');
    if(epoch!==accountEpoch)return;
    versions[name]=result.version;workspace[name]={value:clone(next[name]),version:result.version};
   }
   canonicalTasks=clone(workspace.tasks?.value||[]);canonicalCourses=clone(workspace.courses?.value||[]);
   if(latest===snapshot)latest=null;
   report(latest?'saving':'saved');
  }catch(error){if(epoch!==accountEpoch)return;failed=true;session.conflict=error.status===409;report('error',error.message);}
 });return queue;
}
export function saveStore(data){
 if(!session.user||session.changing){report('error','请先登录账号，或等待账号切换完成');return false;}
 latest=clone(data);if(!failed)enqueue(latest);return true;
}
export async function flushStore(){await queue;if(failed)throw new Error(session.error||'还有未保存的数据');}
export async function retrySave(){if(session.conflict)throw new Error('数据已被其他页面修改，请刷新后再编辑');failed=false;if(latest)await enqueue(latest);await flushStore();}
export async function authenticate(mode,credentials){
 if(session.changing)throw new Error('正在切换账号');
 session.changing=true;
 try{
  await flushStore();resetAccount();
  await api('/api/auth/'+mode,mode==='register'?{email:credentials.email,password:credentials.password,name:credentials.displayName}:{email:credentials.email,password:credentials.password});
  return await initSession();
 }finally{session.changing=false;}
}
export async function logout(){
 if(session.changing)throw new Error('正在切换账号');
 session.changing=true;
 try{await flushStore();resetAccount();await api('/api/auth/logout',{});report('saved');return empty();}
 finally{session.changing=false;}
}
export function dataVersion(name){return versions[name]||0;}
export function workspaceValue(name,fallback){return clone(workspace[name]?.value??fallback);}
export function saveData(name,value){
 if(!session.user||session.changing)return Promise.reject(new Error('请先登录账号，或等待账号切换完成'));
 const snapshot=clone(value),epoch=accountEpoch;
 const update=queue.then(async()=>{
  if(epoch!==accountEpoch)throw new Error('账号已切换');
  const result=await api('/api/data/'+name,{value:snapshot,version:versions[name]||0},'PUT');
  if(epoch!==accountEpoch)throw new Error('账号已切换');
  versions[name]=result.version;workspace[name]={value:snapshot,version:result.version};return result;
 });
 queue=update.catch(()=>{});
 return update;
}
window.addEventListener('beforeunload',event=>{if(latest||failed){event.preventDefault();event.returnValue='';}});

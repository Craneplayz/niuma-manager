import {api,flushStore,session,accountVersion} from './team-store.js';
import {icon} from './icons.js';

let host={};
let selectedId='';
let conversation=[];
let result=null;
let error='';
let busy=false;
let applied=false;
let resultTaskId='';
let activeController=null;

const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const tasks=()=>host.getState().tasks.filter(task=>task.status!=='done');
const selected=()=>tasks().find(task=>task.id===selectedId);

export function configureDecomposition(callbacks){host=callbacks;}

export function resetDecomposition(){
 activeController?.abort();activeController=null;
 selectedId='';conversation=[];result=null;resultTaskId='';error='';busy=false;applied=false;
}

const validResult=()=>Boolean(result&&selected()&&resultTaskId===selectedId&&result.taskId===selectedId);

function taskList(){
 return tasks().map(task=>`<button class="decomposition-task ${task.id===selectedId?'selected':''}" data-decomposition-action="select" data-task-id="${esc(task.id)}">
  <span><b>${esc(task.title)}</b><small>${esc(task.course||'未分类')} · ${task.due?`截止 ${esc(task.due)}`:'无 DDL'}</small></span>
  <em>${task.subtasks.length?`已有 ${task.subtasks.length} 步`:'待拆解'}</em>${icon('chevron')}
 </button>`).join('')||'<div class="decomposition-empty"><p>当前没有需要拆解的任务。</p><a class="btn" href="#tasks">前往任务中心</a></div>';
}

function initialPanel(task){
 if(!task)return `<div class="decomposition-placeholder">${icon('spark')}<h2>选择一项任务</h2><p>AI 会先判断是否需要拆解；信息不足时只询问会影响步骤的问题。</p></div>`;
 return `<div class="decomposition-intro"><span class="decomposition-kicker">准备拆解</span><h2>${esc(task.title)}</h2><p>${esc(task.notes||'暂无任务说明')}</p>
  <div class="decomposition-facts"><span>${task.due?`截止 ${esc(task.due)}${task.dueTime?' '+esc(task.dueTime):''}`:'没有截止日期'}</span><span>${task.subtasks.length?`将重新生成现有 ${task.subtasks.length} 个步骤`:'尚未拆分步骤'}</span></div>
  ${task.subtasks.length?'<p class="decomposition-warning">确认新方案后会替换当前步骤；生成预览不会修改任务。</p>':''}
  <button class="btn primary" data-decomposition-action="start">${icon('spark')}开始 AI 拆解</button></div>`;
}

function questionsPanel(task){
 return `<form id="decomposition-questions-form" class="decomposition-questions"><span class="decomposition-kicker">还需要一点信息</span><h2>${esc(task.title)}</h2><p>${esc(result.assessment.summary)}</p>
  ${result.questions.map((question,index)=>`<label>${esc(question.prompt)}<small>${esc(question.why)}</small><input name="answer-${index}" required maxlength="1000" autocomplete="off" ${question.answer_options.length?`list="answer-options-${index}"`:''} placeholder="输入你的回答"><datalist id="answer-options-${index}">${question.answer_options.map(option=>`<option value="${esc(option)}"></option>`).join('')}</datalist></label>`).join('')}
  <div class="decomposition-actions"><button type="button" class="btn" data-decomposition-action="reset">换一个任务</button><button class="btn primary" type="submit">继续生成方案</button></div></form>`;
}

function planPanel(task){
 const plan=result.plan;
 if(result.status==='no_split')return `<div class="decomposition-direct">${icon('check')}<span class="decomposition-kicker">无需拆解</span><h2>${esc(task.title)}</h2><p>${esc(result.assessment.summary)}</p><div class="decomposition-single"><b>${esc(plan.subtasks[0].title)}</b><span>${esc(plan.subtasks[0].outcome)}</span></div><button class="btn" data-decomposition-action="reset">选择其他任务</button></div>`;
 const names=new Map(plan.subtasks.map(item=>[item.id,item.title]));
 return `<div class="decomposition-plan"><div class="decomposition-plan-head"><div><span class="decomposition-kicker">AI 拆解预览</span><h2>${esc(plan.goal)}</h2><p>${esc(plan.strategy)}</p></div><strong>${plan.subtasks.length} 个步骤 · 约 ${plan.subtasks.reduce((sum,item)=>sum+item.duration_minutes,0)} 分钟</strong></div>
  <ol>${plan.subtasks.map(item=>`<li><span>${esc(item.title)}</span><em>约 ${item.duration_minutes} 分钟</em><p>完成标准：${esc(item.outcome)}</p>${item.depends_on.length?`<small>前置：${item.depends_on.map(id=>esc(names.get(id)||id)).join('、')}</small>`:''}${item.assumption?'<small class="assumption">含 AI 假设，请确认内容</small>':''}</li>`).join('')}</ol>
  ${plan.warnings.length?`<div class="decomposition-warnings">${plan.warnings.map(item=>`<p>${esc(item)}</p>`).join('')}</div>`:''}
  <div class="decomposition-actions"><button class="btn" data-decomposition-action="reset">取消方案</button><button class="btn primary" data-decomposition-action="apply" data-request-id="${esc(result.requestId)}">确认写入任务</button></div></div>`;
}

function workPanel(){
 const task=selected();
 if(result&&!validResult()){result=null;resultTaskId='';}
 if(busy)return `<div class="decomposition-placeholder busy">${icon('spark')}<h2>正在理解任务…</h2><p>会结合任务说明、原始要求与已有进度生成方案。</p></div>`;
 if(error)return `<div class="decomposition-placeholder error-state"><h2>暂时没有生成方案</h2><p>${esc(error)}</p><button class="btn primary" data-decomposition-action="start">重新尝试</button></div>`;
 if(applied)return `<div class="decomposition-placeholder applied">${icon('check')}<h2>步骤已经写入任务</h2><p>${esc(task?.title||'任务')}现在可以在任务中心逐项完成。</p><div class="decomposition-actions"><a class="btn primary" href="#tasks">查看任务</a><button class="btn" data-decomposition-action="reset">继续拆解</button></div></div>`;
 if(!result)return initialPanel(task);
 if(result.status==='needs_clarification')return questionsPanel(task);
 return planPanel(task);
}

export function decompositionPage(){
 if(selectedId&&!selected())resetDecomposition();
 return `<div class="page-heading"><div><h1>AI 任务拆解</h1><p>把复杂任务整理成可完成、可核对的小步骤。</p></div></div>
 <div class="decomposition-layout"><section class="panel decomposition-list"><div class="section-title"><h2>选择任务</h2><span>${tasks().length} 项未完成</span></div>${taskList()}</section>
 <section class="panel decomposition-work" aria-live="polite">${workPanel()}</section></div>`;
}

async function requestProposal(){
 if(!selected()||busy)return;
 const epoch=accountVersion(),taskId=selectedId,history=structuredClone(conversation),controller=new AbortController();
 const current=()=>epoch===accountVersion()&&selectedId===taskId&&Boolean(selected())&&activeController===controller&&!controller.signal.aborted;
 activeController=controller;busy=true;error='';result=null;resultTaskId='';applied=false;host.render();
 try{
  await flushStore();
  if(!current())return;
  const next=await api('/api/decomposition/proposals',{taskId,conversation:history},'POST',{timeoutMs:30000,signal:controller.signal});
  if(!current())return;
  if(next.taskId!==taskId)throw new Error('拆解方案与当前任务不匹配，请重新生成');
  result=next;resultTaskId=taskId;
 }catch(event){if(current())error=event.message;}
 finally{if(current()){activeController=null;busy=false;host.render();}}
}

async function applyProposal(requestId){
 if(!requestId||busy||!validResult()||result.status!=='ready_to_plan'||requestId!==result.requestId)return;
 const epoch=accountVersion(),taskId=selectedId,controller=new AbortController();
 const current=()=>epoch===accountVersion()&&selectedId===taskId&&Boolean(selected())&&activeController===controller&&!controller.signal.aborted;
 activeController=controller;busy=true;error='';host.render();
 try{
  await flushStore();
  if(!current()||!validResult()||result.requestId!==requestId)return;
  await api('/api/decomposition/apply',{requestId,taskId},'POST',{signal:controller.signal});
  if(!current())return;
  await host.refresh();
  if(!current())return;
  result=null;resultTaskId='';conversation=[];applied=true;host.toast('AI 拆解步骤已写入任务');
 }catch(event){if(current())error=event.message;}
 finally{if(current()){activeController=null;busy=false;host.render();}}
}

export function decompositionClick(button){
 const action=button.dataset.decompositionAction;
 if(!action)return false;
 if(action==='select'){
  resetDecomposition();selectedId=button.dataset.taskId;host.render();
 }else if(action==='reset'){
  resetDecomposition();host.render();
 }else if(action==='start'){
  if(!session.user){host.requireLogin();return true;}
  requestProposal();
 }else if(action==='apply')applyProposal(button.dataset.requestId);
 return true;
}

export function decompositionSubmit(form){
 if(form.id!=='decomposition-questions-form')return false;
 if(busy||!validResult()||result.status!=='needs_clarification')return true;
 const answers=[...form.querySelectorAll('input[name^="answer-"]')].map(input=>input.value.trim());
 if(answers.some(value=>!value)){host.toast('请回答所有问题');return true;}
 const questions=result.questions;
 conversation.push({role:'assistant',content:questions.map(item=>item.prompt).join('\n')});
 conversation.push({role:'user',content:questions.map((item,index)=>`${item.prompt}\n${answers[index]}`).join('\n\n')});
 requestProposal();return true;
}

window.addEventListener('niuma:account-reset',resetDecomposition);

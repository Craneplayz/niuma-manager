import {api,flushStore,saveData,workspaceValue,session,accountVersion,planDateTime} from './store.js';
import {icon} from './icons.js';

let host={},result=null,error='',busy=false,availability=null,activeController=null,exclusionsOpen=false,autoTaskIds=null;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaults={startTime:'19:00',endTime:'22:00',weekdays:[1,2,3,4,5]};
const active=()=>host.getState().tasks.filter(t=>t.status!=='done'&&!host.getState().schedule.some(p=>p.taskId===t.id));
function currentAvailability(){return availability||workspaceValue('availability',defaults);}
export function configurePlanner(callbacks){host=callbacks;}
const minutes=value=>`${Math.ceil(value)} 分钟`;
export function proposalSummaryHtml(proposal,tasks,now=Date.now()){
 const order=new Map((proposal.priorityOrder||[]).map(item=>[item.taskId,item]));
 const scheduledIds=new Set(proposal.blocks.map(block=>block.taskId));
 const displayOrder=new Map((proposal.priorityOrder||[]).filter(item=>scheduledIds.has(item.taskId)).map((item,index)=>[item.taskId,index+1]));
 const cards=Object.entries(proposal.estimates).map(([id,estimate])=>{
  const task=tasks.find(item=>item.id===id),rank=order.get(id);
  const scheduled=proposal.blocks.filter(block=>block.taskId===id).reduce((sum,block)=>sum+(Date.parse(block.endAt)-Date.parse(block.startAt))/60000,0);
  const remaining=Math.max(0,estimate-scheduled),due=task?.due||'',dueTime=task?.dueTime||'';
  const expired=due&&Date.parse(`${due}T${dueTime||'23:59:59'}+08:00`)<now;
  const status=!due?'date':expired?'expired':remaining>0.01?'short':'ready';
  const label={date:'待补日期',expired:'已过截止时间',short:'时间不足',ready:'已安排'}[status];
  const guidance={date:'补充截止日期后重新规划。',expired:'请核对日期；已完成的任务可标记完成。',short:`还差 ${minutes(remaining)}；可增加可用时间。`,ready:'确认后写入日历。'}[status];
  const reason=rank?(status==='expired'?'截止时间已过，无法安排。':`截止前可用 ${minutes(rank.availableMinutes)}，${rank.level<=1?'时间紧张，优先处理。':rank.level===2?'余量较少，优先安排。':'时间较充足。'}`):'';
  return {status,rank:rank?.rank??Infinity,html:`<article class="proposal-task ${status}"><div class="proposal-task-top"><span class="proposal-state">${label}</span>${displayOrder.has(id)?`<span class="proposal-rank">本次顺序 ${displayOrder.get(id)}</span>`:''}</div><h3>${esc(task?.title||id)}</h3><p class="proposal-task-meta">${due?`截止 ${esc(due)}${dueTime?' '+esc(dueTime):''}`:'尚无截止日期'} · 预计 ${minutes(estimate)}</p><p class="proposal-task-guidance">${guidance}</p>${reason?`<p class="proposal-task-reason">${esc(reason)}</p>`:''}</article>`};
 });
 const attention=cards.filter(card=>card.status!=='ready').sort((a,b)=>a.rank-b.rank),ready=cards.filter(card=>card.status==='ready').sort((a,b)=>a.rank-b.rank);
 const known=tasks.map(task=>task.title+'：');
 const otherWarnings=[...new Set(proposal.warnings.filter(warning=>!known.some(prefix=>warning.startsWith(prefix)&&/^(没有 DDL|P\d+，DDL 前尚缺|未全部安排)/.test(warning.slice(prefix.length)))))];
 return `<div class="proposal-intro"><strong>${ready.length} 项可安排${attention.length?` · ${attention.length} 项需要处理`:''}</strong><p>先安排截止前余量少的任务；同等风险按截止时间和任务优先级排序。</p></div>
 ${attention.length?`<section class="proposal-group"><h3>需要你处理</h3><div class="proposal-task-grid">${attention.map(card=>card.html).join('')}</div></section>`:''}
 ${ready.length?`<section class="proposal-group"><h3>本次建议安排</h3><div class="proposal-task-grid">${ready.map(card=>card.html).join('')}</div></section>`:''}
 ${otherWarnings.length?`<div class="proposal-extra" role="status">${otherWarnings.map(warning=>`<p>${esc(warning)}</p>`).join('')}</div>`:''}`;
}
window.addEventListener('niuma:account-reset',()=>{
 activeController?.abort();activeController=null;result=null;error='';busy=false;availability=null;exclusionsOpen=false;autoTaskIds=null;
});
document.addEventListener('toggle',event=>{
 if(event.target.isConnected&&event.target.classList?.contains('planning-exclusions'))exclusionsOpen=event.target.open;
},true);
export function plannerPage(){
 const tasks=active(),selected=tasks.filter(t=>!t.planningExcluded),a=currentAvailability();
 return `<div class="page-heading"><h1>智能规划</h1></div>
 <div class="planner-grid"><section class="panel priority-lab"><div class="section-title"><h2>可用时间</h2><span>北京时间</span></div>
 <div class="form-grid"><label>每日开始<input type="time" data-plan-field="startTime" value="${a.startTime}"></label><label>每日结束<input type="time" data-plan-field="endTime" value="${a.endTime}"></label></div>
 <div class="weekday-options">${[1,2,3,4,5,6,0].map(d=>`<label><input type="checkbox" data-plan-day="${d}" ${a.weekdays.includes(d)?'checked':''}>${['周日','周一','周二','周三','周四','周五','周六'][d]}</label>`).join('')}</div>
 <p class="panel-note">可用时间保存到账号；已有安排始终保留。</p></section>
 <section class="panel timeline-panel"><div class="section-title"><h2>规划范围</h2><span>${selected.length} 项待规划</span></div>
 <p>所有尚未完成、没有安排且未标记为“无需规划”的任务会一起送交模型。</p>
 <details class="planning-exclusions" ${exclusionsOpen?'open':''}><summary>无需规划的任务 · 已排除 ${tasks.length-selected.length} 项</summary>
 ${tasks.map(t=>`<label><input type="checkbox" data-plan-exclude="${esc(t.id)}" ${t.planningExcluded?'checked':''}><span>${esc(t.title)}</span><small>${t.due?`${t.due} ${t.dueTime||'待补充时刻'}`:'无 DDL'}</small></label>`).join('')||'<p>当前没有需要安排的任务。</p>'}</details>
 <div class="planner-actions"><button class="btn primary" data-plan-action="start" ${busy||!selected.length?'disabled':''}>${icon('spark')}${busy?'正在处理…':'开始规划'}</button>${busy?'<button class="btn" data-plan-action="cancel">取消等待</button>':''}</div></section></div>
 ${error?`<p class="save-error" role="alert">${esc(error)}</p>`:''}
 ${result?`<section class="panel planning-insights"><div class="section-title"><h2>排程提案</h2><span>确认后才写入日历</span></div>
 ${proposalSummaryHtml(result,host.getState().tasks)}
 ${result.blocks.length?`<section class="proposal-group"><h3>建议时间</h3><div class="proposal-list">${result.blocks.map(b=>{const start=planDateTime(b.startAt),end=planDateTime(b.endAt);return `<article class="plan-block"><b>${esc(b.title)}</b><span>${start.date} ${start.time}–${start.date===end.date?'':end.date+' '}${end.time}</span></article>`;}).join('')}</div></section>`:''}
 <div class="planner-actions"><button class="btn primary" data-plan-action="apply" ${busy||!result.blocks.length?'disabled':''}>确认写入日历</button><button class="btn" data-plan-action="modify" ${busy?'disabled':''}>修改可用时间</button><button class="btn" data-plan-action="discard" ${busy?'disabled':''}>取消提案</button></div></section>`:''}`;
}
export async function proposeForTasks(taskIds){
 if(!session.user||busy||!taskIds.length)return false;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 autoTaskIds=taskIds;result=null;error='';busy=true;host.openPreview?.();host.render();
 try{
  await flushStore();if(!current())return false;
  await saveData('availability',currentAvailability());if(!current())return false;
  const next=await api('/api/planning/proposals',{taskIds},'POST');
  if(!current())return false;
  result=next;
  host.openPreview?.();return true;
 }catch(e){if(current())error='任务已加入日历，但排程提案未生成：'+e.message;return false;}
 finally{if(current()){busy=false;host.render();}}
}
export async function plannerClick(button){
 const action=button.dataset.planAction;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 if(action==='cancel'){activeController?.abort();return true;}
 if(action==='discard'){result=null;autoTaskIds=null;error='';host.render();return true;}
 if(action==='modify'){result=null;host.render();return true;}
 if(!session.user||session.changing){error='请先登录账号';host.render();return true;}
 if(action==='start'){
  if(busy)return true;
  const a=currentAvailability();
  if(!a.weekdays.length||!a.startTime||a.endTime<=a.startTime){error='请选择有效可用时间和星期';host.render();return true;}
  busy=true;error='';result=null;host.render();
  const controller=new AbortController();activeController=controller;const timer=setTimeout(()=>controller.abort(),30000);
  try{
   await flushStore();if(!current())return true;
   await saveData('availability',a);if(!current())return true;
   if(controller.signal.aborted)throw new Error('已取消等待');
   const next=await api('/api/planning/proposals',autoTaskIds?{taskIds:autoTaskIds}:{},'POST',{signal:controller.signal});
   if(current())result=next;
  }
  catch(e){if(current())error=controller.signal.aborted?'已取消或请求超时；任务尚未变更':e.message;}
  finally{clearTimeout(timer);if(current()){activeController=null;busy=false;host.render();}}
  return true;
 }
 if(action==='apply'){
  if(busy||!result)return true;
  busy=true;error='';host.render();
  try{
   await flushStore();if(!current())return true;
   await api('/api/planning/apply',{requestId:result.requestId});if(!current())return true;
   result=null;autoTaskIds=null;await host.refresh();if(current())host.toast('规划已写入日历');
  }
  catch(e){if(current())error=e.message;}finally{if(current()){busy=false;host.render();}}
  return true;
 }
 return false;
}
export function plannerChange(event){
 const target=event.target;
 if(target.dataset.planField){availability={...currentAvailability(),[target.dataset.planField]:target.value};result=null;host.render();return true;}
 if(target.dataset.planDay!==undefined){const d=Number(target.dataset.planDay),a=currentAvailability();availability={...a,weekdays:target.checked?[...a.weekdays,d]:a.weekdays.filter(x=>x!==d)};result=null;host.render();return true;}
 if(target.dataset.planExclude){
  const task=host.getState().tasks.find(t=>t.id===target.dataset.planExclude);
  if(task){task.planningExcluded=target.checked;result=null;host.persist();host.render();}
  return true;
 }
 return false;
}
export function plannerSubmit(){return false;}
export function plannerApplyHours(){return false;}

import {api,flushStore,saveData,workspaceValue,session,accountVersion,planDateTime} from './store.js';
import {icon} from './icons.js';
import {pageIntro} from './story-layout.js';
import {priorityLabels} from './data.js';

let host={},result=null,resultContext=null,error='',busy=false,availability=null,activeController=null,exclusionsOpen=false,replanExisting=false,targetedIds=null;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaults={startTime:'19:00',endTime:'22:00',weekdays:[1,2,3,4,5]};
const active=()=>host.getState().tasks.filter(t=>t.status!=='done'&&(targetedIds?targetedIds.includes(t.id):replanExisting||!host.getState().schedule.some(p=>p.taskId===t.id)));
function currentAvailability(){return availability||workspaceValue('availability',defaults);}
const planningContext=()=>JSON.stringify([host.getState().tasks,host.getState().schedule,currentAvailability(),replanExisting,targetedIds]);
const changedMessage='任务或可用时间已更新，请重新规划。';
function invalidateChangedProposal(){
 if(result&&resultContext!==planningContext()){result=null;error=changedMessage;}
}
export function configurePlanner(callbacks){host=callbacks;}
const minutes=value=>`${Math.ceil(value)} 分钟`;
const riskLabels=['时间不足','余量极小','余量偏紧','余量充足'];
export function proposalSummaryHtml(proposal,tasks,now=Date.now()){
 const order=new Map((proposal.priorityOrder||[]).map(item=>[item.taskId,item]));
 const scheduledIds=new Set(proposal.blocks.map(block=>block.taskId));
 const displayOrder=new Map((proposal.priorityOrder||[]).filter(item=>scheduledIds.has(item.taskId)).map((item,index)=>[item.taskId,index+1]));
 const cards=Object.entries(proposal.estimates).map(([id,estimate])=>{
  const task=tasks.find(item=>item.id===id),rank=order.get(id);
  const priority=['high','medium','low'].includes(task?.priority)?task.priority:'unset';
  const scheduled=proposal.blocks.filter(block=>block.taskId===id).reduce((sum,block)=>sum+(Date.parse(block.endAt)-Date.parse(block.startAt))/60000,0);
  const remaining=Math.max(0,estimate-scheduled),due=task?.due||'',dueTime=task?.dueTime||'';
  const expired=due&&Date.parse(`${due}T${dueTime||'23:59:59'}+08:00`)<now;
  const status=!due?'date':expired?'expired':remaining>0.01?'short':'ready';
  const label={date:'待补日期',expired:'已过截止时间',short:'时间不足',ready:'已安排'}[status];
  const guidance={date:'补充截止日期后重新规划。',expired:'截止时间已过，无法安排；请核对日期或标记完成。',short:`还差 ${minutes(remaining)}；可增加可用时间。`,ready:'确认后写入日历。'}[status];
  const risk=rank&&Number.isInteger(rank.level)&&rank.level>=0&&rank.level<=3?rank.level:null;
  const reason=risk===null||status==='expired'?'':risk===0?`截止前可用 ${minutes(rank.availableMinutes)}，还缺 ${minutes(-rank.slackMinutes)}。`:`截止前可用 ${minutes(rank.availableMinutes)}，余量 ${minutes(rank.slackMinutes)}。`;
  return {status,rank:rank?.rank??Infinity,html:`<article class="proposal-task ${status}"><div class="proposal-task-top"><span class="proposal-state">${label}</span>${displayOrder.has(id)?`<span class="proposal-rank">排程优先第 ${displayOrder.get(id)} 位</span>`:''}</div><h3>${esc(task?.title||id)}</h3><p class="proposal-task-meta"><span class="proposal-priority ${priority}">${esc(priorityLabels[priority]||'优先级未设置')}</span>${risk!==null?`<span class="proposal-risk p${risk}" aria-label="自动风险 P${risk}，${riskLabels[risk]}">风险 P${risk} · ${riskLabels[risk]}</span>`:''}<span>${due?`截止 ${esc(due)}${dueTime?' '+esc(dueTime):''}`:'尚无截止日期'} · 预计 ${minutes(estimate)}</span></p><p class="proposal-task-guidance">${guidance}</p>${reason?`<p class="proposal-task-reason">${esc(reason)}</p>`:''}</article>`};
 });
 const attention=cards.filter(card=>card.status!=='ready').sort((a,b)=>a.rank-b.rank),ready=cards.filter(card=>card.status==='ready').sort((a,b)=>a.rank-b.rank);
 const known=tasks.map(task=>task.title+'：');
 const otherWarnings=[...new Set(proposal.warnings.filter(warning=>!known.some(prefix=>warning.startsWith(prefix)&&/^(没有 DDL|P\d+，DDL 前尚缺|未全部安排)/.test(warning.slice(prefix.length)))))];
 return `<div class="proposal-intro"><strong>${ready.length} 项可安排${attention.length?` · ${attention.length} 项需要处理`:''}</strong><p>任务优先级由你设置；排程先看截止前余量，同等风险再比较截止时间和任务优先级。</p></div>
 ${attention.length?`<section class="proposal-group"><h3>需要你处理</h3><div class="proposal-task-grid">${attention.map(card=>card.html).join('')}</div></section>`:''}
 ${ready.length?`<section class="proposal-group"><h3>本次建议安排</h3><div class="proposal-task-grid">${ready.map(card=>card.html).join('')}</div></section>`:''}
 ${otherWarnings.length?`<div class="proposal-extra" role="status">${otherWarnings.map(warning=>`<p>${esc(warning)}</p>`).join('')}</div>`:''}`;
}
window.addEventListener('niuma:account-reset',()=>{
 activeController?.abort();activeController=null;result=null;error='';busy=false;availability=null;exclusionsOpen=false;replanExisting=false;targetedIds=null;
});
document.addEventListener('toggle',event=>{
 if(event.target.isConnected&&event.target.classList?.contains('planning-exclusions'))exclusionsOpen=event.target.open;
},true);
export function plannerPage(){
 invalidateChangedProposal();
 const tasks=active(),selected=tasks.filter(t=>targetedIds||!t.planningExcluded),a=currentAvailability();
 return `${pageIntro('plan')}
 <div class="planner-grid"><section class="panel priority-lab"><div class="section-title"><h2>可用时间</h2><span>北京时间</span></div>
 <div class="form-grid"><label>每日开始<input type="time" data-plan-field="startTime" value="${a.startTime}"></label><label>每日结束<input type="time" data-plan-field="endTime" value="${a.endTime}"></label></div>
 <div class="weekday-options">${[1,2,3,4,5,6,0].map(d=>`<label><input type="checkbox" data-plan-day="${d}" ${a.weekdays.includes(d)?'checked':''}>${['周日','周一','周二','周三','周四','周五','周六'][d]}</label>`).join('')}</div>
 <p class="panel-note">可用时间保存到账号；规划预览不会修改当前安排。</p></section>
 <section class="panel timeline-panel"><div class="section-title"><h2>规划范围</h2><span>${selected.length} 项待规划</span></div>
 ${targetedIds?`<p class="planning-replacement-note">只重新规划：${selected.map(task=>esc(task.title)).join('、')}</p><button class="btn" data-plan-action="default-range" ${busy?'disabled':''}>恢复默认规划范围</button>`:`<label class="planning-replan-option"><input type="checkbox" data-plan-replan="true" ${replanExisting?'checked':''} ${busy?'disabled':''}><span>同时重新规划已有安排的任务<small>确认新提案后替换尚未开始的时段；已开始的安排保留。</small></span></label>`}
 <p>${targetedIds?'本次只处理上方选中的任务，其他任务的安排保持原值。':replanExisting?'所有尚未完成且未标记为“无需规划”的任务会一起送交模型。':'所有尚未完成、没有安排且未标记为“无需规划”的任务会一起送交模型。'}</p>
 ${targetedIds?'':`<details class="planning-exclusions" ${exclusionsOpen?'open':''}><summary>无需规划的任务 · 已排除 ${tasks.length-selected.length} 项</summary>
 ${tasks.map(t=>`<label><input type="checkbox" data-plan-exclude="${esc(t.id)}" ${t.planningExcluded?'checked':''}><span>${esc(t.title)}</span><small>${t.due?`${t.due} ${t.dueTime||'待补充时刻'}`:'无 DDL'}</small></label>`).join('')||'<p>当前没有需要安排的任务。</p>'}</details>`}
 <div class="planner-actions"><button class="btn primary" data-plan-action="start" ${busy||!selected.length?'disabled':''}>${icon('spark')}${busy?'正在处理…':'开始规划'}</button>${busy?'<button class="btn" data-plan-action="cancel">取消等待</button>':''}</div></section></div>
 ${error?`<p class="save-error" role="alert">${esc(error)}</p>`:''}
 ${result?`<section class="panel planning-insights"><div class="section-title"><h2>排程提案</h2><span>确认后才写入日历</span></div>
 ${result.replannedTaskIds?.length?`<p class="planning-replacement-note">确认后将替换以下任务尚未开始的安排：${result.replannedTaskIds.map(id=>esc(host.getState().tasks.find(task=>task.id===id)?.title||id)).join('、')}。未生成新时段的任务保留原安排。</p>`:''}
 ${proposalSummaryHtml(result,host.getState().tasks)}
 ${result.blocks.length?`<section class="proposal-group"><h3>建议时间</h3><div class="proposal-list">${result.blocks.map(b=>{const start=planDateTime(b.startAt),end=planDateTime(b.endAt);return `<article class="plan-block"><b>${esc(b.title)}</b><span>${start.date} ${start.time}–${start.date===end.date?'':end.date+' '}${end.time}</span></article>`;}).join('')}</div></section>`:''}
 <div class="planner-actions"><button class="btn primary" data-plan-action="apply" ${busy||!result.blocks.length?'disabled':''}>确认写入日历</button><button class="btn" data-plan-action="modify" ${busy?'disabled':''}>修改可用时间</button><button class="btn" data-plan-action="discard" ${busy?'disabled':''}>取消提案</button></div></section>`:''}`;
}
export async function proposeForTasks(taskIds){
 if(!session.user||busy||!taskIds.length)return false;
 targetedIds=null;replanExisting=false;
 return requestProposal({taskIds});
}
export async function replanTasks(taskIds){
 if(!session.user||busy||!taskIds.length)return false;
 targetedIds=[...taskIds];replanExisting=true;
 const a=currentAvailability();
 if(!a.weekdays.length||!a.startTime||a.endTime<=a.startTime){error='请选择有效可用时间和星期';host.openPreview?.();host.render();return false;}
 return requestProposal({taskIds,replan:true});
}
async function requestProposal(payload={}){
 const epoch=accountVersion(),controller=new AbortController();
 const context=planningContext();
 const current=()=>epoch===accountVersion()&&activeController===controller&&!controller.signal.aborted;
 activeController=controller;result=null;error='';busy=true;
 controller.signal.addEventListener('abort',()=>{
  if(epoch!==accountVersion()||activeController!==controller)return;
  activeController=null;busy=false;error='已取消或请求超时；尚未写入排程，已导入的任务保留。';host.render();
 },{once:true});
 const timer=setTimeout(()=>controller.abort(),30000);
 host.openPreview?.();host.render();
 try{
  await flushStore();if(!current())return false;
  await saveData('availability',currentAvailability());if(!current())return false;
  if(context!==planningContext())throw new Error(changedMessage);
  const next=await api('/api/planning/proposals',payload,'POST',{signal:controller.signal});
  if(!current())return false;
  if(context!==planningContext())throw new Error(changedMessage);
  resultContext=context;result=next;return true;
 }catch(e){if(current())error=(payload.replan?'重新规划未完成，原安排保留：':payload.taskIds?'任务已创建，但排程提案未生成：':'')+e.message;return false;}
 finally{clearTimeout(timer);if(current()){activeController=null;busy=false;host.render();}}
}
export async function plannerClick(button){
 const action=button.dataset.planAction;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 if(action==='cancel'){activeController?.abort();return true;}
 if(action==='discard'){result=null;error='';host.render();return true;}
 if(action==='modify'){result=null;host.render();return true;}
 if(action==='default-range'&&!busy){targetedIds=null;replanExisting=false;result=null;error='';host.render();return true;}
 if(!session.user||session.changing){error='请先登录账号';host.render();return true;}
 if(action==='start'){
  if(busy)return true;
  const a=currentAvailability();
  if(!a.weekdays.length||!a.startTime||a.endTime<=a.startTime){error='请选择有效可用时间和星期';host.render();return true;}
  await requestProposal(targetedIds?{taskIds:targetedIds,replan:true}:replanExisting?{replan:true}:{});
  return true;
 }
 if(action==='apply'){
  invalidateChangedProposal();
  if(!result){host.render();return true;}
  if(busy)return true;
  const proposal=result,context=resultContext;
  busy=true;error='';host.render();
  try{
   await flushStore();if(!current())return true;
   if(context!==planningContext()){result=null;throw new Error(changedMessage);}
   await api('/api/planning/apply',{requestId:proposal.requestId});if(!current())return true;
   result=null;await host.refresh();if(current())host.toast('规划已写入日历');
  }
  catch(e){if(current())error=e.message;}finally{if(current()){busy=false;host.render();}}
  return true;
 }
 return false;
}
export function plannerChange(event){
 const target=event.target;
 if(target.dataset.planReplan!==undefined){replanExisting=target.checked;result=null;host.render();return true;}
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

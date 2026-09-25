import {api,flushStore,saveData,workspaceValue,session,accountVersion,planDateTime} from './store.js';
import {icon} from './icons.js';

let host={},result=null,error='',busy=false,availability=null,activeController=null,exclusionsOpen=false;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaults={startTime:'19:00',endTime:'22:00',weekdays:[1,2,3,4,5]};
const active=()=>host.getState().tasks.filter(t=>t.status!=='done'&&!host.getState().schedule.some(p=>p.taskId===t.id));
function currentAvailability(){return availability||workspaceValue('availability',defaults);}
export function configurePlanner(callbacks){host=callbacks;}
window.addEventListener('niuma:account-reset',()=>{
 activeController?.abort();activeController=null;result=null;error='';busy=false;availability=null;exclusionsOpen=false;
});
document.addEventListener('toggle',event=>{
 if(event.target.isConnected&&event.target.classList?.contains('planning-exclusions'))exclusionsOpen=event.target.open;
},true);
export function plannerPage(){
 const tasks=active(),selected=tasks.filter(t=>!t.planningExcluded),a=currentAvailability();
 return `<div class="page-heading"><div><div class="eyebrow">NIUMA INTELLIGENT PLANNER</div><h1>让任务找到合适的时间。</h1><p>点击开始规划后，模型结合真实 DDL、已有安排和你的用时反馈评估并排程。</p></div></div>
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
 ${result?`<section class="panel planning-insights"><div class="section-title"><h2>模型规划预览</h2><span>确认后才写入日历</span></div>
 <div class="proposal-list">${Object.entries(result.estimates).map(([id,minutes])=>`<p><b>${esc(host.getState().tasks.find(t=>t.id===id)?.title||id)}</b> · 评估 ${minutes} 分钟</p>`).join('')}
 ${result.warnings.map(w=>`<p class="draft-warning">${esc(w)}</p>`).join('')}
 ${result.blocks.map(b=>{const start=planDateTime(b.startAt),end=planDateTime(b.endAt);return `<article class="plan-block"><b>${esc(b.title)}</b><span>${start.date} ${start.time}–${start.date===end.date?'':end.date+' '}${end.time}</span></article>`;}).join('')||'<p>模型未找到可安排的时段，请查看警告。</p>'}</div>
 <button class="btn primary" data-plan-action="apply" ${busy||!result.blocks.length?'disabled':''}>确认应用计划</button></section>`:''}`;
}
export async function plannerClick(button){
 const action=button.dataset.planAction;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 if(action==='cancel'){activeController?.abort();return true;}
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
   const next=await api('/api/planning/proposals',{},'POST',{signal:controller.signal});
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
   result=null;await host.refresh();if(current())host.toast('规划已写入日历');
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

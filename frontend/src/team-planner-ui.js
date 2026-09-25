import {api,flushStore,saveData,workspaceValue} from './store.js';
import {icon} from './icons.js';

let host={},result=null,error='',busy=false,availability=null,activeController=null;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaults={startTime:'19:00',endTime:'22:00',weekdays:[1,2,3,4,5]};
const active=()=>host.getState().tasks.filter(t=>t.status!=='done'&&!host.getState().schedule.some(p=>p.taskId===t.id));
function currentAvailability(){return availability||workspaceValue('availability',defaults);}
export function configurePlanner(callbacks){host=callbacks;}
export function plannerPage(){
 const tasks=active(),selected=tasks.filter(t=>!t.planningExcluded),a=currentAvailability();
 return `<div class="page-heading"><div><div class="eyebrow">NIUMA INTELLIGENT PLANNER</div><h1>让任务找到合适的时间。</h1><p>点击开始规划后，模型结合真实 DDL、已有安排和你的用时反馈评估并排程。</p></div></div>
 <div class="planner-grid"><section class="panel priority-lab"><div class="section-title"><h2>可用时间</h2><span>北京时间</span></div>
 <div class="form-grid"><label>每日开始<input type="time" data-plan-field="startTime" value="${a.startTime}"></label><label>每日结束<input type="time" data-plan-field="endTime" value="${a.endTime}"></label></div>
 <div class="weekday-options">${[1,2,3,4,5,6,0].map(d=>`<label><input type="checkbox" data-plan-day="${d}" ${a.weekdays.includes(d)?'checked':''}>${['周日','周一','周二','周三','周四','周五','周六'][d]}</label>`).join('')}</div>
 <p class="panel-note">可用时间保存到账号；已有安排始终保留。</p></section>
 <section class="panel timeline-panel"><div class="section-title"><h2>规划范围</h2><span>${selected.length} 项待规划</span></div>
 <p>所有尚未完成、没有安排且未标记为“无需规划”的任务会一起送交模型。</p>
 <details class="planning-exclusions"><summary>无需规划的任务 · 已排除 ${tasks.length-selected.length} 项</summary>
 ${tasks.map(t=>`<label><input type="checkbox" data-plan-exclude="${esc(t.id)}" ${t.planningExcluded?'checked':''}><span>${esc(t.title)}</span><small>${t.due?`${t.due} ${t.dueTime||'待补充时刻'}`:'无 DDL'}</small></label>`).join('')||'<p>当前没有需要安排的任务。</p>'}</details>
 <div class="planner-actions"><button class="btn primary" data-plan-action="start" ${busy||!selected.length?'disabled':''}>${icon('spark')}${busy?'正在处理…':'开始规划'}</button>${busy?'<button class="btn" data-plan-action="cancel">取消等待</button>':''}</div></section></div>
 ${error?`<p class="save-error" role="alert">${esc(error)}</p>`:''}
 ${result?`<section class="panel planning-insights"><div class="section-title"><h2>模型规划预览</h2><span>确认后才写入日历</span></div>
 <div class="proposal-list">${Object.entries(result.estimates).map(([id,minutes])=>`<p><b>${esc(host.getState().tasks.find(t=>t.id===id)?.title||id)}</b> · 评估 ${minutes} 分钟</p>`).join('')}
 ${result.warnings.map(w=>`<p class="draft-warning">${esc(w)}</p>`).join('')}
 ${result.blocks.map(b=>`<article class="plan-block"><b>${esc(b.title)}</b><span>${esc(b.startAt.slice(0,16).replace('T',' '))}–${esc(b.endAt.slice(11,16))}</span></article>`).join('')||'<p>模型未找到可安排的时段，请查看警告。</p>'}</div>
 <button class="btn primary" data-plan-action="apply" ${busy||!result.blocks.length?'disabled':''}>确认应用计划</button></section>`:''}`;
}
export async function plannerClick(button){
 const action=button.dataset.planAction;
 if(action==='cancel'){activeController?.abort();return true;}
 if(action==='start'){
  if(busy)return true;
  const a=currentAvailability();
  if(!a.weekdays.length||!a.startTime||a.endTime<=a.startTime){error='请选择有效可用时间和星期';host.render();return true;}
  busy=true;error='';result=null;host.render();
  activeController=new AbortController();const timer=setTimeout(()=>activeController?.abort(),30000);
  try{await flushStore();await saveData('availability',a);if(activeController.signal.aborted)throw new Error('已取消等待');result=await api('/api/planning/proposals',{},'POST',{signal:activeController.signal});}
  catch(e){error=activeController.signal.aborted?'已取消或请求超时；任务尚未变更':e.message;}
  finally{clearTimeout(timer);activeController=null;busy=false;host.render();}
  return true;
 }
 if(action==='apply'){
  if(busy||!result)return true;
  busy=true;error='';host.render();
  try{await api('/api/planning/apply',{requestId:result.requestId});result=null;await host.refresh();host.toast('规划已写入日历');}
  catch(e){error=e.message;}finally{busy=false;host.render();}
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

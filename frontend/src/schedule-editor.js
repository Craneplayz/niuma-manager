import {flushStore,retrySave,accountVersion} from './store.js';
import {replanTasks} from './team-planner-ui.js';
import {occupiedSchedule} from './data.js';

let host={};
const originals=new WeakMap();
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=value=>String(Math.floor(value)).padStart(2,'0')+':'+String(Math.round(value%1*60)).padStart(2,'0');
export function configureScheduleEditor(callbacks){host=callbacks;}
export function scheduleTimes(item){
 const end=new Date(Date.parse(`${item.date}T${clock(item.start)}:00+08:00`)+item.hours*3600000);
 const parts=Object.fromEntries(new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(end).map(part=>[part.type,part.value]));
 return {date:item.date,start:clock(item.start),endDate:`${parts.year}-${parts.month}-${parts.day}`,end:`${parts.hour}:${parts.minute}`};
}
export function editedSchedule(item,values,schedule,tasks=[]){
 const startAt=Date.parse(`${values.date}T${values.start}:00+08:00`),endAt=Date.parse(`${values.endDate}T${values.end}:00+08:00`);
 if(!Number.isFinite(startAt)||!Number.isFinite(endAt)||endAt<=startAt)throw new Error('结束时间必须晚于开始时间');
 for(const other of occupiedSchedule(schedule,tasks)){
  if(other.id===item.id&&other.taskId===item.taskId)continue;
  const otherStart=Date.parse(`${other.date}T${clock(other.start)}:00+08:00`);
  if(startAt<otherStart+other.hours*3600000&&otherStart<endAt)throw new Error('这个时段与其他学习安排重叠，请调整时间');
 }
 return {...item,date:values.date,start:Number(values.start.slice(0,2))+Number(values.start.slice(3))/60,hours:(endAt-startAt)/3600000,label:values.label.trim()||item.label};
}
function openEditor(taskId,id){
 const state=host.getState(),item=state.schedule.find(plan=>plan.taskId===taskId&&plan.id===id),task=state.tasks.find(task=>task.id===taskId);
 if(!item||!task)return host.toast('这项安排已经不存在，请刷新日历');
 const times=scheduleTimes(item);
 host.openDialog(`<form id="schedule-form" data-plan-id="${esc(id)}" data-task-id="${esc(taskId)}">${host.dialogHeader('调整学习时段')}
 <p class="schedule-task-name">${esc(task.title)}</p><p class="panel-note">时间均为北京时间。这里调整学习安排，任务截止时间保持原值。</p>
 <label>安排名称<input name="label" required maxlength="100" value="${esc(item.label||task.title)}"></label>
 <div class="form-grid"><label>开始日期<input type="date" name="date" required value="${times.date}"></label><label>开始时间<input type="time" name="start" required value="${times.start}"></label><label>结束日期<input type="date" name="endDate" required value="${times.endDate}"></label><label>结束时间<input type="time" name="end" required value="${times.end}"></label></div>
 <p id="schedule-error" class="error" role="alert"></p><div class="schedule-secondary-actions"><button type="button" class="btn" data-schedule-edit-task="${esc(taskId)}">任务详情</button><button type="button" class="btn" data-schedule-replan="${esc(taskId)}" ${task.status==='done'?'disabled':''}>重新规划这项任务</button></div>
 <p class="panel-note">重新规划将先生成预览，确认后替换这项任务尚未开始的安排；已开始的时段保留。</p>
 <div class="dialog-actions"><button type="button" class="btn danger" data-schedule-remove>移除这个时段</button><button type="submit" class="btn primary">保存时段</button></div></form>`);
 originals.set(document.querySelector('#schedule-form'),{item:structuredClone(item),index:state.schedule.indexOf(item)});
}
export function scheduleEditorClick(button){
 if(button.dataset.scheduleId){openEditor(button.dataset.scheduleTask,button.dataset.scheduleId);return true;}
 if(button.dataset.scheduleEditTask){host.editTask(button.dataset.scheduleEditTask);return true;}
 if(button.dataset.scheduleReplan){
  const id=button.dataset.scheduleReplan;
  host.openDialog(`${host.dialogHeader('重新规划这项任务？')}<p>会为这项任务重新生成学习时段。当前安排会保留到你确认新提案；取消或生成失败时保留原安排。</p><div class="dialog-actions"><button type="button" class="btn" data-action="close">取消</button><button type="button" class="btn primary" data-schedule-confirm-replan="${esc(id)}">生成预览</button></div>`);return true;
 }
 if(button.dataset.scheduleConfirmReplan){host.closeDialog();void replanTasks([button.dataset.scheduleConfirmReplan]);return true;}
 if(button.hasAttribute('data-schedule-remove')){void saveEdit(button.closest('form'),true);return true;}
 return false;
}
async function saveEdit(form,remove=false){
 const epoch=accountVersion(),state=host.getState(),index=state.schedule.findIndex(plan=>plan.id===form.dataset.planId&&plan.taskId===form.dataset.taskId),error=form.querySelector('#schedule-error');
 if(index<0&&!form.dataset.removed){error.textContent='这项安排已经不存在，请刷新日历';return;}
 const fields=[...form.querySelectorAll('button,input')];error.textContent='';
 try{
  if(remove&&!form.dataset.removed){state.schedule.splice(index,1);form.dataset.removed='true';}
  else if(form.dataset.removed)remove=true;
  else state.schedule[index]=editedSchedule(state.schedule[index],Object.fromEntries(new FormData(form)),state.schedule,state.tasks);
  fields.forEach(field=>field.disabled=true);
  host.persist();await retrySave();await flushStore();
  if(epoch!==accountVersion())return;
  const original=originals.get(form);
  host.closeDialog();host.render();host.toast(remove?'学习时段已移除，任务仍保留':'学习时段已保存',remove&&original?()=>{
   const current=host.getState();
   if(current.tasks.some(task=>task.id===original.item.taskId)&&!current.schedule.some(plan=>plan.id===original.item.id&&plan.taskId===original.item.taskId))current.schedule.splice(Math.min(original.index,current.schedule.length),0,original.item);
  }:null);
 }catch(cause){if(epoch===accountVersion()){error.textContent='尚未保存：'+cause.message;fields.forEach(field=>field.disabled=false);}}
}
export function scheduleEditorSubmit(form){if(form.id!=='schedule-form')return false;void saveEdit(form);return true;}

import {icon} from './icons.js';
import {pageIntro} from './story-layout.js';
import {dayKey} from './data.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let view='week',selectedDay=dayKey();
const pad=n=>String(n).padStart(2,'0');
const weekdays='一二三四五六日';
const dateAt=(date,days)=>{const result=new Date(date+'T12:00:00');result.setDate(result.getDate()+days);return dayKey(result);};
const monday=date=>{const d=new Date(date+'T12:00:00');return dateAt(date,-((d.getDay()+6)%7));};
const hour=n=>pad(Math.floor(n))+':'+pad(Math.round((n%1)*60));
export function calendarPage(state,week){
 const completed=new Set(state.tasks.filter(task=>task.status==='done').map(task=>task.id)),now=Date.now();
 state={...state,schedule:state.schedule.filter(plan=>!completed.has(plan.taskId)||Date.parse(plan.date+'T00:00:00+08:00')+plan.start*3600000<=now)};
 const current=dayKey(),start=dateAt(monday(current),week*7);
 const dates=view==='day'?[selectedDay]:view==='week'?Array.from({length:7},(_,i)=>dateAt(start,i)):Array.from({length:42},(_,i)=>dateAt(monday(selectedDay.slice(0,7)+'-01'),i));
 const ddl=state.tasks.filter(t=>t.status!=='done'&&t.due);
 const unknown=ddl.filter(t=>!t.dueTime&&dates.includes(t.due));
 const title=view==='month'?selectedDay.slice(0,7).replace('-',' 年 ')+' 月':view==='day'?selectedDay:start.replaceAll('-','.')+' — '+dates.at(-1).slice(5).replace('-','.');
 if(view!=='month')requestAnimationFrame(()=>{const grid=document.querySelector('.calendar-time-grid');if(grid)grid.scrollTop=10*52;});
 return `${pageIntro('calendar','<button class="btn" data-action="new">新建任务</button><button class="btn primary" data-action="plan">'+icon('spark')+'开始规划</button>')}
 <section class="panel"><div class="section-title"><h2>${title}</h2><div class="button-row"><button class="btn" data-action="prev">上一${view==='day'?'日':view==='month'?'月':'周'}</button><button class="btn" data-action="thisweek">今天</button><button class="btn" data-action="next">下一${view==='day'?'日':view==='month'?'月':'周'}</button></div></div>
 <div class="calendar-view-tabs">${[['day','日'],['week','周'],['month','月']].map(([value,label])=>`<button class="btn ${view===value?'primary':''}" data-calendar-view="${value}">${label}</button>`).join('')}</div>
 ${unknown.length?`<div class="calendar-unknown"><b>待补充截止时刻</b>${unknown.map(t=>`<button data-edit="${esc(t.id)}">${esc(t.title)} · ${esc(t.due)} · ${esc(t.source||'手动创建')}</button>`).join('')}</div>`:''}
 ${view==='month'?monthGrid(dates,state):timeGrid(dates,state)}
 </section>`;
}
function monthGrid(dates,state){
 return `<div class="calendar-month-grid">${weekdays.split('').map(d=>`<b>周${d}</b>`).join('')}
 ${dates.map(date=>`<div class="calendar-month-day ${date===dayKey()?'is-today':''}"><button class="calendar-day-link" data-calendar-day="${date}" aria-label="查看 ${date} 日程">${Number(date.slice(-2))}</button>
 ${state.tasks.filter(t=>t.status!=='done'&&t.due===date).map(t=>`<button class="calendar-month-ddl" data-edit="${esc(t.id)}"><small>DDL ${t.dueTime||'待补时刻'} · ${esc(t.source||'手动创建')}</small>${esc(t.title)}</button>`).join('')}
 ${state.schedule.filter(s=>s.date===date).map(s=>`<button class="calendar-month-plan" data-schedule-id="${esc(s.id)}" data-schedule-task="${esc(s.taskId)}"><small>${hour(s.start)}</small>${esc(s.label||'执行计划')}</button>`).join('')}</div>`).join('')}</div>`;
}
function timeGrid(dates,state){
 const axis=Array.from({length:24},(_,h)=>`<span>${pad(h)}:00</span>`).join('');
 return `<div class="calendar-time-grid ${view==='day'?'single':''}" tabindex="0" aria-label="每天的时间线，默认从 10:00 开始，可向上滚到 00:00">
 <div class="calendar-time-axis"><b>时间</b><span></span>${axis}</div>
 ${dates.map(date=>`<div class="calendar-time-day ${date===dayKey()?'is-today':''}"><header><button class="calendar-day-link" data-calendar-day="${date}" aria-label="查看 ${date} 日程"><span>周${weekdays[(new Date(date+'T12:00:00').getDay()+6)%7]}</span><b>${Number(date.slice(-2))}</b></button></header>
 ${state.schedule.filter(s=>s.date===date).map(s=>event(s.taskId,s.label||state.tasks.find(t=>t.id===s.taskId)?.title||'执行计划',hour(s.start),s.start,'plan',hour((s.start+s.hours)%24),s.id)).join('')}
 ${state.tasks.filter(t=>t.status!=='done'&&t.due===date&&t.dueTime).map(t=>event(t.id,t.title,t.dueTime,Number(t.dueTime.slice(0,2))+Number(t.dueTime.slice(3,5))/60,'ddl','',t.source)).join('')}</div>`).join('')}</div>`;
}
function event(id,title,time,start,kind,end='',source=''){
 const top=64+start*52;
 const action=kind==='plan'?`data-schedule-id="${esc(source)}" data-schedule-task="${esc(id)}"`:`data-edit="${esc(id)}"`;
 return `<button class="calendar-timed-event ${kind}" ${action} style="top:${top}px" aria-label="${kind==='ddl'?'截止':'安排'} ${esc(time)} ${esc(title)}"><small>${kind==='ddl'?'DDL · ':'计划 · '}${esc(time)}${end?'–'+esc(end):''}${kind==='ddl'&&source?' · '+esc(source):''}</small><b>${esc(title)}</b></button>`;
}
export function calendarClick(button,rerender){
 if(button.dataset.calendarView){view=button.dataset.calendarView;selectedDay=dayKey();rerender();return true;}
 if(button.dataset.calendarDay){selectedDay=button.dataset.calendarDay;view='day';rerender();return true;}
 return false;
}
export function calendarStep(direction,week){
 if(view==='week')return week+direction;
 if(view==='day'){selectedDay=dateAt(selectedDay,direction);return week;}
 const date=new Date(selectedDay+'T12:00:00');date.setDate(1);date.setMonth(date.getMonth()+direction);selectedDay=dayKey(date);return week;
}
export function calendarToday(){selectedDay=dayKey();return 0;}

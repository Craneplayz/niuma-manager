import {transitionView} from './view-motion.js';
import {icon} from './icons.js';
import {pageIntro} from './story-layout.js';
import {dayKey,occupiedSchedule} from './data.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let view='week',selectedDay=dayKey(),highlightedPlans=new Set();
export function focusCalendar(date,planIds=[]){selectedDay=date;view='day';highlightedPlans=new Set(planIds);return Math.round((Date.parse(monday(date)+'T12:00:00')-Date.parse(monday(dayKey())+'T12:00:00'))/(7*86400000));}
export function resetCalendarFocus(){highlightedPlans.clear();}
const highlight=id=>highlightedPlans.has(id)?' data-schedule-highlight="true"':'';
const pad=n=>String(n).padStart(2,'0');
const weekdays='一二三四五六日';
const dateAt=(date,days)=>{const result=new Date(date+'T12:00:00');result.setDate(result.getDate()+days);return dayKey(result);};
const monday=date=>{const d=new Date(date+'T12:00:00');return dateAt(date,-((d.getDay()+6)%7));};
const hour=n=>pad(Math.floor(n))+':'+pad(Math.round((n%1)*60));
export function calendarPage(state,week){
 state={...state,schedule:occupiedSchedule(state.schedule,state.tasks)};
 const current=dayKey(),start=dateAt(monday(current),week*7);
 const dates=view==='day'?[selectedDay]:view==='week'?Array.from({length:7},(_,i)=>dateAt(start,i)):Array.from({length:42},(_,i)=>dateAt(monday(selectedDay.slice(0,7)+'-01'),i));
 const ddl=state.tasks.filter(t=>t.status!=='done'&&t.due);
 const unknown=ddl.filter(t=>!t.dueTime&&dates.includes(t.due));
 const title=view==='month'?selectedDay.slice(0,7).replace('-',' 年 ')+' 月':view==='day'?selectedDay:start.replaceAll('-','.')+' — '+dates.at(-1).slice(5).replace('-','.');
 if(view!=='month')requestAnimationFrame(()=>{const grid=document.querySelector('.calendar-time-grid');if(grid){const event=grid.querySelector('[data-schedule-highlight]');grid.scrollTop=event?Math.max(0,parseFloat(event.style.top)-100):10*52;}});
 return `${pageIntro('calendar','<button class="btn" data-action="new">新建任务</button><button class="btn primary" data-action="plan">'+icon('spark')+'开始规划</button>')}
 <section class="panel calendar-panel"><div class="section-title"><h2>${title}</h2><div class="button-row"><button class="btn" data-action="prev">上一${view==='day'?'日':view==='month'?'月':'周'}</button><button class="btn" data-action="thisweek">今天</button><button class="btn" data-action="next">下一${view==='day'?'日':view==='month'?'月':'周'}</button></div></div>
 <div class="calendar-view-tabs">${[['day','日'],['week','周'],['month','月']].map(([value,label])=>`<button class="btn ${view===value?'primary':''}" data-calendar-view="${value}">${label}</button>`).join('')}</div>
 ${highlightedPlans.size?'<p class="calendar-arrival" role="status">已定位本次写入的安排，可点击时段调整。<button class="inline-button" data-calendar-clear>取消高亮</button></p>':''}
 ${unknown.length?`<div class="calendar-unknown"><b>待补充截止时刻</b>${unknown.map(t=>`<button data-edit="${esc(t.id)}">${esc(t.title)} · ${esc(t.due)} · ${esc(t.source||'手动创建')}</button>`).join('')}</div>`:''}
 ${view==='month'?monthGrid(dates,state):timeGrid(dates,state)}
 </section>`;
}
function monthGrid(dates,state){
 return `<div class="calendar-month-grid">${weekdays.split('').map(d=>`<b>周${d}</b>`).join('')}
 ${dates.map(date=>`<div class="calendar-month-day ${date===dayKey()?'is-today':''}"><button class="calendar-day-link" data-calendar-day="${date}" aria-label="查看 ${date} 日程">${Number(date.slice(-2))}</button>
 ${state.tasks.filter(t=>t.status!=='done'&&t.due===date).map(t=>`<button class="calendar-month-ddl" data-edit="${esc(t.id)}"><small>DDL ${t.dueTime||'待补时刻'} · ${esc(t.source||'手动创建')}</small>${esc(t.title)}</button>`).join('')}
 ${state.schedule.filter(s=>s.date===date).map(s=>`<button class="calendar-month-plan" data-schedule-id="${esc(s.id)}" data-schedule-task="${esc(s.taskId)}"${highlight(s.id)}><small>${hour(s.start)}</small>${esc(s.label||'执行计划')}</button>`).join('')}</div>`).join('')}</div>`;
}
function timeGrid(dates,state){
 const axis=Array.from({length:24},(_,h)=>`<span>${pad(h)}:00</span>`).join('');
 return `<div class="calendar-time-grid ${view==='day'?'single':''}" tabindex="0" aria-label="${highlightedPlans.size?'每天的时间线，已定位本次写入的安排，可上下滚动':'每天的时间线，默认从 10:00 开始，可向上滚到 00:00'}">
 <div class="calendar-time-axis"><b>时间</b><span></span>${axis}</div>
 ${dates.map(date=>`<div class="calendar-time-day ${date===dayKey()?'is-today':''}"><header><button class="calendar-day-link" data-calendar-day="${date}" aria-label="查看 ${date} 日程"><span>周${weekdays[(new Date(date+'T12:00:00').getDay()+6)%7]}</span><b>${Number(date.slice(-2))}</b></button></header>
 ${state.schedule.filter(s=>s.date===date).map(s=>event(s.taskId,s.label||state.tasks.find(t=>t.id===s.taskId)?.title||'执行计划',hour(s.start),s.start,'plan',hour((s.start+s.hours)%24),s.id)).join('')}
 ${state.tasks.filter(t=>t.status!=='done'&&t.due===date&&t.dueTime).map(t=>event(t.id,t.title,t.dueTime,Number(t.dueTime.slice(0,2))+Number(t.dueTime.slice(3,5))/60,'ddl','',t.source)).join('')}</div>`).join('')}</div>`;
}
function event(id,title,time,start,kind,end='',source=''){
 const top=64+start*52;
 const action=kind==='plan'?`data-schedule-id="${esc(source)}" data-schedule-task="${esc(id)}"`:`data-edit="${esc(id)}"`;
 return `<button class="calendar-timed-event ${kind}" ${action}${kind==='plan'?highlight(source):''} style="top:${top}px" aria-label="${kind==='ddl'?'截止':'安排'} ${esc(time)} ${esc(title)}"><small>${kind==='ddl'?'DDL · ':'计划 · '}${esc(time)}${end?'–'+esc(end):''}${kind==='ddl'&&source?' · '+esc(source):''}</small><b>${esc(title)}</b></button>`;
}
export function calendarClick(button,rerender){
 if(button.hasAttribute?.('data-calendar-clear')){highlightedPlans.clear();rerender();return true;}
 if(button.dataset.calendarView){transitionView(()=>{view=button.dataset.calendarView;if(!highlightedPlans.size)selectedDay=dayKey();rerender();},{scope:'calendar'});return true;}
 if(button.dataset.calendarDay){transitionView(()=>{selectedDay=button.dataset.calendarDay;view='day';rerender();},{scope:'calendar'});return true;}
 return false;
}
export function calendarStep(direction,week){
 highlightedPlans.clear();
 if(view==='week')return week+direction;
 if(view==='day'){selectedDay=dateAt(selectedDay,direction);return week;}
 const date=new Date(selectedDay+'T12:00:00');date.setDate(1);date.setMonth(date.getMonth()+direction);selectedDay=dayKey(date);return week;
}
export function calendarToday(){highlightedPlans.clear();selectedDay=dayKey();return 0;}

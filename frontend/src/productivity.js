import {isBlocked} from './data.js';

export function selectTasks(tasks,{filter='all',query='',sort='due',today}={}) {
 const weights={high:0,medium:1,low:2};
 const dueCompare=(a,b)=>(a.due||'9999-12-31').localeCompare(b.due||'9999-12-31');
 return tasks.filter(task=>{
  const match=filter==='all'||task.status===filter||
   (filter==='today'&&task.status!=='done'&&task.due===today)||
   (filter==='overdue'&&task.status!=='done'&&!!task.due&&task.due<today)||
   (filter==='blocked'&&task.status!=='done'&&isBlocked(task,tasks));
  return match&&(task.title+' '+task.course+' '+task.notes).toLowerCase().includes(query.toLowerCase().trim());
 }).sort((a,b)=>{
  const completion=Number(a.status==='done')-Number(b.status==='done');
  if(completion)return completion;
  if(sort==='priority')return weights[a.priority]-weights[b.priority]||dueCompare(a,b);
  if(sort==='duration')return (a.hours>0?a.hours:Infinity)-(b.hours>0?b.hours:Infinity)||dueCompare(a,b);
  return dueCompare(a,b)||(a.dueTime||'23:59').localeCompare(b.dueTime||'23:59')||weights[a.priority]-weights[b.priority];
 });
}
export function remainingSeconds(timer,now=Date.now()) {
 return timer.running?Math.max(0,Math.ceil((timer.endsAt-now)/1000)):Math.max(0,timer.remaining);
}
export function formatTimer(seconds) {
 return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
}
export function searchCommands(tasks,query) {
 const q=query.toLowerCase().trim();
 return tasks.filter(t=>(t.title+' '+t.course).toLowerCase().includes(q)).slice(0,7);
}

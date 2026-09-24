import type { Task } from './App'

// Adapted from Pan Yongzhen's productivity interactions; see ../CONTRIBUTIONS.md.
export function selectTasks(tasks:Task[], {query='',status='all',window='all',sort='due',today=''}: {query?:string;status?:string;window?:string;sort?:string;today?:string}={}) {
  const weights={high:0,medium:1,low:2}, q=query.trim().toLowerCase()
  return tasks.filter(t=>(status==='all'?t.status!=='cancelled':t.status===status)
    && (window==='all'||(t.status!=='completed'&&t.status!=='cancelled'&&!!t.due&&(window==='today'?t.due.date===today:t.due.date<today)))
    && [t.title,t.description,t.course||'',...t.subtasks.map(s=>s.title)].join(' ').toLowerCase().includes(q))
    .sort((a,b)=>(sort==='priority'?weights[a.priority]-weights[b.priority]:0)
      || (a.due?.date||'9999').localeCompare(b.due?.date||'9999')
      || (a.due?.time||'23:59').localeCompare(b.due?.time||'23:59') || weights[a.priority]-weights[b.priority])
}
export type FocusState={duration:number;remaining:number;running:boolean;endsAt:number;taskId:string}
export const initialFocus:FocusState={duration:1500,remaining:1500,running:false,endsAt:0,taskId:''}
export const remainingSeconds=(timer:FocusState,now=Date.now())=>timer.running?Math.min(timer.duration,Math.max(0,Math.ceil((timer.endsAt-now)/1000))):timer.remaining
export const formatTimer=(seconds:number)=>`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`

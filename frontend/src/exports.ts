import type { Task } from './App'

const escapeText=(value:string)=>value.replace(/\\/g,'\\\\').replace(/\r\n|\r|\n/g,'\\n').replace(/,/g,'\\,').replace(/;/g,'\\;')
const utc=(value:string)=>new Date(value).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'')
// UTF-8-safe line folding follows Pan Yongzhen's calendar export implementation.
export function foldLine(line:string) {
  let result='',bytes=0
  for(const char of line){const size=new TextEncoder().encode(char).length;if(bytes+size>75){result+='\r\n ';bytes=1}result+=char;bytes+=size}
  return result
}
export function exportCalendar(tasks:Task[],now=new Date().toISOString()) {
  const lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Niuma Manager//Calendar//ZH','CALSCALE:GREGORIAN']
  for(const task of tasks.filter(t=>t.status!=='cancelled')) {
    const event=(id:string,title:string,start:string,end?:string)=>{
      lines.push('BEGIN:VEVENT','UID:'+encodeURIComponent(task.id+'-'+id)+'@niuma-manager','DTSTAMP:'+utc(now),'SUMMARY:'+escapeText(title),'DESCRIPTION:'+escapeText(task.description),start)
      if(end)lines.push(end)
      lines.push('END:VEVENT')
    }
    if(task.due){const d=task.due;event('deadline','DDL · '+task.title,d.precision==='datetime'&&d.time?'DTSTART:'+utc(d.date+'T'+d.time+':00+08:00'):'DTSTART;VALUE=DATE:'+d.date.replace(/-/g,''))}
    for(const plan of task.plans)event(plan.id,'计划 · '+plan.title,'DTSTART:'+utc(plan.startAt),plan.endAt?'DTEND:'+utc(plan.endAt):undefined)
  }
  lines.push('END:VCALENDAR')
  return lines.map(foldLine).join('\r\n')+'\r\n'
}
export function downloadFile(name:string,text:string,type:string) {
  const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a')
  a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}

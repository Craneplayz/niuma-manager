import type { Task } from './App'
export type Notice = 'create'|'update'|'postpone'|'cancel'|'uncertain'
export type Deadline = {value:string|null;precision:'date'|'datetime'|'unknown';timezone:string|null;kind:'deadline'|'reference'|'unknown';status:'explicit'|'inferred'|'unknown'|'conflict'|'not_provided';raw:string|null;basis:string;alternatives:string[]}
export type TextCandidate = {candidate_id:string;input_id:string;title:string;description:string;prerequisites:string[];course_or_project:string|null;course_basis:'provided'|'inferred'|'unknown';notice_type:Notice;target_reference:string|null;deadline:Deadline;needs_review:boolean;review_reasons:string[];sources:{input_id:string;platform:string;url:string|null;label:string|null;source_item_id:string|null}[];evidence:{input_id:string;field:string;quote:string;supports:string[]}[]}
export type StoredCandidate = {id:string;candidate:TextCandidate;state:'pending'|'applied'|'ignored';version:number}
export type TextBatch = {demo:boolean;request:{request_id:string;records:{input_id:string;text:string;text_quality:string;published_at:{value:string|null;precision:string;basis:string};context:{input_id:string;text:string}[]}[]};result:{status:'success'|'no_tasks'|'partial_success'|'failed';record_results:{input_id:string;status:string;task_ids:string[];warnings:string[]}[];errors:{input_id:string|null;code:string;message:string;retryable:boolean}[]};items:StoredCandidate[]}
export const noticeLabels:Record<Notice,string>={create:'新增',update:'更新',postpone:'延期',cancel:'取消',uncertain:'性质待核实'}
export const batchLabels={success:'处理成功',no_tasks:'已处理的文本中未识别到待办',partial_success:'部分处理成功',failed:'处理失败'}
export function deadlineLabel(d:Deadline) {
  if(d.status==='not_provided')return '原文未提供截止日期（可保留无 DDL）'
  if(d.status==='conflict')return '截止时间存在冲突，请核实原文与候选时间'
  if(d.kind==='reference')return '参考时间（不是真实 DDL）：'+(d.value||d.raw||'待核实')
  if(d.status==='unknown')return '截止日期待核实'
  return (d.status==='inferred'?'推断截止时间（待核实）：':'明确截止时间：')+d.value
}
/** Only an explicit deadline is prefilled; users still must explicitly confirm it. */
export function suggestedDue(d:Deadline):Task['due'] {
  if(d.kind!=='deadline'||d.status!=='explicit'||!d.value)return null
  if(d.precision==='date')return {date:d.value,precision:'date'}
  if(d.precision!=='datetime'||!Number.isFinite(Date.parse(d.value)))return null
  if(new Date(d.value).getUTCSeconds()!==0||new Date(d.value).getUTCMilliseconds()!==0)return null
  const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(d.value))
  const p=Object.fromEntries(parts.map(x=>[x.type,x.value]))
  return {date:p.year+'-'+p.month+'-'+p.day,time:p.hour+':'+p.minute,precision:'datetime'}
}
export function safeSourceUrl(value:string|null) {
  try { const u=new URL(value||'');return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null } catch{return null}
}

import type { Task, Plan } from './App'

export type Availability = { startDate: string; endDate: string; startTime: string; endTime: string; weekdays: number[]; focusMinutes: number; dailyMinutes: number }
export type PlanningRequest = {
  schemaVersion: '1.0'; requestId: string; requestedAt: string; timezone: 'Asia/Shanghai';
  availability: Availability;
  tasks: Array<{ id: string; title: string; description: string; due: Task['due']; priority: Task['priority']; estimatedMinutes: number }>;
  existingPlans: Array<Plan & { taskId: string }>;
}
export type Proposal = { taskId: string; title: string; startAt: string; endAt: string }
export type PlanningResult = { requestId: string; blocks: Proposal[]; warnings: string[] }
export const eligible = (tasks: Task[]) => tasks.filter(t => !['completed', 'cancelled'].includes(t.status) && t.plans.length === 0)
const minutes = (time: string) => Number(time.slice(0,2)) * 60 + Number(time.slice(3,5))
const instant = (day: string, minute: number) => `${day}T${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}:00+08:00`
const overlaps = (a: string, b: string, c: string, d: string) => Date.parse(a) < Date.parse(d) && Date.parse(c) < Date.parse(b)
export const dueLimit = (due: Task['due']) => !due ? Infinity : Date.parse(`${due.date}T${due.precision === 'datetime' ? due.time + ':00' : '23:59:59'}+08:00`)
export function validateAvailability(a: Availability) {
  if (!a.startDate || !a.endDate || a.endDate < a.startDate) throw new Error('请选择有效的规划日期范围')
  if ((Date.parse(a.endDate)-Date.parse(a.startDate))/86400000 > 60) throw new Error('单次规划范围最多 60 天')
  if (!a.startTime || !a.endTime || a.endTime <= a.startTime) throw new Error('可用结束时间必须晚于开始时间')
  if (!a.weekdays.length) throw new Error('至少选择一个可用星期')
  if (!Number.isFinite(a.focusMinutes) || a.focusMinutes < 15 || a.focusMinutes > 240 || !Number.isFinite(a.dailyMinutes) || a.dailyMinutes < 15 || a.dailyMinutes > 1440) throw new Error('请填写有效的专注时长和每日上限')
}

/** Local deterministic substitute; never presented as a real LLM response. */
export function localPlan(request: PlanningRequest): PlanningResult {
  validateAvailability(request.availability)
  const a = request.availability
  const remaining = new Map(request.tasks.map(t => [t.id, t.estimatedMinutes]))
  const tasks = [...request.tasks].sort((x,y) => dueLimit(x.due)-dueLimit(y.due) || ({high:0,medium:1,low:2}[x.priority]-{high:0,medium:1,low:2}[y.priority]))
  const blocks: Proposal[] = []
  for (let date = Date.parse(a.startDate+'T00:00:00Z'); date <= Date.parse(a.endDate+'T00:00:00Z'); date += 86400000) {
    const day = new Date(date).toISOString().slice(0,10)
    if (!a.weekdays.includes(new Date(date).getUTCDay())) continue
    let used = 0
    for (let minute=minutes(a.startTime); minute<minutes(a.endTime);) {
      const task = tasks.find(t => (remaining.get(t.id)||0)>0 && dueLimit(t.due)>Date.parse(instant(day,minute)))
      if (!task || used >= a.dailyMinutes) break
      const duration = Math.min(remaining.get(task.id)!, a.focusMinutes, a.dailyMinutes-used, minutes(a.endTime)-minute)
      const startAt = instant(day,minute), endAt = instant(day,minute+duration)
      if (Date.parse(startAt) < Date.parse(request.requestedAt) || Date.parse(endAt)>dueLimit(task.due) || request.existingPlans.some(p=>overlaps(startAt,endAt,p.startAt,p.endAt || p.startAt))) { minute+=15; continue }
      blocks.push({taskId:task.id,title:task.title,startAt,endAt}); remaining.set(task.id,remaining.get(task.id)!-duration); used+=duration; minute+=duration
    }
  }
  return {requestId:request.requestId,blocks,warnings:tasks.filter(t=>(remaining.get(t.id)||0)>0).map(t=>`${t.title}：还有 ${remaining.get(t.id)} 分钟无法在当前约束内安排`)}
}

export function validateResult(request: PlanningRequest, value: unknown): PlanningResult {
  const r = value as PlanningResult
  if (!r || r.requestId !== request.requestId || !Array.isArray(r.blocks) || !Array.isArray(r.warnings) || !r.warnings.every(w=>typeof w==='string')) throw new Error('规划服务返回格式不正确')
  const totals = new Map<string,number>(), days = new Map<string,number>()
  for (const [index,b] of r.blocks.entries()) {
    const task = request.tasks.find(t=>t.id===b.taskId)
    if (!task || typeof b.title !== 'string' || !b.title.trim() || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:00\+08:00$/.test(b.startAt) || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:00\+08:00$/.test(b.endAt)) throw new Error('规划结果包含未知任务或无效时间')
    const start=Date.parse(b.startAt), end=Date.parse(b.endAt), day=b.startAt.slice(0,10), a=request.availability
    const duration=(end-start)/60000
    if (!Number.isFinite(start+end) || duration<=0 || duration>a.focusMinutes || start<Date.parse(request.requestedAt) || end>dueLimit(task.due) || day<a.startDate || day>a.endDate || b.endAt.slice(0,10)!==day || b.startAt.slice(11,16)<a.startTime || b.endAt.slice(11,16)>a.endTime || !a.weekdays.includes(new Date(day+'T00:00:00Z').getUTCDay())) throw new Error('规划结果违反可用时间、专注时长或真实 DDL，请重新规划')
    if ([...request.existingPlans,...r.blocks.slice(0,index)].some(p=>overlaps(b.startAt,b.endAt,p.startAt,p.endAt||p.startAt))) throw new Error('规划结果与现有安排或其他建议冲突')
    totals.set(task.id,(totals.get(task.id)||0)+duration); days.set(day,(days.get(day)||0)+duration)
    if (totals.get(task.id)!>task.estimatedMinutes || days.get(day)!>a.dailyMinutes) throw new Error('规划结果超过任务工时或每日上限')
  }
  const warnings = [...r.warnings]
  request.tasks.forEach(t=>{ const left=t.estimatedMinutes-(totals.get(t.id)||0); if(left>0) warnings.push(`${t.title}：未安排 ${left} 分钟`) })
  return {...r,warnings:[...new Set(warnings)]}
}

export async function requestPlanning(request: PlanningRequest, signal: AbortSignal) {
  const base = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/,'')
  if (!base) { await new Promise<void>((resolve,reject)=> {const timer=setTimeout(resolve,350); signal.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Cancelled','AbortError'))},{once:true})}); return validateResult(request,localPlan(request)) }
  const response = await fetch(`${base}/planning/proposals`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','Idempotency-Key':request.requestId},body:JSON.stringify(request),signal})
  if (!response.ok) throw new Error(response.status===401?'规划服务需要登录，请与后端团队确认会话接入':`规划请求失败（${response.status}），请重试`)
  return validateResult(request,await response.json())
}

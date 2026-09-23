import { useEffect, useRef, useState } from 'react'
import type { Task } from './App'
import { eligible, requestPlanning, validateAvailability, type Availability, type PlanningRequest, type PlanningResult } from './planning'
import { useUserData } from './account'

export default function Planner({userId,tasks,onClose,onApply}:{userId:string;tasks:Task[];onClose:()=>void;onApply:(result:PlanningResult,snapshot:string)=>boolean}) {
  const date = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date())
  const end = new Date(Date.parse(date+'T00:00:00Z')+13*86400000).toISOString().slice(0,10)
  const [saved,savePreferences,storageError] = useUserData<Availability>(userId,'availability',{startDate:date,endDate:end,startTime:'19:00',endTime:'22:00',weekdays:[1,2,3,4,5],focusMinutes:60,dailyMinutes:120})
  const [a,setA]=useState<Availability>({...saved,startDate:date,endDate:end})
  const pending=eligible(tasks)
  const [hours,setHours]=useState<Record<string,number>>(Object.fromEntries(pending.map(t=>[t.id,(t.estimatedMinutes||60)/60])))
  const [selected,setSelected]=useState(pending.map(t=>t.id))
  const [busy,setBusy]=useState(false), [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [result,setResult]=useState<PlanningResult|null>(null)
  const [snapshot,setSnapshot]=useState('')
  const controller=useRef<AbortController|null>(null)
  useEffect(()=>()=>controller.current?.abort(),[])
  const change=(patch:Partial<Availability>)=>{setA({...a,...patch});setResult(null)}
  return <div className="modal-layer"><section className="editor planning-editor" role="dialog" aria-modal="true" aria-label="开始规划">
    <header className="editor-head"><div><p className="eyebrow">独立规划 · Asia/Shanghai</p><h2>把未安排任务放进可用时间</h2></div><button className="icon-button" aria-label="关闭规划" onClick={()=>{controller.current?.abort();onClose()}}>×</button></header>
    <div className="form-scroll">
      <p className="local-note">{import.meta.env.VITE_API_BASE_URL?'将所选任务、真实 DDL、当前安排和可用时间发送给规划服务。':'本地演示规划器（未连接大模型）：按截止日期和可用时间生成建议。'} 确认前不会修改任务或日历。</p>
      <fieldset disabled={busy}><legend>1. 可用时间</legend><div className="form-grid">
        <label>开始日期<input type="date" value={a.startDate} onChange={e=>change({startDate:e.target.value})}/></label><label>结束日期<input type="date" value={a.endDate} onChange={e=>change({endDate:e.target.value})}/></label>
        <label>每日开始<input type="time" value={a.startTime} onChange={e=>change({startTime:e.target.value})}/></label><label>每日结束<input type="time" value={a.endTime} onChange={e=>change({endTime:e.target.value})}/></label>
        <label>单次专注（分钟）<input type="number" min="15" max="240" value={a.focusMinutes} onChange={e=>change({focusMinutes:Number(e.target.value)})}/></label><label>每日新增计划上限（分钟）<input type="number" min="15" max="1440" value={a.dailyMinutes} onChange={e=>change({dailyMinutes:Number(e.target.value)})}/></label>
      </div><div className="weekday-options">{[1,2,3,4,5,6,0].map(d=><label key={d}><input type="checkbox" checked={a.weekdays.includes(d)} onChange={()=>change({weekdays:a.weekdays.includes(d)?a.weekdays.filter(x=>x!==d):[...a.weekdays,d]})}/>{['周日','周一','周二','周三','周四','周五','周六'][d]}</label>)}</div>
      <button className="secondary-button" onClick={()=>{setError('');setNotice('');try{validateAvailability(a);if(savePreferences(a))setNotice('可用时间偏好已保存')}catch(e){setError((e as Error).message)}}}>保存为默认偏好</button>
      {notice&&<p role="status">{notice}</p>}
      </fieldset>
      <fieldset disabled={busy}><legend>2. 未安排任务（{pending.length}）</legend><p className="muted">仅列出未完成且没有执行计划的主任务。没有预计工时的任务默认 1 小时，请核对。</p>
      {pending.map(t=><div className="planning-task" key={t.id}><label><input type="checkbox" checked={selected.includes(t.id)} onChange={()=>{setSelected(selected.includes(t.id)?selected.filter(id=>id!==t.id):[...selected,t.id]);setResult(null)}}/><span>{t.title}<small>真实 DDL：{t.due?`${t.due.date} ${t.due.time||'仅日期'}`:'未设置'}</small></span></label><label>预计小时<input aria-label={`${t.title}预计小时`} type="number" min="0.25" step="0.25" max="1000" value={hours[t.id]} onChange={e=>{setHours({...hours,[t.id]:Number(e.target.value)});setResult(null)}}/></label></div>)}
      {!pending.length&&<p>没有未安排任务。先创建任务并确认真实 DDL，再回来规划。</p>}</fieldset>
      <details><summary>会一起提交的现有安排（{tasks.reduce((n,t)=>n+t.plans.length,0)}）</summary>{tasks.flatMap(t=>t.plans.map(p=><p key={p.id}>{t.title} · {p.startAt.replace('T',' ')} — {p.endAt?.replace('T',' ')}{p.isLocked?' · 已固定':''}</p>))}<p className="muted">现有安排保持不变，并作为不可重叠的占用时段。</p></details>
      {(error||storageError)&&<p className="form-error" role="alert">{storageError||error}</p>}
      {result&&<section className="proposal"><h3>3. 规划预览 · 新增 {result.blocks.length} 个时段</h3>{result.warnings.map((w,i)=><p className="warning" key={i}>{w}</p>)}{result.blocks.map((b,i)=><article key={i}><strong>{b.title}</strong><p>{b.startAt.slice(0,10)} · {b.startAt.slice(11,16)}–{b.endAt.slice(11,16)}</p></article>)}<p>真实 DDL 和已有安排均保持不变。确认后新增时段才进入日历。</p></section>}
    </div>
    <footer className="editor-footer">{busy?<button className="secondary-button" onClick={()=>controller.current?.abort()}>取消等待</button>:<button className="secondary-button" disabled={!selected.length} onClick={async()=>{
      setError('');setResult(null)
      try {
        validateAvailability(a)
        const chosen=pending.filter(t=>selected.includes(t.id))
        if(!chosen.length||chosen.some(t=>!Number.isFinite(hours[t.id])||hours[t.id]<=0||hours[t.id]>1000))throw new Error('请选择任务并填写有效预计工时')
        const request:PlanningRequest={schemaVersion:'1.0',requestId:crypto.randomUUID(),requestedAt:new Date().toISOString(),timezone:'Asia/Shanghai',availability:a,tasks:chosen.map(t=>({id:t.id,title:t.title,description:t.description,due:t.due,priority:t.priority,estimatedMinutes:Math.round(hours[t.id]*60)})),existingPlans:tasks.flatMap(t=>t.plans.map(p=>({...p,taskId:t.id})))}
        setSnapshot(JSON.stringify(tasks));setBusy(true);controller.current=new AbortController()
        const timeout=window.setTimeout(()=>controller.current?.abort(),30000)
        let next: PlanningResult
        try { next=await requestPlanning(request,controller.current.signal) }
        finally { clearTimeout(timeout) }
        if(!controller.current.signal.aborted)setResult(next)
      }catch(e){setError((e as Error).name==='AbortError'?'已取消或请求超时，任务保持不变；可以重新生成':(e as Error).message)}finally{setBusy(false)}
    }}>{result?'重新生成':'开始规划'}</button>}
    {result&&<button className="primary-button" disabled={!result.blocks.length||busy} onClick={()=>{if(onApply(result,snapshot))onClose();else setError('任务数据已变化或保存失败，请重新生成规划')}}>确认应用计划</button>}</footer>
  </section></div>
}

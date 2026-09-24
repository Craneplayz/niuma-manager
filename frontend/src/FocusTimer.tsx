import { useEffect, useState } from 'react'
import type { Task } from './App'
import { useUserData } from './account'
import { initialFocus, remainingSeconds, formatTimer } from './productivity'

export default function FocusTimer({userId,tasks}:{userId:string;tasks:Task[]}) {
  const [timer,save,error,refresh]=useUserData(userId,'focus',initialFocus)
  const [now,setNow]=useState(Date.now()),[busy,setBusy]=useState(false),[open,setOpen]=useState(false),[refreshError,setRefreshError]=useState('')
  useEffect(()=>{const id=window.setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(id)},[])
  const seconds=remainingSeconds(timer,now),running=timer.running&&seconds>0
  const update=async(next:typeof timer)=>{setBusy(true);try{await save(next)}finally{setBusy(false)}}
  return <section className="focus-widget" aria-label="专注计时">
    <button className="secondary-button" aria-expanded={open} onClick={()=>setOpen(!open)}>专注 · {formatTimer(seconds)}{running?' · 进行中':''}</button>
    {open&&<div className="panel focus-panel"><div className="panel-title"><h2>留一段专注时间</h2><button className="icon-button" aria-label="收起专注计时" onClick={()=>setOpen(false)}>×</button></div>
      <div className="focus-clock" role="timer">{formatTimer(seconds)}</div><p className="subtle">{seconds===0?'本段计时结束，休息一下吧。':running?'一次只做一件事。':'准备好就开始。'}</p>
      <fieldset disabled={busy} className="recognition-fields"><div className="button-row">{[25,50].map(n=><button key={n} className="secondary-button" disabled={running} onClick={()=>update({...timer,duration:n*60,remaining:n*60,running:false,endsAt:0})}>{n} 分钟</button>)}</div>
      <label>关联任务<select disabled={running} value={timer.taskId} onChange={e=>update({...timer,taskId:e.target.value})}><option value="">自由专注</option>{tasks.filter(t=>!['completed','cancelled'].includes(t.status)||t.id===timer.taskId).map(t=><option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
      <div className="button-row"><button className="primary-button" onClick={()=>update(running?{...timer,running:false,remaining:remainingSeconds(timer),endsAt:0}:{...timer,running:true,remaining:seconds||timer.duration,endsAt:Date.now()+(seconds||timer.duration)*1000})}>{busy?'保存中…':running?'暂停':seconds===0?'再来一段':'开始 / 继续'}</button><button className="secondary-button" onClick={()=>update({...timer,running:false,remaining:timer.duration,endsAt:0})}>重置</button></div></fieldset>
      {(error||refreshError)&&<p role="alert" className="form-error">{error||refreshError}</p>}<button className="text-button" disabled={busy} onClick={async()=>{try{await refresh();setRefreshError('')}catch(e){setRefreshError((e as Error).message)}}}>读取服务器最新计时</button>
      <p className="local-note">计时状态保存在账号数据库，切换页面不停止。到时不会自动完成任务、记录实际用时或发送系统通知；实际用时请在个性化中核对填写。</p>
    </div>}
  </section>
}

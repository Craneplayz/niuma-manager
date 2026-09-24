import { useState } from 'react'
import type { Task } from './App'
import { useUserData } from './account'
type Feedback={taskId:string;actualMinutes:number;recordedAt:string}
export default function Personalization({userId,tasks}:{userId:string;tasks:Task[]}) {
  const [feedback,save,error]=useUserData<Feedback[]>(userId,'feedback',[])
  const [sample,setSample]=useState<Task[]>([]),[minutes,setMinutes]=useState<Record<string,string>>({})
  const [message,setMessage]=useState(''),[busy,setBusy]=useState(false)
  const completed=tasks.filter(t=>t.status==='completed')
  return <section className="panel personalization"><div className="panel-title"><div><h2>个性化</h2><span>让模型逐渐了解你的实际节奏</span></div><button className="secondary-button" disabled={!completed.length||busy} onClick={()=>{const shuffled=[...completed];for(let i=shuffled.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[shuffled[i],shuffled[j]]=[shuffled[j],shuffled[i]]}setSample(shuffled.slice(0,3));setMinutes({});setMessage('')}}>反馈任务用时</button></div><p className="local-note">每次随机抽取最多 3 个已完成任务。记录真实用时后，下一次规划会把这些记录交给模型参考，不会修改你的原计划。</p><p className="muted">已记录 {feedback.length} 个任务{!completed.length?'；完成任务后即可反馈。':''}</p>
  {sample.length>0&&<form onSubmit={async e=>{e.preventDefault();setBusy(true);setMessage('');try{const next=sample.map(t=>({taskId:t.id,actualMinutes:Number(minutes[t.id]),recordedAt:new Date().toISOString()}));if(next.some(f=>!Number.isFinite(f.actualMinutes)||f.actualMinutes<=0||f.actualMinutes>100000)){setMessage('请输入有效分钟数');return}if(await save([...feedback.filter(f=>!sample.some(t=>t.id===f.taskId)),...next])){setSample([]);setMessage('已保存，下次规划将参考这些用时。')}}finally{setBusy(false)}}}>{sample.map(t=><label className="feedback-row" key={t.id}><span>{t.title}</span><input type="number" min="1" max="100000" required aria-label={t.title+'实际分钟'} placeholder="实际分钟" value={minutes[t.id]||''} onChange={e=>setMinutes({...minutes,[t.id]:e.target.value})}/></label>)}<button className="primary-button" disabled={busy}>保存反馈</button></form>}{error&&<p className="form-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}</section>
}

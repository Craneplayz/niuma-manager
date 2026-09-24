import { ArrowRight, CalendarDays, Check, Sparkles } from 'lucide-react'
import type { Task } from './App'
import { selectTasks } from './productivity'

// Visual composition adapted from Pan Yongzhen's premium hero (see CONTRIBUTIONS.md).
export default function StudyHero({tasks,onCollect,onPlan,onOpen}:{tasks:Task[];onCollect:()=>void;onPlan:()=>void;onOpen:(task:Task)=>void}) {
  const next=selectTasks(tasks.filter(t=>!['completed','cancelled'].includes(t.status)))[0]
  const active=tasks.filter(t=>t.status!=='cancelled'),completed=active.filter(t=>t.status==='completed').length
  const percent=active.length?Math.round(completed/active.length*100):0
  return <section className="study-hero" aria-label="学习工作台">
    <div className="hero-copy"><p className="hero-tag"><Sparkles size={14}/> NIUMA · YOUR STUDY SPACE</p><h2>想做的事很多。<br/><span>从容，一件件来。</span></h2><p className="hero-description">先记下任务，核对真实截止时间。<br/>把安排交给规划，把精力留给学习。</p><div className="hero-actions"><button className="primary-button" onClick={onCollect}>整理任务 <ArrowRight size={16}/></button><button className="text-button" onClick={onPlan}>开始规划 <ArrowRight size={14}/></button></div></div>
    <div className="hero-visual"><div className="hero-glow" aria-hidden="true"/><div className="hero-stack-back" aria-hidden="true"/><button className="hero-stack" onClick={()=>next?onOpen(next):onCollect()} aria-label={next?'查看下一项任务：'+next.title:'添加第一项任务'}><span className="stack-top"><span><Sparkles size={13}/> 一步一步来</span><span aria-hidden="true">•••</span></span><span className="stack-label">YOUR NEXT STEP</span><strong>{next?.title||'从第一个小目标开始。'}</strong><span className="stack-course">{next?.course||next?.category||'记下想做的事'}</span><span className="stack-deadline"><CalendarDays size={14}/>{next?.due?`${next.due.date}${next.due.time?' '+next.due.time:''} 截止`:'没有截止日期，也值得被安排'}<Check size={14}/></span><span className="stack-progress"><i style={{width:percent+'%'}}/></span><span className="stack-footer"><span>任务完成进度</span><span>{percent}%</span></span></button><div className="hero-caption"><span className="hero-waves" aria-hidden="true"><i/><i/><i/><i/><i/></span><span>每一步，都算数。</span></div></div>
  </section>
}

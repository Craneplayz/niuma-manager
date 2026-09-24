import { useState } from 'react'
import { useUserData } from './account'
import { safeSourceUrl } from './recognition'
import type { Task } from './App'

type Course={id:string;name:string;teacher:string;notes:string;url:string}
export default function Courses({userId,tasks,onOpen}:{userId:string;tasks:Task[];onOpen:(task:Task)=>void}) {
  const [courses,save,error]=useUserData<Course[]>(userId,'courses',[])
  const [draft,setDraft]=useState<Course|null>(null),[busy,setBusy]=useState(false)
  return <><div className="page-heading"><div><p className="eyebrow">学习资料</p><h1>课程与笔记</h1><p className="subtle">将课程资料链接、教师信息和手写笔记收在一起。</p></div><button className="primary-button" onClick={()=>setDraft({id:crypto.randomUUID(),name:'',teacher:'',notes:'',url:''})}>添加课程</button></div>
    {error&&<p role="alert" className="form-error">{error}</p>}
    {draft&&<section className="panel course-editor"><h2>{courses.some(c=>c.id===draft.id)?'编辑课程':'新课程'}</h2><form className="recognition-fields" onSubmit={async e=>{e.preventDefault();setBusy(true);try{const next={...draft,name:draft.name.trim(),url:draft.url.trim()};if(await save(old=>old.some(c=>c.id===next.id)?old.map(c=>c.id===next.id?next:c):[...old,next]))setDraft(null)}finally{setBusy(false)}}}><fieldset disabled={busy} className="recognition-fields">
      <label>课程名称<input required maxLength={120} value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/></label><label>教师 / 联系人<input maxLength={120} value={draft.teacher} onChange={e=>setDraft({...draft,teacher:e.target.value})}/></label>
      <label>资料 / 回放链接<input type="url" placeholder="https://…" maxLength={2000} value={draft.url} onChange={e=>setDraft({...draft,url:e.target.value})}/></label><label>课程笔记<textarea rows={6} maxLength={20000} value={draft.notes} onChange={e=>setDraft({...draft,notes:e.target.value})}/></label><div className="button-row"><button className="primary-button" disabled={!draft.name.trim()||!!draft.url&&!safeSourceUrl(draft.url.trim())}>保存课程</button><button type="button" className="secondary-button" onClick={()=>setDraft(null)}>取消</button></div></fieldset></form></section>}
    {!courses.length&&!draft&&<section className="panel"><p className="subtle">还没有课程。添加后可保存笔记和资料链接；与任务的课程名称一致时会显示关联任务。</p></section>}
    <div className="course-grid">{courses.map(c=><article className="panel" key={c.id}><div className="panel-title"><div><h2>{c.name}</h2><span>{c.teacher||'未填写教师'}</span></div><button className="text-button" onClick={()=>setDraft({...c})}>编辑</button></div>{safeSourceUrl(c.url)&&<a href={safeSourceUrl(c.url)!} target="_blank" rel="noopener noreferrer">打开资料 / 回放 ↗</a>}<p className="course-notes">{c.notes||'暂时没有笔记'}</p><details><summary>关联任务（{tasks.filter(t=>t.course===c.name).length}）</summary>{tasks.filter(t=>t.course===c.name).map(t=><button className="course-task text-button" key={t.id} onClick={()=>onOpen(t)}>{t.title}</button>)}</details></article>)}</div>
  </>
}

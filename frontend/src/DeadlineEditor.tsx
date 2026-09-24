import { useState } from 'react'
import type { Task } from './App'

export default function DeadlineEditor({due,onSave}:{due:Task['due'];onSave:(due:Task['due'])=>Promise<boolean>}) {
  const [open,setOpen]=useState(true),[date,setDate]=useState(due?.date||''),[time,setTime]=useState(due?.time||''),[busy,setBusy]=useState(false),[error,setError]=useState('')
  return <section className="detail-section deadline-editor">
    <button className="secondary-button" aria-expanded={open} onClick={()=>{setDate(due?.date||'');setTime(due?.time||'');setError('');setOpen(!open)}} disabled={busy}>修改真实截止时间</button>
    {open&&<form onSubmit={async e=>{e.preventDefault();if(date&&!time)return;setBusy(true);setError('');try{const next:Task['due']=date?{date,precision:time?'datetime':'date',...(time?{time}:{})}:null;if(await onSave(next))setOpen(false);else setError('未能保存截止时间，请检查页面错误提示后重试。')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}}>
      <fieldset disabled={busy} className="form-grid"><label>真实 DDL 日期<input type="date" value={date} onChange={e=>{setDate(e.target.value);if(!e.target.value)setTime('')}}/></label><label>真实 DDL 具体时间<input type="time" step="60" required={!!date} value={time} disabled={!date} onChange={e=>setTime(e.target.value)}/></label></fieldset>
      <p className="local-note">填写截止日期时，请同时填写具体时刻；清空日期表示无 DDL。修改截止时间不会自动改动已有执行计划。</p>
      {error&&<p role="alert" className="form-error">{error}</p>}<div className="button-row"><button className="primary-button" disabled={busy}>{busy?'保存中…':'保存截止时间'}</button><button type="button" className="secondary-button" disabled={busy} onClick={()=>setOpen(false)}>取消修改</button></div>
    </form>}
  </section>
}

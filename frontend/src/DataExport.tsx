import { useState } from 'react'
import { api } from './account'
import { downloadFile, exportCalendar } from './exports'

export default function DataExport() {
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('')
  const run=async(kind:'json'|'ics')=>{setBusy(true);setMessage('');try{
    const workspace=await api('/workspace')
    if(kind==='ics')downloadFile('niuma-calendar.ics',exportCalendar(workspace.tasks?.value||[]),'text/calendar;charset=utf-8')
    else{const batches=await api('/recognition/batches');downloadFile('niuma-workspace.json',JSON.stringify({format:'niuma-export-1',exportedAt:new Date().toISOString(),workspace,recognition:batches},null,2),'application/json')}
    setMessage(kind==='ics'?'已导出 DDL 与执行计划，可手动导入外部日历。':'已导出账号业务数据，不包含密码、密钥或会话。')
  }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  return <section className="panel data-export"><div className="panel-title"><div><h2>数据与日历导出</h2><span>参考潘勇圳的备份与外部日历交互</span></div></div><div className="button-row"><button className="secondary-button" disabled={busy} onClick={()=>run('json')}>导出 JSON 数据</button><button className="secondary-button" disabled={busy} onClick={()=>run('ics')}>导出 .ics 日历</button></div>{message&&<p role="status">{message}</p>}<p className="local-note">文件可能包含原文和个人笔记，请妥善保存。导出不上传到第三方，不是双向同步；暂不支持 JSON 恢复导入。日期型 DDL 保持全天，精确时间按 +08:00 转为 UTC。</p></section>
}

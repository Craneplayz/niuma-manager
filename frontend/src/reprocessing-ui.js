import {newId} from './id.js';
import {api,session,accountVersion} from './store.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const root='/api/recognition/reprocessing/';
const labels={queued:'排队中',running:'识别中',stopping:'正在停止',succeeded:'已完成',partial_success:'部分完成',failed:'识别失败',stopped:'已停止',interrupted:'已中断',cancelled:'未处理',superseded:'已被新运行替代'};
let host={},accounts=[],runs=[],selected=new Set(),source='',loaded=false,busy=false,error='',timer=null,pendingOperation=null;
export function configureReprocessing(callbacks){host=callbacks;}
function active(){return ['#sources','#settings'].includes(location.hash);}
function reset(){clearTimeout(timer);accounts=[];runs=[];selected.clear();source='';loaded=false;busy=false;error='';pendingOperation=null;}
window.addEventListener('niuma:account-reset',reset);
window.addEventListener('hashchange',()=>{clearTimeout(timer);if(active()&&loaded)void load(false);});
function render(){if(active())host.render?.();}
async function pages(path,key){let cursor=null,items=[];do{const page=await api(root+path+(cursor?'?cursor='+encodeURIComponent(cursor):''));items.push(...page[key]);cursor=page.next_cursor;}while(cursor);return items;}
async function load(directory=true){
 const epoch=accountVersion(),before=new Map(runs.map(run=>[run.run_id,run.state]));
 try{
  const list=await pages('runs','runs'),entries=directory?await pages('sources','accounts'):null;
  if(epoch!==accountVersion())return;
  runs=list;
  if(entries){const merged=new Map();for(const entry of entries){const old=merged.get(entry.source_account_id);if(old)old.records.push(...entry.records);else merged.set(entry.source_account_id,entry);}accounts=[...merged.values()];if(!accounts.some(a=>a.source_account_id===source)){source=accounts[0]?.source_account_id||'';selected.clear();}}
  loaded=true;error='';
  if(runs.some(run=>['queued','running','stopping'].includes(before.get(run.run_id))&&!['queued','running','stopping'].includes(run.state)))await host.finished?.();
  if(epoch!==accountVersion())return;
  if(runs.some(run=>['queued','running','stopping'].includes(run.state))&&active()){clearTimeout(timer);timer=setTimeout(()=>load(false),2000);}
 }catch(e){if(epoch===accountVersion())error=e.message;}
 finally{if(epoch===accountVersion()){busy=false;render();}}
}
export function reprocessingPanel(){
 if(session.user&&!loaded&&!busy&&!error){busy=true;queueMicrotask(()=>load());}
 const records=(accounts.find(a=>a.source_account_id===source)?.records||[]).map(record=>({...record,result_status:({success:'已识别',no_tasks:'无任务',failed:'识别失败',unknown:'尚未识别'})[record.result_status]}));
 return `<section class="agent-source-sync"><h2>重新识别已有通知</h2><button type="button" class="btn" data-reprocess-action="load" ${busy||!session.user?'disabled':''}>${loaded?'刷新记录':'选择已保存通知'}</button>${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${loaded?`${accounts.length>1?`<label>来源<select id="reprocess-source">${accounts.map(a=>`<option value="${esc(a.source_account_id)}" ${a.source_account_id===source?'selected':''}>${esc(a.label)}</option>`).join('')}</select></label>`:''}${records.length?`<div class="source-import-list">${records.map((record,index)=>`<label class="source-import-message"><input type="checkbox" data-reprocess-index="${index}" ${selected.has(record.input_id)?'checked':''} ${busy?'disabled':''}><span>${esc(record.label)}<small>${esc(record.course_or_project||'')} · ${esc(record.result_status)}<details><summary>规则版本</summary>${esc(record.policy_version||'历史版本未知')}</details></small></span></label>`).join('')}</div><button type="button" class="btn primary" data-reprocess-action="start" ${busy||!selected.size?'disabled':''}>重新识别所选 ${selected.size} 条</button>`:'<p>暂无已保存的教学网通知。</p>'}`:''}${runs.slice(0,5).map(run=>{const done=run.counts.total-run.counts.queued-run.counts.running;return `<article class="draft-card"><strong>${esc(labels[run.state])} · ${done}/${run.counts.total}</strong><progress max="${run.counts.total}" value="${done}"></progress>${['queued','running','stopping'].includes(run.state)?`<button type="button" class="btn" data-reprocess-action="stop" data-run="${esc(run.run_id)}" ${busy||run.state==='stopping'?'disabled':''}>停止识别</button>`:''}<details><summary>处理详情</summary><ul>${run.records.map(record=>`<li>${esc(records.find(r=>r.input_id===record.input_id)?.label||record.input_id)} · ${esc(labels[record.state])}${record.old_result_retained?' · 保留旧结果':''}${record.decision_summary.unresolved?' · 新差异待核对，用户决定已保留':''}${record.error?' · '+esc(record.error.message):''}${record.policy_version?`<details><summary>实际规则版本</summary>${esc(record.policy_version)}</details>`:''}</li>`).join('')}</ul></details>${done?'<a href="#agent">查看识别结果</a>':''}</article>`;}).join('')}</section>`;
}
export function reprocessingChange(event){
 if(session.changing)return;
 if(event.target.id==='reprocess-source'){source=event.target.value;selected.clear();render();}
 if(event.target.dataset.reprocessIndex!==undefined){const item=accounts.find(a=>a.source_account_id===source)?.records[Number(event.target.dataset.reprocessIndex)];if(item){if(event.target.checked)selected.add(item.input_id);else selected.delete(item.input_id);render();}}
}
export async function reprocessingClick(event){
 const button=event.target.closest?.('[data-reprocess-action]');if(!button||busy||!session.user||session.changing)return;
 const action=button.dataset.reprocessAction,epoch=accountVersion();busy=true;error='';render();
 try{
  if(action==='load'){await load();return;}
  if(action==='start'){
   if(!selected.size)return;if(selected.size>100)throw new Error('每次最多选择 100 条通知');
   const signature=JSON.stringify([source,[...selected].sort()]);
   if(pendingOperation?.signature!==signature)pendingOperation={signature,id:newId()};
   await api(root+'runs',{contract_version:'reprocessing/0.1',operation_id:pendingOperation.id,source_account_id:source,input_ids:[...selected]});
   if(epoch!==accountVersion())return;
   pendingOperation=null;
  }
  if(action==='stop')await api(root+'runs/'+encodeURIComponent(button.dataset.run)+'/stop',{contract_version:'reprocessing/0.1'});
  if(epoch!==accountVersion())return;
  await host.finished?.();if(epoch!==accountVersion())return;
  await load(false);
 }catch(e){if(epoch===accountVersion())error=e.message;}
 finally{if(epoch===accountVersion()){busy=false;render();}}
}

import {api,session,accountVersion} from './store.js';

const root='/api/sources/sync-schedule',version='source-sync/0.1';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let host={},state=null,draft=null,loading=false,saving=false,error='',notice='',generation=0;
export function configureSyncSchedule(callbacks){host=callbacks;}
export function resetSyncSchedule(){generation++;state=null;draft=null;loading=false;saving=false;error='';notice='';}
window.addEventListener('niuma:account-reset',resetSyncSchedule);
const currentAt=()=>{const epoch=accountVersion(),gen=generation;return()=>epoch===accountVersion()&&gen===generation;};
function accept(value){
 if(value.contract_version!==version||typeof value.enabled!=='boolean')throw new Error('定时同步服务版本不匹配');
 state=value;draft={enabled:value.enabled,minutes:String(value.interval_minutes),teaching:true,consent:false};
}
export async function refreshSyncSchedule(){
 if(!session.user||session.changing||loading||saving)return;
 const current=currentAt();loading=true;error='';notice='';
 try{const value=await api(root);if(current())accept(value);}
 catch(e){if(current())error=e.message;}
 finally{if(current()){loading=false;host.render?.();}}
}
export function syncScheduleInput(event){
 const key=event.target.dataset?.syncScheduleField;if(!key)return false;
 if(draft&&!loading&&!saving){draft[key]=key==='minutes'?event.target.value:event.target.checked;notice='';}
 return true;
}
export async function syncScheduleClick(button){
 const action=button.dataset.syncScheduleAction;if(!action)return false;
 if(action==='refresh'){await refreshSyncSchedule();return true;}
 if(!state||!draft||saving||loading||!session.user||session.changing)return true;
 error='';notice='';
 const minutes=Number(draft.minutes);
 if(!Number.isInteger(minutes)||minutes<15||minutes>10080)error='同步间隔应为 15～10080 分钟。';
 else if(draft.enabled&&(!draft.teaching||!draft.consent))error='请选定教学网，并同意定期发送所选来源内容。';
 if(error){host.render?.();return true;}
 const current=currentAt();saving=true;host.render?.();
 try{
  const value=await api(root,{contract_version:version,revision:state.revision,enabled:draft.enabled,
   interval_minutes:minutes,sources:['teaching_web'],consent:draft.consent},'PUT');
  if(current()){accept(value);notice=value.enabled?'已保存，将在设定间隔后首次同步；也可以手动立即同步。':'定时同步已关闭，已保存结果保留。';}
 }catch(e){if(current())error=e.message;}
 finally{if(current()){saving=false;host.render?.();}}
 return true;
}
export function syncSchedulePanel(){
 if(!session.user)return '';
 if(!state&&!loading&&!error){loading=true;queueMicrotask(()=>{loading=false;void refreshSyncSchedule();});}
 const disabled=loading||saving,blocked=disabled||!state?.available;
 return `<section class="agent-source-sync source-schedule"><h2>定时同步</h2><p>关闭网页仍可运行；只获取你在这里选择的来源。当前支持教学网，微信尚不支持定时获取。</p>
 ${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}
 <button class="btn" data-sync-schedule-action="refresh" ${disabled?'disabled':''}>重新读取设置</button>
 ${state&&draft?`${!state.available?'<p>当前环境未开启定时同步，请联系管理员；手动同步仍可使用。</p>':''}${state.error?`<p class="error">${esc(state.error.message)}</p>`:''}
 <label><input type="checkbox" data-sync-schedule-field="enabled" ${draft.enabled?'checked':''} ${disabled||!state.available&&!draft.enabled?'disabled':''}>启用定时同步</label>
 <label>同步间隔（分钟）<input type="number" min="15" max="10080" step="1" data-sync-schedule-field="minutes" value="${esc(draft.minutes)}" ${blocked?'disabled':''}></label>
 <label><input type="checkbox" data-sync-schedule-field="teaching" ${draft.teaching?'checked':''} ${blocked?'disabled':''}>教学网</label>
 <label class="source-import-consent"><input type="checkbox" data-sync-schedule-field="consent" ${draft.consent?'checked':''} ${blocked?'disabled':''}>我同意定期获取上述来源，并将待识别内容发送给识别服务。</label>
 <button class="btn" data-sync-schedule-action="save" ${disabled?'disabled':''}>${saving?'正在保存…':'保存定时设置'}</button>
 <p>${state.enabled&&state.available&&state.next_run_at?'下次同步：'+esc(new Date(state.next_run_at*1000).toLocaleString()):'当前没有启用可运行的定时计划。'} 同步进度和失败记录见下方。</p>`:'<p>正在读取设置…</p>'}</section>`;
}

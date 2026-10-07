import {flushStore,saveData,workspaceValue,accountVersion} from './store.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let host={},settingBusy=false,settingMessage='';

export function configurePersonalization(callbacks){host=callbacks;}
window.addEventListener('niuma:account-reset',()=>{settingBusy=false;settingMessage='';});

export function asksForCompletionMinutes(){
 return workspaceValue('preferences',{}).askCompletionMinutes!==false;
}

export function personalizationSettings(){
 return `<hr><div class="personalization-setting"><div><h2>个性化安排</h2><p>完成任务时询问实际用时，帮助后续规划更贴合你的节奏。</p>${settingMessage?`<p class="${settingMessage.startsWith('保存失败')?'error':''}" role="status">${esc(settingMessage)}</p>`:''}</div><label class="setting-switch"><input type="checkbox" role="switch" data-feedback-setting="completion-minutes" ${asksForCompletionMinutes()?'checked':''} ${settingBusy?'disabled':''}><span aria-hidden="true"></span><b>询问任务完成用时</b></label></div>`;
}

export function personalizationChange(event){
 if(event.target.dataset?.feedbackSetting!=='completion-minutes')return false;
 const epoch=accountVersion(),enabled=event.target.checked;
 settingBusy=true;settingMessage='';event.target.disabled=true;
 saveData('preferences',{...workspaceValue('preferences',{}),askCompletionMinutes:enabled})
  .then(()=>{if(epoch===accountVersion())settingMessage='';})
  .catch(error=>{if(epoch===accountVersion())settingMessage='保存失败：'+error.message;})
  .finally(()=>{if(epoch===accountVersion()){settingBusy=false;host.render();}});
 return true;
}

export function promptForCompletionMinutes(task){
 if(!asksForCompletionMinutes())return false;
 const estimate=Number.isFinite(task.hours)&&task.hours>0?Math.round(task.hours*60):'';
 host.openDialog(`<form id="completion-feedback-form" data-task-id="${esc(task.id)}">${host.dialogHeader('记录完成用时')}<p class="muted">${esc(task.title)}</p><label>实际用时（分钟）<input name="actualMinutes" type="number" min="1" max="100000" step="1" required autofocus value="${estimate}" placeholder="例如：45"></label><p id="completion-feedback-error" class="error" role="alert"></p><div class="dialog-actions"><button type="button" class="btn" data-action="close">跳过</button><button type="submit" class="btn primary">保存用时</button></div></form>`);
 return true;
}

export async function personalizationSubmit(form){
 if(form.id!=='completion-feedback-form')return false;
 const submit=form.querySelector('[type="submit"]'),error=form.querySelector('#completion-feedback-error');
 submit.disabled=true;error.textContent='';
 try{
  const actualMinutes=Number(new FormData(form).get('actualMinutes'));
  if(!Number.isFinite(actualMinutes)||actualMinutes<=0||actualMinutes>100000)throw new Error('请输入有效分钟数');
  await flushStore();
  const old=workspaceValue('feedback',[]),taskId=form.dataset.taskId;
  await saveData('feedback',[...old.filter(item=>item.taskId!==taskId),{taskId,actualMinutes,recordedAt:new Date().toISOString()}]);
  host.closeDialog();host.toast('实际用时已保存，后续规划会参考这次记录');
 }catch(cause){error.textContent=cause.message;submit.disabled=false;}
 return true;
}

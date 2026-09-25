import {flushStore,saveData,workspaceValue,accountVersion} from './store.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let host={},sample=[],message='',busy=false;
export function configurePersonalization(callbacks){host=callbacks;}
window.addEventListener('niuma:account-reset',()=>{sample=[];message='';busy=false;});
export function personalizationSection(tasks){
 const completed=tasks.filter(t=>t.status==='done'),feedback=workspaceValue('feedback',[]);
 return `<section class="panel settings-panel"><div class="section-title"><div><h2>个性化</h2><p>根据真实完成用时改进后续规划。</p></div><button class="btn" data-feedback-action="sample" ${busy||!completed.length?'disabled':''}>反馈任务用时</button></div>
 <p class="panel-note">每次随机选取最多三项已完成任务，输入实际分钟数。已记录 ${feedback.length} 项。</p>
 ${sample.length?`<form id="feedback-form">${sample.map(t=>`<label class="feedback-row">${esc(t.title)}<input name="${esc(t.id)}" type="number" min="1" max="100000" required placeholder="实际分钟"></label>`).join('')}
 <div class="dialog-actions"><button class="btn primary" ${busy?'disabled':''}>保存反馈</button></div></form>`:''}
 ${message?`<p role="status">${esc(message)}</p>`:''}</section>`;
}
export function personalizationClick(button){
 if(button.dataset.feedbackAction!=='sample')return false;
 const tasks=host.getState().tasks.filter(t=>t.status==='done');
 for(let i=tasks.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[tasks[i],tasks[j]]=[tasks[j],tasks[i]];}
 sample=tasks.slice(0,3);message='';host.render();return true;
}
export async function personalizationSubmit(form){
 if(form.id!=='feedback-form')return false;
 if(busy)return true;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 busy=true;message='';
 try{
  await flushStore();
  if(!current())return true;
  const old=workspaceValue('feedback',[]),values=new FormData(form);
  const next=sample.map(t=>({taskId:t.id,actualMinutes:Number(values.get(t.id)),recordedAt:new Date().toISOString()}));
  if(next.some(x=>!Number.isFinite(x.actualMinutes)||x.actualMinutes<=0||x.actualMinutes>100000))throw new Error('请输入有效分钟数');
  await saveData('feedback',[...old.filter(f=>!sample.some(t=>t.id===f.taskId)),...next]);
  if(!current())return true;
  sample=[];message='反馈已保存，下次规划会参考这些用时。';
 }catch(e){if(current())message=e.message;}finally{if(current()){busy=false;host.render();}}
 return true;
}

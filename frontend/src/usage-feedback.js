import { userErrorMarkup } from "./user-errors.js";
import {api,session,accountVersion} from './team-store.js';
let offeredFor=null;
let experimentEnabled=false, experimentBusy=false, experimentTimer;
const modules={text:'文本识别', teaching:'教学网识别', decomposition:'任务拆解', estimation:'工时估计'};
const experimentPath='/api/usage/model-experiment';
const currentAccount=()=>[accountVersion(),session.user?.id];
const sameAccount=([epoch,uid])=>accountVersion()===epoch&&session.user?.id===uid&&!session.changing;

function evaluationForm(result, account, afterSave) {
 const form=document.createElement('form');
 const label=document.createElement('p');
 label.textContent=`${modules[result.module]||'模型结果'} · ${new Date(result.at*1000).toLocaleString('zh-CN')}`;
 form.append(label);
 form.insertAdjacentHTML('beforeend', '<div class="usage-rating-options" role="group" aria-label="给结果打 1 至 5 分">'+[1,2,3,4,5].map(score=>`<button class="btn" type="submit" value="${score}" aria-label="${score}分" aria-pressed="${score===result.score}">${score}</button>`).join('')+'</div><span role="status"></span>');
 const status=form.querySelector('[role=status]');
 if(result.score)status.textContent=`已评价：${result.score} 分`;
 form.onsubmit=async event=>{
  event.preventDefault();event.stopPropagation();
  if(!event.submitter||!sameAccount(account))return;
  const buttons=[...form.querySelectorAll('button')];
  buttons.forEach(button=>button.disabled=true);
  try{
   const score=Number(event.submitter.value);
   await api(experimentPath,{id:result.id,score});
   if(sameAccount(account)&&form.isConnected){
    status.textContent=`已评价：${score} 分`;
    buttons.forEach(button=>button.setAttribute('aria-pressed',String(Number(button.value)===score)));
    afterSave?.();
   }
  }catch(error){if(sameAccount(account))status.innerHTML=userErrorMarkup(error,'feedback');}
  finally{buttons.forEach(button=>button.disabled=false);}
 };
 return form;
}

async function experimentHistory(details, before) {
 const account=currentAccount();
 const list=details.querySelector('[data-experiment-results]');
 try {
  const page=await api(experimentPath+(before?'?before='+encodeURIComponent(before):''));
  if(!sameAccount(account)||!details.isConnected)return;
  if(!before)list.replaceChildren();
  list.querySelector('[data-experiment-next]')?.remove();
  for(const result of page.results)list.append(evaluationForm(result,account));
  if(!list.childElementCount)list.textContent='还没有可评价的模型结果。';
  if(page.next){
   const more=document.createElement('button');more.type='button';more.dataset.experimentNext='';more.textContent='查看更多结果';
   more.onclick=()=>void experimentHistory(details,page.next);list.append(more);
  }
 }catch{if(sameAccount(account)&&details.isConnected)list.textContent='暂时无法读取，重新展开可重试。';}
}

async function experimentPrompt(){
 if(!experimentEnabled||experimentBusy||!session.user||session.changing||document.visibilityState!=='visible'||document.querySelector('dialog[open]'))return;
 const account=currentAccount();
 experimentBusy=true;
 try{
  const state=await api(experimentPath);
  if(!state.promptDue||!sameAccount(account)||document.visibilityState!=='visible'||document.querySelector('dialog[open]'))return;
  const {result}=await api(experimentPath+'/prompt',{});
  if(!result||!sameAccount(account))return;
  const dialog=document.createElement('dialog');
  dialog.id='model-experiment-prompt';dialog.setAttribute('aria-label','评价模型结果');
  dialog.innerHTML='<h2>这次结果好用吗？</h2><p>每两条生成结果邀请评价一次，跳过后仍可在账户反馈里评价。</p>';
  const skip=document.createElement('button');skip.type='button';skip.className='btn';skip.textContent='暂不评价';
  skip.onclick=()=>dialog.close();
  dialog.append(evaluationForm(result,account,()=>dialog.close()),skip);
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  document.body.append(dialog);dialog.showModal();
 }catch{}finally{experimentBusy=false;}
}

document.addEventListener('toggle',event=>{
 const details=event.target;
 if(details.matches?.('[data-experiment-history]')&&details.open)void experimentHistory(details);
},true);
const ratingPrompts = [
 '抱歉没能让您满意，请问有哪些地方需要改进？',
 '感谢您的反馈，哪些体验还不够好？',
 '感谢您的评价，我们还可以做些什么，让您用得更顺手？',
 '很高兴您用得不错！还有哪些细节可以做得更好？',
 '感谢您的认可！最喜欢哪些功能，或有什么新的期待？',
];

function showRating(uid) {
 const epoch = accountVersion();
 // Non-modal: never replaces a task editor or blocks ongoing work.
 const panel = document.createElement('section');
 panel.id = 'usage-rating';
 panel.className = 'usage-rating';
 panel.setAttribute('aria-label', '使用评分');
 panel.innerHTML = `<button type="button" data-rating-dismiss aria-label="关闭评分">×</button>
  <strong>用起来感觉怎么样？</strong>
  <div class="usage-rating-options" role="group" aria-label="1至5分">${[1,2,3,4,5].map(n => `<button type="button" data-rating="${n}" aria-label="${n}分" aria-pressed="false">${n}</button>`).join('')}</div>
  <form data-rating-feedback hidden>
   <p class="usage-rating-receipt" data-rating-receipt></p>
   <p class="usage-rating-prompt" data-rating-prompt tabindex="-1"></p>
   <div class="field"><textarea name="message" aria-label="使用反馈（选填）" maxlength="2000" rows="3" placeholder="欢迎告诉我们遇到的问题或建议……"></textarea></div>
   <div class="usage-rating-actions"><button class="btn primary" type="submit">提交反馈</button><button class="btn quiet" type="button" data-rating-skip>暂时跳过</button></div>
  </form>
  <div role="status" data-rating-status></div>`;
 const choices = [...panel.querySelectorAll('[data-rating]')];
 const form = panel.querySelector('[data-rating-feedback]');
 const status = panel.querySelector('[data-rating-status]');
 const submit = form.querySelector('[type="submit"]');
 const skip = form.querySelector('[data-rating-skip]');
 const input = form.querySelector('textarea');
 const prompt = panel.querySelector('[data-rating-prompt]');
 const receipt = panel.querySelector('[data-rating-receipt]');
 let saving = false, submitting = false, completed = false, closing = false;
 let selectedScore = 0, savedScore = 0, selection = 0, closeTimer;
 let ratingQueue = Promise.resolve();
 const sameAccount = () => accountVersion() === epoch && session.user?.id === uid && !session.changing;
 const current = () => panel.isConnected && sameAccount() && !closing;
 const close = () => {
  if (closing) return;
  closing = true;
  clearTimeout(closeTimer);
  panel.inert = true;
  panel.classList.add('is-closing');
  // Keep removal reliable when animations are reduced or interrupted.
  const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 220;
  setTimeout(() => panel.remove(), duration);
 };
 panel.addEventListener('animationend', event => {
  if (event.target === panel && event.animationName === 'usage-rating-out') panel.remove();
 });

 panel.addEventListener('click', event => {
  if (event.target.closest('[data-rating-dismiss]')) return close();
  if (event.target.closest('[data-rating-skip]')) return void finish(false);
  const choice = event.target.closest('[data-rating]');
  if (!choice || submitting || completed || !current()) return;
  const score = Number(choice.dataset.rating);
  if (score === selectedScore && (saving || score === savedScore)) return;
  selectedScore = score;
  const revision = ++selection;
  saving = true;
  submit.disabled = true;
  receipt.textContent = '';
  status.textContent = '正在保存评分…';
  choices.forEach(button => {
   button.setAttribute('aria-pressed', String(button === choice));
  });
  prompt.textContent = ratingPrompts[score - 1];
  form.hidden = false;
  // Serialize writes so a slower earlier request cannot overwrite the final choice.
  // Finishing a queued choice after dismissal still preserves the last selected score.
  ratingQueue = ratingQueue.then(async () => {
   if (!sameAccount()) return;
   try {
    await api('/api/usage/feedback', { kind: 'rating', score });
    savedScore = score;
    if (!current() || revision !== selection) return;
    receipt.textContent = `已记录您的 ${score} 分评分。`;
    status.textContent = '';
    submit.disabled = submitting;
   } catch (error) {
    if (!current() || revision !== selection) return;
    status.innerHTML = userErrorMarkup(error, 'feedback');
   } finally {
    if (revision === selection) saving = false;
   }
  });
 });

 const finish = async includeMessage => {
  if (submitting || completed || !selectedScore || !current()) return;
  const message = includeMessage ? new FormData(form).get('message').trim() : '';
  submitting = true;
  submit.disabled = true;
  skip.disabled = true;
  input.disabled = true;
  choices.forEach(button => { button.disabled = true; });
  try {
   // Skipping also waits for the final score to save before acknowledging it.
   await ratingQueue;
   if (!current() || savedScore !== selectedScore) return;
   status.textContent = message ? '正在提交反馈…' : '';
   if (message) await api('/api/usage/feedback', { kind: 'feedback', message });
   if (!current()) return;
   completed = true;
   form.hidden = true;
   status.textContent = '谢谢您的反馈，我们会继续努力！';
   panel.querySelector('[data-rating-dismiss]').focus({ preventScroll: true });
   // Include the exit animation in the 1.5-second acknowledgement window.
   closeTimer = setTimeout(close, 1280);
  } catch (error) {
   if (current()) status.innerHTML = userErrorMarkup(error, 'feedback');
  } finally {
   submitting = false;
   submit.disabled = completed || savedScore !== selectedScore;
   skip.disabled = completed;
   input.disabled = completed;
   choices.forEach(button => { button.disabled = completed; });
  }
 };
 form.addEventListener('submit', event => {
  event.preventDefault();
  event.stopPropagation();
  void finish(true);
 });
 document.body.append(panel);
}

export function feedbackBox(){
 return session.user?`<section class="account-feedback"><h3>使用反馈</h3><form id="usage-feedback-form"><label class="field">有什么可以改进？<textarea name="message" maxlength="2000" required rows="3" placeholder="写下你的建议…"></textarea></label><button class="btn" type="submit">发送反馈</button><div role="status" data-feedback-status></div></form><details data-experiment-history ${experimentEnabled?'':'hidden'}><summary>评价已有模型结果</summary><div data-experiment-results></div></details></section>`:'';
}
export async function usageVisit(){
 if(document.visibilityState==='hidden')return;
 const uid=session.user?.id;
 const day=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const key=(uid||'guest')+':'+day;if(offeredFor===key)return;
 offeredFor=key;
 try{
  if(!uid){
   const response=await fetch('/api/usage/guest-visit',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});
   if(!response.ok||(await response.json()).saved!==true)throw new Error('visit unavailable');
   return;
  }
  const result=await api('/api/usage/visit',{});
  if(session.user?.id!==uid)return;
  if(result.modelExperiment&&!experimentEnabled){
   experimentEnabled=true;
   document.querySelectorAll('[data-experiment-history]').forEach(el=>el.hidden=false);
   experimentTimer=setInterval(()=>void experimentPrompt(),12000);
   void experimentPrompt();
  }
  if(!result.offerRating)return;
  showRating(uid);
 }catch{if(offeredFor===key)offeredFor=null;} // Analytics failure must not block ordinary use.
}
export async function submitUsageFeedback(form){
 if(session.changing||!session.user)return;
 const status=form.querySelector('[data-feedback-status]'),button=form.querySelector('button');button.disabled=true;
 try{await api('/api/usage/feedback',{kind:'feedback',message:new FormData(form).get('message')});form.reset();status.textContent='已收到，谢谢你的反馈。';}
 catch(error){status.innerHTML=userErrorMarkup(error,'feedback');}finally{button.disabled=false;}
}
window.addEventListener('niuma:account-reset',()=>{offeredFor=null;experimentEnabled=false;clearInterval(experimentTimer);document.querySelector('#model-experiment-prompt')?.remove();document.querySelector('#usage-rating')?.remove();});
document.addEventListener('submit',event=>{if(event.target.id==='usage-feedback-form'){event.preventDefault();event.stopImmediatePropagation();void submitUsageFeedback(event.target);}},true);
document.addEventListener('click',()=>{if(session.user)void usageVisit();});
window.addEventListener('niuma:session-ready',()=>{void usageVisit();});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void usageVisit();});
window.addEventListener('pageshow',()=>{void usageVisit();});
window.addEventListener('focus',()=>{void usageVisit();});
document.addEventListener('keydown',()=>{void usageVisit();});

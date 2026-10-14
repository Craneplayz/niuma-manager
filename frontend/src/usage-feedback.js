import {api,session} from './store.js';
let offeredFor=null;
export function feedbackBox(){
 return session.user?`<section class="panel"><h2>使用反馈</h2><form id="usage-feedback-form"><label>有什么可以改进？<textarea name="message" maxlength="2000" required rows="3" placeholder="写下你的建议…"></textarea></label><button class="btn" type="submit">发送反馈</button><p role="status" data-feedback-status></p></form></section>`:'';
}
export async function usageVisit(){
 const uid=session.user?.id;if(!uid)return;
 const day=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const key=uid+':'+day;if(offeredFor===key)return;
 offeredFor=key;
 try{
  const result=await api('/api/usage/visit',{});
  if(session.user?.id!==uid||!result.offerRating)return;
  // Non-modal: never replaces a task editor or blocks ongoing work.
  const panel=document.createElement('section');panel.id='usage-rating';panel.className='usage-rating';
  panel.setAttribute('aria-label','使用评分');
  panel.innerHTML='<button type="button" data-rating-dismiss aria-label="关闭评分">×</button><strong>用起来感觉怎么样？</strong><div aria-label="1至5分">'+[1,2,3,4,5].map(n=>`<button type="button" data-rating="${n}" aria-label="${n}分">${n}</button>`).join('')+'</div><span role="status"></span>';
  document.body.append(panel);
  panel.addEventListener('click',async event=>{
   if(event.target.closest('[data-rating-dismiss]'))return panel.remove();
   const choice=event.target.closest('[data-rating]');if(!choice)return;
   panel.querySelectorAll('button').forEach(b=>b.disabled=true);
   try{await api('/api/usage/feedback',{kind:'rating',score:Number(choice.dataset.rating)});panel.remove();}
   catch(error){panel.querySelector('[role="status"]').textContent=error.message;panel.querySelectorAll('button').forEach(b=>b.disabled=false);}
  });
 }catch{if(offeredFor===key)offeredFor=null;} // Analytics failure must not block ordinary use.
}
export async function submitUsageFeedback(form){
 if(session.changing||!session.user)return;
 const status=form.querySelector('[data-feedback-status]'),button=form.querySelector('button');button.disabled=true;
 try{await api('/api/usage/feedback',{kind:'feedback',message:new FormData(form).get('message')});form.reset();status.textContent='已收到，谢谢你的反馈。';}
 catch(error){status.textContent=error.message;}finally{button.disabled=false;}
}
window.addEventListener('niuma:account-reset',()=>{offeredFor=null;document.querySelector('#usage-rating')?.remove();});
document.addEventListener('submit',event=>{if(event.target.id==='usage-feedback-form'){event.preventDefault();event.stopImmediatePropagation();void submitUsageFeedback(event.target);}},true);
document.addEventListener('click',()=>{if(session.user)void usageVisit();});

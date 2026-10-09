// Capture positions before synchronous rendering; business updates never wait for motion.
const selector='[data-motion-task]:not([data-motion-ghost]), [data-candidate-card], .planning-insights, .proposal-task, .plan-block';
const reduced=()=>globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
let animations=[];
function key(node,index){return node.dataset.motionTask?`task:${node.dataset.motionTask}`:node.dataset.candidateCard?`candidate:${node.dataset.candidateCard}`:`${node.className}:${index}:${node.textContent}`;}
export function motionUpdate(update){
 if(reduced()){update();return;}
 for(const animation of animations)animation.cancel();animations=[];
 const before=new Map([...document.querySelectorAll(selector)].map((node,index)=>[key(node,index),{node,rect:node.getBoundingClientRect(),done:node.dataset.motionDone}]));
 const active=document.querySelector('.tabs button.active');
 const tabRect=active?.getBoundingClientRect();
 const previousFocus=document.activeElement;
 const focusKey=previousFocus?.dataset.complete||previousFocus?.dataset.filter;
 update();
 const after=[...document.querySelectorAll(selector)];
 const keys=new Set();
 after.forEach((node,index)=>{
  const id=key(node,index),old=before.get(id),rect=node.getBoundingClientRect();keys.add(id);
  if(!node.animate||rect.height===0)return;
  if(old){
   const dx=old.rect.left-rect.left,dy=old.rect.top-rect.top;
   if(dx||dy)animations.push(node.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'translate(0,0)'}],{duration:240,easing:'cubic-bezier(.22,1,.36,1)'}));
   if(old.done!==node.dataset.motionDone)animations.push(node.animate([{background:'var(--story-soft)'},{background:'transparent'}],{duration:450}));
  }else animations.push(node.animate([{opacity:0,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}],{duration:260,delay:Math.min(index,4)*35,fill:'backwards',easing:'cubic-bezier(.22,1,.36,1)'}));
 });
 for(const [id,{node,rect}] of before){
  if(keys.has(id)||!id.startsWith('task:')||!document.querySelector('#task-results')||!rect.height)continue;
  const ghost=node.cloneNode(true);ghost.setAttribute('aria-hidden','true');ghost.dataset.motionGhost='true';ghost.inert=true;
  Object.assign(ghost.style,{position:'fixed',left:`${rect.left}px`,top:`${rect.top}px`,width:`${rect.width}px`,height:`${rect.height}px`,margin:'0',pointerEvents:'none',zIndex:'2'});
  ghost.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));document.body.append(ghost);
  const animation=ghost.animate([{opacity:.65,transform:'translateX(0)'},{opacity:0,transform:'translateX(16px)'}],{duration:180});
  animations.push(animation);animation.finished.catch(()=>{}).finally(()=>ghost.remove());
 }
 const next=document.querySelector('.tabs button.active');
 if(tabRect&&next?.animate){const rect=next.getBoundingClientRect();if(rect.left!==tabRect.left)animations.push(next.animate([{transform:`translateX(${tabRect.left-rect.left}px) scaleX(${tabRect.width/rect.width})`},{transform:'translateX(0) scaleX(1)'}],{duration:220,easing:'ease-out',pseudoElement:'::after'}));}
 if(focusKey&&!previousFocus.isConnected){const target=[...document.querySelectorAll('[data-complete],[data-filter]')].find(node=>node.dataset.complete===focusKey||node.dataset.filter===focusKey);(target||document.querySelector('.tabs button.active'))?.focus({preventScroll:true});}
}
export function waitingPanel(title,description){return `<section class="motion-waiting" role="status" aria-live="polite"><span class="motion-waiting-orb" aria-hidden="true"></span><div><strong>${title}</strong><p>${description}</p><div class="motion-skeleton" aria-hidden="true"><i></i><i></i><i></i></div></div></section>`;}

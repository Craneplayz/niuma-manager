// Only a successfully saved review flies to the task chapter; the API remains authoritative.
const reduced=()=>globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const flights=new Set(),flyingIds=new Set();
export const isReviewFlying=id=>flyingIds.has(id);
export function cancelReviewMotion(){for(const cancel of flights)cancel();flights.clear();}
function visible(rect){return rect.width>0&&rect.height>0&&rect.top>=0&&rect.bottom<=globalThis.innerHeight;}
export async function animateReviewedCandidate(id,current=()=>true){
 const doc=globalThis.document;
 if(reduced()||!current()||typeof doc?.querySelectorAll!=='function')return;
 const source=[...doc.querySelectorAll('[data-candidate-card]')].find(node=>node.dataset.candidateCard===id);
 if(!source?.animate)return;
 const rect=source.getBoundingClientRect();
 if(rect.bottom<0||rect.top>globalThis.innerHeight||!rect.width)return;
 let destination=doc.querySelector('[data-review-task-center]')||doc.querySelector('.story-chapter[href="#tasks"]'),chip=null;
 if(!destination||!visible(destination.getBoundingClientRect())){
  chip=doc.createElement('a');chip.className='review-destination';chip.href='#tasks';chip.textContent='看清事情 · 任务中心';doc.body.append(chip);destination=chip;
 }
 const target=destination.getBoundingClientRect(),x=rect.left+rect.width/2-64,y=Math.max(12,Math.min(globalThis.innerHeight-94,rect.top+rect.height/2-41));
 const envelope=doc.createElement('div');envelope.className='review-envelope';envelope.setAttribute('aria-hidden','true');envelope.inert=true;
 envelope.innerHTML='<svg viewBox="0 0 128 82" fill="none" aria-hidden="true"><rect x="1" y="1" width="126" height="80" rx="6"/><path d="M3 4 64 44 125 4M3 78 44 39M125 78 84 39"/></svg>';
 Object.assign(envelope.style,{left:`${x}px`,top:`${y}px`});doc.body.append(envelope);
 const animations=[];let stopped=false;
 const cancel=()=>{stopped=true;for(const animation of animations)animation.cancel();envelope.remove();chip?.remove();};
 flights.add(cancel);flyingIds.add(id);
 const win=globalThis.window;
 win?.addEventListener('hashchange',cancel,{once:true});win?.addEventListener('niuma:account-reset',cancel,{once:true});
 try{
  const card=source.animate([{opacity:1,transform:'scale(1)'},{opacity:0,transform:'scale(.24)'}],{duration:220,easing:'cubic-bezier(.4,0,.2,1)',fill:'forwards'});card.finished.catch(()=>{});animations.push(card);
  const fold=envelope.animate([{opacity:0,transform:'scale(.6)'},{opacity:1,transform:'scale(1)'}],{duration:220,easing:'ease-out',fill:'forwards'});animations.push(fold);await fold.finished;
  if(stopped||!current())return;
  const dx=target.left+target.width/2-(x+64),dy=target.top+target.height/2-(y+41);
  const flight=envelope.animate([{opacity:1,transform:'translate(0,0) scale(1) rotate(0deg)'},{opacity:1,transform:`translate(${dx*.55}px,${dy*.6-28}px) scale(.65) rotate(-9deg)`,offset:.55},{opacity:0,transform:`translate(${dx}px,${dy}px) scale(.12) rotate(0deg)`}],{duration:460,easing:'cubic-bezier(.4,0,.2,1)',fill:'forwards'});animations.push(flight);await flight.finished;
  if(!stopped&&current()){source.style.visibility='hidden';}
  if(!stopped&&current())destination.animate?.([{background:'var(--story-soft)',transform:'scale(1.06)'},{background:'transparent',transform:'scale(1)'}],{duration:400,easing:'ease-out'});
 }catch{/* Navigation or account switching cancels visual feedback, not the saved task. */}
 finally{win?.removeEventListener('hashchange',cancel);win?.removeEventListener('niuma:account-reset',cancel);flights.delete(cancel);flyingIds.delete(id);for(const animation of animations)animation.cancel();envelope.remove();if(chip){if(stopped||!current())chip.remove();else setTimeout(()=>chip.remove(),1200);}}
}

// Draw the completion arrow in page coordinates so it scrolls with the review content.
export function syncReviewDestination(){
 const doc=globalThis.document;if(typeof doc?.querySelector!=='function')return;
 const region=doc.querySelector('#import-review'),rect=region?.getBoundingClientRect();
 const shown=globalThis.location?.hash==='#agent'&&rect&&rect.top<globalThis.innerHeight-80&&rect.bottom>0;
 doc.documentElement.dataset.reviewDock=shown?'visible':'hidden';

}
let destinationFrame=0;
function queueDestination(){if(destinationFrame)return;destinationFrame=globalThis.requestAnimationFrame?.(()=>{destinationFrame=0;syncReviewDestination();});}
globalThis.window?.addEventListener?.('scroll',queueDestination,{passive:true});
globalThis.window?.addEventListener?.('resize',queueDestination);
globalThis.window?.addEventListener?.('hashchange',queueDestination);

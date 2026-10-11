// Tracks only the current import; older pending candidates never block its handoff.
export class ImportJourney{
 constructor(){this.reset();}
 reset(){this.phase='idle';this.requestIds=new Set();this.originalTaskIds=new Set();this.seen=0;this.source='';}
 begin(ids,tasks=[],source=''){this.reset();this.phase='importing';this.requestIds=new Set(ids);this.originalTaskIds=new Set(tasks.map(task=>task.id));this.source=source;}
 get active(){return ['importing','review','failed'].includes(this.phase);}
 items(batches){return batches.filter(batch=>this.requestIds.has(batch.request.request_id)).flatMap(batch=>batch.items);}
 snapshot(batches,tasks=[]){const items=this.items(batches);this.seen=Math.max(this.seen,items.length);return {pending:items.filter(item=>item.state==='pending').length,ignored:items.filter(item=>item.state==='ignored').length,added:tasks.filter(task=>!this.originalTaskIds.has(task.id)&&task.recognitionHistory?.some(history=>this.requestIds.has(history.requestId))).map(task=>task.id)};}
 finish(failed=false){this.phase=failed?'failed':'review';}
}
let slideFrame=0;
export function slideToImportPage(selector){
 const doc=globalThis.document;if(typeof doc?.querySelector!=='function')return;
 const target=doc.querySelector(selector);if(!target)return;
 if(slideFrame)cancelAnimationFrame(slideFrame);
 if(globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches){target.scrollIntoView({behavior:'instant',block:'start'});return;}
 const start=globalThis.scrollY||0,started=performance.now(),route=globalThis.location?.hash;
 doc.documentElement.classList.add('capture-sliding');
 const tick=now=>{
  const node=doc.querySelector(selector);
  if(!node||route!==globalThis.location?.hash){doc.documentElement.classList.remove('capture-sliding');slideFrame=0;return;}
  const t=Math.min(1,(now-started)/560),ease=1-Math.pow(1-t,3),end=selector==='.story-header'?0:node.getBoundingClientRect().top+(globalThis.scrollY||0);
  globalThis.scrollTo({top:start+(end-start)*ease,behavior:'instant'});
  if(t<1)slideFrame=requestAnimationFrame(tick);else{doc.documentElement.classList.remove('capture-sliding');slideFrame=0;}
 };
 slideFrame=requestAnimationFrame(tick);
}
export function scrollToReview(){slideToImportPage('#import-review');}
function cancelSlide(){if(slideFrame){cancelAnimationFrame(slideFrame);slideFrame=0;globalThis.document?.documentElement.classList.remove('capture-sliding');}}
globalThis.window?.addEventListener?.('wheel',cancelSlide,{passive:true});
globalThis.window?.addEventListener?.('touchstart',cancelSlide,{passive:true});
globalThis.window?.addEventListener?.('niuma:account-reset',cancelSlide);
globalThis.window?.addEventListener?.('hashchange',cancelSlide);
// Every stop between the two pages settles; scrolling within review stays free.
export function boundarySnapTarget(y,previousY,reviewTop,viewportHeight){
 if(y<=0||y>=reviewTop||reviewTop<=0)return null;
 // A short window may need ordinary scrolling to reach the original form controls.
 if(y<=Math.max(0,reviewTop-viewportHeight))return null;
 const movement=y-previousY;
 if(Math.abs(movement)<24)return y<reviewTop/2?'.story-header':'#import-review';
 return movement<0?'.story-header':'#import-review';
}
export function syncImportPageSize(){
 const entry=globalThis.document?.querySelector?.('#import-entry');if(!entry)return;
 const top=entry.getBoundingClientRect().top+(globalThis.scrollY||0);
 entry.style.setProperty('--capture-entry-space',`${Math.max(240,(globalThis.innerHeight||800)-top)}px`);
}
let boundaryTimer,scrollIntent=false,scrollStart=0,lastScrollY=0,touchActive=false;
function scheduleBoundary(){clearTimeout(boundaryTimer);boundaryTimer=setTimeout(settleBoundary,180);}
function beginUserScroll(){cancelSlide();if(!scrollIntent)scrollStart=globalThis.scrollY||0;scrollIntent=true;scheduleBoundary();}
function settleBoundary(){
 if(!scrollIntent||slideFrame||touchActive)return;
 scrollIntent=false;
 if(globalThis.location?.hash!=='#agent')return;
 const review=globalThis.document?.querySelector?.('#import-review');if(!review)return;
 const y=globalThis.scrollY||0,target=boundarySnapTarget(y,scrollStart,review.getBoundingClientRect().top+y,globalThis.innerHeight||800);
 if(target)slideToImportPage(target);
}
globalThis.window?.addEventListener?.('wheel',beginUserScroll,{passive:true});
globalThis.window?.addEventListener?.('touchstart',()=>{touchActive=true;beginUserScroll();},{passive:true});
globalThis.window?.addEventListener?.('touchend',()=>{touchActive=false;scheduleBoundary();},{passive:true});
globalThis.window?.addEventListener?.('touchcancel',()=>{touchActive=false;scheduleBoundary();},{passive:true});
globalThis.window?.addEventListener?.('keydown',event=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)&&!event.target?.closest?.('input,textarea,select,[contenteditable=true]'))beginUserScroll();});
globalThis.window?.addEventListener?.('scroll',()=>{
 const y=globalThis.scrollY||0;
 if(!slideFrame){if(!scrollIntent){scrollStart=lastScrollY;scrollIntent=true;}scheduleBoundary();}
 lastScrollY=y;
},{passive:true});
globalThis.window?.addEventListener?.('resize',syncImportPageSize);
export async function rewriteReview(){
 const doc=globalThis.document;if(typeof doc?.querySelector!=='function'||globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
 const region=doc.querySelector('.current-import-section .result-heading')||doc.querySelector('.notification-import');
 if(!region?.animate)return;
 const sweep=doc.createElement('div');sweep.className='journey-wipe';sweep.setAttribute('aria-hidden','true');region.append(sweep);
 const animation=sweep.animate([{transform:'scaleX(0)',transformOrigin:'left',opacity:1},{transform:'scaleX(1)',opacity:1,offset:.5},{transform:'scaleX(0)',transformOrigin:'right',opacity:0}],{duration:420,easing:'cubic-bezier(.22,1,.36,1)'});
 try{await animation.finished;}catch{}finally{sweep.remove();}
}

export function groupImportBatches(batches){
 const groups=new Map();
 for(const batch of batches){const id=batch.importId||batch.request.request_id;let group=groups.get(id);if(!group){group={id,createdAt:batch.importedAt||'',source:batch.importSource||[...new Set(batch.request.records.map(record=>({teaching_web:'教学网',wechat:'微信',manual:'手动通知',email:'邮件'}[record.platform]||'通知')))].join('、'),batches:[]};groups.set(id,group);}group.batches.push(batch);}
 return [...groups.values()].sort((a,b)=>(b.createdAt||'').localeCompare(a.createdAt||''));
}

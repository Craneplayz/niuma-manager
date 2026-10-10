// Commit navigation synchronously so a snapshot never swallows the next click.
const routes=['agent','tasks','decompose','book','trash','plan','calendar','settings'];
const regions={page:'.story-main',import:'.import-mode-panel',calendar:'.calendar-panel'};
let animations=[];
function stopAnimations(){for(const animation of animations)animation.cancel();animations=[];}
export function transitionView(update,{scope='page',from='',to='',direction=routes.indexOf(to)<routes.indexOf(from)?-1:1}={}){
 const doc=globalThis.document;
 stopAnimations();
 if(!doc?.documentElement||globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches){update();return;}
 const marker=scope==='page'?doc.querySelector('.story-nav-marker'):null;
 const oldMarker=marker?.getBoundingClientRect?.();
 doc.documentElement.dataset.motionScope=scope;
 try{update();}finally{delete doc.documentElement.dataset.motionScope;}
 const region=doc.querySelector(regions[scope]);
 if(region?.animate){
  const targets=scope==='page'?[...region.querySelectorAll('.story-intro, .story-capture-note, .panel, .decomposition-layout')]:[region];
  const groups=targets.filter(target=>!targets.some(parent=>parent!==target&&parent.contains(target)));
  for(const [index,target] of (groups.length?groups:[region]).entries())animations.push(target.animate([
   {opacity:0,transform:`translate(${direction*16}px, 8px)`},{opacity:1,transform:'translate(0,0)'}
  ],{duration:300,delay:Math.min(index,3)*30,easing:'cubic-bezier(.22,1,.36,1)',fill:'backwards'}));
 }
 const next=scope==='page'?doc.querySelector('.story-nav-marker'):null;
 if(oldMarker&&next?.animate){const rect=next.getBoundingClientRect();if(rect.width)animations.push(next.animate([
  {transform:`translateX(${oldMarker.left-rect.left}px) scaleX(${oldMarker.width/rect.width})`},{transform:'translateX(0) scaleX(1)'}
 ],{duration:300,easing:'cubic-bezier(.22,1,.36,1)'}));}
}

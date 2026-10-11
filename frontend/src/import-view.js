// Reconcile the import screen in place so reading evidence survives background updates.
function key(node){
 if(node.nodeType!==1)return '';
 for(const attribute of ['id','data-candidate-card','data-import-history','data-import-part']){
  if(node.hasAttribute(attribute))return `${attribute}:${node.getAttribute(attribute)}`;
 }
 return '';
}
function compatible(a,b){return a.nodeType===b.nodeType&&a.nodeName===b.nodeName&&key(a)===key(b)&&(!a.classList||a.classList[0]===b.classList[0]);}
function reconcile(node,next,active){
 if(node.nodeType!==1){if(node.nodeValue!==next.nodeValue)node.nodeValue=next.nodeValue;return;}
 for(const attribute of [...node.attributes]){
  if(attribute.name==='open'&&node.nodeName==='DETAILS')continue;
  if(!next.hasAttribute(attribute.name))node.removeAttribute(attribute.name);
 }
 for(const attribute of [...next.attributes]){
  if(attribute.name==='open'&&node.nodeName==='DETAILS')continue;
  if(node.getAttribute(attribute.name)!==attribute.value)node.setAttribute(attribute.name,attribute.value);
 }
 reconcileChildren(node,next,active);
 if(node.nodeName==='INPUT'){
  node.checked=next.checked;
  if(node!==active&&node.type!=='file'&&node.value!==next.value)node.value=next.value;
 }
 if(['TEXTAREA','SELECT'].includes(node.nodeName)&&node!==active&&node.value!==next.value)node.value=next.value;
}
function reconcileChildren(parent,next,active){
 const old=[...parent.childNodes],used=new Set(),keyed=new Map(old.filter(key).map(node=>[key(node),node]));
 let cursor=parent.firstChild;
 for(const child of [...next.childNodes]){
  const id=key(child);
  const match=id?keyed.get(id):old.find(node=>!used.has(node)&&!key(node)&&compatible(node,child));
  if(match&&compatible(match,child)){
   used.add(match);
   if(match!==cursor)parent.insertBefore(match,cursor);
   reconcile(match,child,active);cursor=match.nextSibling;
  }else{parent.insertBefore(child,cursor);}
 }
 for(const node of old)if(!used.has(node))node.remove();
}
export function updateImportView(root,markup,afterUpdate=()=>{}){
 const doc=root.ownerDocument,win=doc.defaultView,active=doc.activeElement;
 const reading=[...root.querySelectorAll('[data-candidate-card]'),...root.querySelectorAll('[data-import-history], #import-review')]
  .filter(node=>{const rect=node.getBoundingClientRect();return rect.bottom>0&&rect.top<win.innerHeight;});
 // Prefer the card being read; the review heading is a fallback before results arrive.
 const anchors=reading.map(node=>({node,top:node.getBoundingClientRect().top}));
 const y=win.scrollY,x=win.scrollX;
 const template=doc.createElement('template');template.innerHTML=markup;
 reconcileChildren(root,template.content,active);
 afterUpdate();
 if(active!==doc.body&&root.contains(active)&&doc.activeElement!==active)active.focus({preventScroll:true});
 if(y>0){
  const anchor=anchors.find(({node})=>node.isConnected);
  const delta=anchor?anchor.node.getBoundingClientRect().top-anchor.top:0;
  if(delta||win.scrollY!==y)win.scrollTo({left:x,top:y+delta,behavior:'instant'});
 }
}

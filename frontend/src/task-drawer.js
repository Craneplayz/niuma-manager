const configured=new WeakSet();
export function prepareDialog(dialog){
 const task=dialog.querySelector('#task-form');
 if(!dialog.open)dialog.returnTarget=document.activeElement;
 dialog.classList.toggle('task-drawer',Boolean(task));
 document.documentElement.toggleAttribute('data-task-drawer',Boolean(task));
 if(task){
  dialog.returnTaskId=task.dataset.id;
  const heading=dialog.querySelector('.dialog-title h2');heading.id='task-drawer-title';dialog.setAttribute('aria-labelledby',heading.id);
 }else dialog.removeAttribute('aria-labelledby');
 if(configured.has(dialog))return;
 configured.add(dialog);
 dialog.addEventListener('close',()=>{
  if(dialog.open)return;
  document.documentElement.removeAttribute('data-task-drawer');dialog.classList.remove('task-drawer','closing');
  const row=[...document.querySelectorAll('[data-motion-task]:not([data-motion-ghost])')].find(node=>node.dataset.motionTask===dialog.returnTaskId);
  const target=dialog.returnTarget?.isConnected?dialog.returnTarget:row?.querySelector('[data-edit]');
  target?.focus({preventScroll:true});
 });
}
export function highlightTask(id){
 const row=[...document.querySelectorAll('[data-motion-task]:not([data-motion-ghost])')].find(node=>node.dataset.motionTask===id);
 if(!row)return;
 row.setAttribute('data-task-saved','true');setTimeout(()=>row.removeAttribute('data-task-saved'),1400);
}

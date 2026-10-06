import {trashStoredTasks,restoreStoredTasks,deleteStoredTasks,accountVersion,session} from './store.js';
import {icon} from './icons.js';
import {statusLabels} from './data.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let host={},busy=false,pendingDeleteIds=[];
export function configureTrash(callbacks){host=callbacks;}
export const isTrashBusy=()=>busy;
window.addEventListener('niuma:account-reset',()=>{busy=false;pendingDeleteIds=[];});
function deletedTime(value){
 const date=new Date(value);
 return Number.isFinite(date.getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date):'时间未知';
}
export function trashPage(){
 const tasks=[...(host.getState?.().trash||[])].sort((a,b)=>String(b.recycleBin.deletedAt).localeCompare(String(a.recycleBin.deletedAt)));
 return `<div class="page-heading"><div><h1>回收站</h1><p>已删除的任务保留在这里，可以随时恢复。</p></div><a class="btn" href="#tasks">返回任务中心</a></div><section class="panel trash-panel"><div class="trash-toolbar"><span>共 ${tasks.length} 项任务</span><div class="button-row"><button class="btn" data-trash-action="restore-all" ${busy||!tasks.length?'disabled':''}>全部恢复</button><button class="btn danger" data-trash-action="empty" ${busy||!tasks.length?'disabled':''}>清空回收站</button></div></div>${tasks.length?tasks.map(task=>`<article class="trash-task"><div class="trash-task-content"><h2>${esc(task.title)}</h2><p>${[task.course,statusLabels[task.status],task.due?'截止 '+task.due+(task.dueTime?' '+task.dueTime:''):'无 DDL'].filter(Boolean).map(esc).join(' · ')}</p><span>删除于 ${esc(deletedTime(task.recycleBin.deletedAt))}${task.recycleBin.plans?.length?` · 保留 ${task.recycleBin.plans.length} 项日程`:''}</span>${task.notes?`<details><summary>查看备注</summary><p>${esc(task.notes)}</p></details>`:''}</div><div class="button-row"><button class="btn" data-trash-restore="${esc(task.id)}" ${busy?'disabled':''}>${icon('restore')}恢复</button><button class="btn danger text-btn" data-trash-delete="${esc(task.id)}" ${busy?'disabled':''}>彻底删除</button></div></article>`).join(''):`<div class="empty">${icon('trash')}<h3>回收站是空的</h3><p>删除的任务会先移到这里。</p></div>`}</section>`;
}
async function changeTasks(ids,operation,verb){
 if(busy)return;
 if(!session.user){host.requireLogin?.();return;}
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 busy=true;host.closeDialog();host.render();
 let saved=false;
 try{
  await operation(ids);saved=true;
  if(!current())return;
  host.clearSelection?.(ids);
  await host.refresh();
  if(current())host.toast(`已${verb} ${ids.length} 项任务`);
 }catch(error){if(current())host.toast(saved?`任务已${verb}，页面刷新失败：${error.message}`:`操作失败：${error.message}`);}
 finally{if(current()){busy=false;host.render();}}
}
export function moveTasksToTrash(ids){
 const selected=[...new Set(ids)].filter(id=>host.getState().tasks.some(task=>task.id===id));
 if(selected.length)return changeTasks(selected,trashStoredTasks,'移入回收站');
}
function confirmPermanentDelete(ids){
 if(busy||!ids.length)return;
 pendingDeleteIds=[...ids];
 host.openDialog(`${host.dialogHeader(ids.length===1?'彻底删除这项任务？':`清空回收站中的 ${ids.length} 项任务？`)}<p>任务、保留的日程和完成用时记录会被永久删除，无法恢复。</p><div class="dialog-actions"><button class="btn" data-action="close">取消</button><button class="btn danger" data-trash-action="confirm-delete">彻底删除</button></div>`);
}
export function trashClick(button){
 const {trashAction,trashRestore,trashDelete}=button.dataset;
 if(!trashAction&&!trashRestore&&!trashDelete)return false;
 const tasks=host.getState().trash||[];
 if(busy)return true;
 if(trashRestore&&tasks.some(task=>task.id===trashRestore))void changeTasks([trashRestore],restoreStoredTasks,'恢复');
 if(trashDelete&&tasks.some(task=>task.id===trashDelete))confirmPermanentDelete([trashDelete]);
 if(trashAction==='restore-all'&&tasks.length)void changeTasks(tasks.map(task=>task.id),restoreStoredTasks,'恢复');
 if(trashAction==='empty')confirmPermanentDelete(tasks.map(task=>task.id));
 if(trashAction==='confirm-delete'){
  const ids=pendingDeleteIds.filter(id=>tasks.some(task=>task.id===id));pendingDeleteIds=[];
  if(ids.length)void changeTasks(ids,deleteStoredTasks,'彻底删除');
 }
 return true;
}

import {isBlocked} from './data.js';

export function updateTaskCompletion(tasks,ids,{completed,date}) {
 const selected=new Set(ids),changed=[],skipped=[];
 const next=tasks.map(task=>{
  if(!selected.has(task.id))return task;
  if(completed&&task.status!=='done'&&isBlocked(task,tasks)){skipped.push(task.id);return task;}
  const status=completed?'done':'todo',completedAt=completed?date:'';
  if(task.status===status&&(task.completedAt||'')===completedAt)return task;
  changed.push({id:task.id,status:task.status,completedAt:task.completedAt||''});
  return {...task,status,completedAt};
 });
 return {tasks:next,changed,skipped};
}

export function restoreTaskCompletion(tasks,changed) {
 const previous=new Map(changed.map(task=>[task.id,task]));
 return tasks.map(task=>previous.has(task.id)?{...task,status:previous.get(task.id).status,completedAt:previous.get(task.id).completedAt}:task);
}

export function removeTasks(tasks,schedule,ids) {
 const selected=new Set(ids);
 const snapshot={
  tasks:tasks.map((task,index)=>({task,index})).filter(item=>selected.has(item.task.id)),
  schedule:schedule.map((event,index)=>({event,index})).filter(item=>selected.has(item.event.taskId)),
  dependencies:tasks.filter(task=>selected.has(task.dependsOn)&&!selected.has(task.id)).map(task=>({id:task.id,dependsOn:task.dependsOn}))
 };
 return {
  tasks:tasks.filter(task=>!selected.has(task.id)).map(task=>selected.has(task.dependsOn)?{...task,dependsOn:''}:task),
  schedule:schedule.filter(event=>!selected.has(event.taskId)),
  snapshot
 };
}

function restoreAt(items,entries,key) {
 const result=[...items];
 for(const entry of [...entries].sort((a,b)=>a.index-b.index)){
  const value=entry[key];
  if(!result.some(item=>item.id===value.id))result.splice(Math.min(entry.index,result.length),0,value);
 }
 return result;
}

export function restoreRemovedTasks(tasks,schedule,snapshot) {
 const nextTasks=restoreAt(tasks,snapshot.tasks,'task');
 const dependencies=new Map(snapshot.dependencies.map(item=>[item.id,item.dependsOn]));
 return {
  tasks:nextTasks.map(task=>dependencies.has(task.id)&&!task.dependsOn?{...task,dependsOn:dependencies.get(task.id)}:task),
  schedule:restoreAt(schedule,snapshot.schedule,'event')
 };
}

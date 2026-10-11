export const TASK_PAGE_SIZE=10;
export function paginateTasks(tasks,page=1){
 const total=tasks.length,pageCount=Math.max(1,Math.ceil(total/TASK_PAGE_SIZE));
 const current=Math.min(pageCount,Math.max(1,Number.isFinite(page)?Math.trunc(page):1));
 const offset=(current-1)*TASK_PAGE_SIZE;
 return {items:tasks.slice(offset,offset+TASK_PAGE_SIZE),page:current,pageCount,total,start:total?offset+1:0,end:Math.min(offset+TASK_PAGE_SIZE,total)};
}
export function taskPaginationMarkup({page,pageCount,total,start,end}){
 if(!total)return '';
 return `<nav class="task-pagination" aria-label="任务分页"><span class="task-page-range">第 ${start}–${end} 项 · 共 ${total} 项</span><div class="task-page-controls"><button class="btn" data-task-page="${page-1}" ${page===1?'disabled':''}>上一页</button><span class="task-page-position" tabindex="-1" aria-live="polite" aria-atomic="true">第 ${page} / ${pageCount} 页</span><button class="btn" data-task-page="${page+1}" ${page===pageCount?'disabled':''}>下一页</button></div></nav>`;
}

export const dayKey = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
export function offsetDay(n) {const d = new Date(); d.setDate(d.getDate()+n); return dayKey(d);}
export function seed() {
 return {version:1,tasks:[
 {id:'t1',title:'完成需求分析与用例设计',course:'智能化软件系统与工程',due:offsetDay(0),priority:'high',status:'doing',hours:2,location:'图书馆',notes:'梳理核心场景，整理用户故事与验收标准。',dependsOn:'',subtasks:[{title:'整理用户故事',done:true},{title:'绘制用例图',done:false},{title:'和小组对齐验收标准',done:false}]},
 {id:'t2',title:'第三章习题：线性空间',course:'线性代数',due:offsetDay(1),priority:'high',status:'todo',hours:1.5,location:'自习室',notes:'完成教材第三章第 1—8 题。',dependsOn:'',subtasks:[]},
 {id:'t3',title:'阅读论文，整理文献笔记',course:'人工智能基础',due:offsetDay(3),priority:'medium',status:'todo',hours:2,location:'图书馆',notes:'阅读课程指定论文，记录方法与问题。',dependsOn:'',subtasks:[]},
 {id:'t4',title:'搭建项目首页与任务列表',course:'智能化软件系统与工程',due:offsetDay(4),priority:'medium',status:'todo',hours:3,location:'宿舍',notes:'完成可以在组会上演示的前端。',dependsOn:'t1',subtasks:[]},
 {id:'t5',title:'整理本周课程笔记',course:'线性代数',due:offsetDay(-1),priority:'low',status:'done',hours:1,location:'宿舍',notes:'',dependsOn:'',subtasks:[]}
 ],courses:[{id:'c1',name:'智能化软件系统与工程',color:'green',teacher:'课程项目',description:'需求分析 · 团队协作 · 软件实现'},{id:'c2',name:'线性代数',color:'orange',teacher:'基础课程',description:'线性空间 · 矩阵 · 线性变换'},{id:'c3',name:'人工智能基础',color:'blue',teacher:'专业课程',description:'机器学习 · 论文阅读 · 实践'}],schedule:[],materials:[]};
}
export const priorityLabels = {high:'高优先级',medium:'中优先级',low:'低优先级'};
export const statusLabels = {todo:'待开始',doing:'进行中',done:'已完成'};
export function isBlocked(task,tasks) {return Boolean(task.dependsOn && tasks.some(t=>t.id===task.dependsOn && t.status!=='done'));}

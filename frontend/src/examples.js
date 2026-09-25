import {seed, offsetDay, dayKey} from './data.js';

// Stable IDs keep repeat clicks from duplicating or overwriting an edited example.
export function examplePack() {
 const pack=seed();
 const ids=new Map(pack.tasks.map(t=>[t.id,'example-'+t.id]));
 pack.tasks=pack.tasks.map(t=>({...t,id:ids.get(t.id),title:'【示例】'+t.title,dependsOn:ids.get(t.dependsOn)||'',notes:'示例内容，可自由修改。\n'+t.notes}));
 pack.courses=pack.courses.map(c=>({...c,id:'example-'+c.id}));
 pack.schedule=[{id:'example-s1',taskId:'example-t1',date:offsetDay(0),start:14,hours:2},{id:'example-s2',taskId:'example-t2',date:offsetDay(1),start:10,hours:1.5},{id:'example-s3',taskId:'example-t3',date:offsetDay(2),start:15,hours:2}];
 pack.materials=[
  {id:'example-m1',title:'【示例】需求分析 · 组会纪要',course:pack.courses[0].name,url:'',notes:'目标：先完成「说出 DDL → 核对 → 加入日历」的完整流程。\n分工：前端负责交互，后端负责账号与识别接口。\n下次组会：演示任务创建与日历联动。'},
  {id:'example-m2',title:'【示例】线性空间 · 复习卡片',course:pack.courses[1].name,url:'',notes:'核心概念：线性无关、基、维数。\n自测：如何判断一组向量能否构成一组基？\n待复习：把课上例题独立再做一遍。'},
  {id:'example-m3',title:'【示例】论文阅读 · 笔记模板',course:pack.courses[2].name,url:'',notes:'研究问题：作者试图解决什么问题？\n方法：核心思路与关键假设是什么？\n证据：实验是否支持结论？\n我的疑问：记录一个希望课上讨论的问题。'}
 ];
 return pack;
}
export function addExamples(state) {
 const pack=examplePack(); let added=0;
 const result={...state};
 for(const key of ['courses','tasks','materials','schedule']){
  const fresh=pack[key].filter(item=>!state[key].some(old=>old.id===item.id||(key==='courses'&&old.name===item.name)));
  result[key]=[...state[key],...fresh];added+=fresh.length;
 }
 return {state:result,added};
}
export function weekReview(state, date=new Date()) {
 const monday=new Date(date);monday.setDate(monday.getDate()-(monday.getDay()+6)%7);
 const sunday=new Date(monday);sunday.setDate(sunday.getDate()+6);
 const start=dayKey(monday),end=dayKey(sunday);
 const tasks=state.tasks.filter(t=>t.due>=start&&t.due<=end);
 return {total:tasks.length,done:tasks.filter(t=>t.status==='done').length,remaining:tasks.filter(t=>t.status!=='done').reduce((sum,t)=>sum+t.hours,0),scheduled:state.schedule.filter(s=>s.date>=start&&s.date<=end).reduce((sum,s)=>sum+s.hours,0)};
}

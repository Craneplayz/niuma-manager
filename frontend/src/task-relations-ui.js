import {api,session,accountVersion,flushStore} from './team-store.js';
const root='/api/recognition/relations';
const statuses={running:'正在关联相关任务',succeeded:'关联完成',failed:'关联未完成',interrupted:'关联已中断',stopped:'关联已停止'};
export function createTaskRelationsUI(host){
 const esc=host.escapeHtml,details=new Map();let data={jobs:[],proposals:[]},generation=0,busy=false,loading=false,error='',timer=null,opened=false;
 const stamp=()=>[generation,accountVersion()],valid=s=>s[0]===generation&&s[1]===accountVersion()&&session.user&&!host.isDemo();
 function reset(){generation++;clearTimeout(timer);timer=null;data={jobs:[],proposals:[]};busy=loading=opened=false;error='';details.clear();}
 function toggle(event){
  const node=event.target,d=node?.dataset;if(!d||node.isConnected===false)return false;
  if('relationPanel' in d){opened=node.open;return true;}
  if(d.relationDetails){details.set(d.relationDetails,node.open);return true;}return false;
 }
 async function load(){
  if(loading||!session.user||host.isDemo())return;
  const s=stamp();loading=true;clearTimeout(timer);
  try{
   const next=await api(root);if(!valid(s))return;
   const changed=JSON.stringify(data)!==JSON.stringify(next);
   data=next;error='';
   if(changed)await host.changed(()=>valid(s));
   if(valid(s))host.render();
  }catch(e){if(valid(s)){error=e.message;host.render();}}
  finally{
   if(valid(s)){loading=false;if(data.jobs.some(j=>j.state==='running')&&(host.isVisible?.()??true))timer=setTimeout(()=>void load(),2000);}
  }
 }
 async function click(button){
  const action=button.dataset.relationAction;if(!action)return false;
  if(busy||!session.user||host.isDemo())return true;
  const s=stamp();busy=true;opened=true;error='';host.render();
  try{
   await flushStore();if(!valid(s))return true;
   if(action==='load'){await load();return true;}
   const id=button.dataset.relationId;
   if(action==='start')await api(root,{consent:true});
   else if(['apply','ignore','undo','stop'].includes(action))await api(root+'/'+encodeURIComponent(id)+'/'+action,action==='apply'?{reviewed:true}:{});
   if(!valid(s))return true;
   await host.changed(()=>valid(s));if(valid(s))await load();
  }catch(e){if(valid(s))error=e.message;}
  finally{if(valid(s)){busy=false;host.render();}}return true;
 }
 function render(){
  const active=data.jobs.find(j=>j.state==='running'),latest=data.jobs[0],pending=data.proposals.filter(p=>p.state==='review');
  const button=(action,label,id='')=>`<button class="btn quiet" data-relation-action="${action}" data-relation-id="${esc(id)}" ${busy?'disabled':''}>${label}</button>`;
  return `<details class="import-history relation-panel" data-relation-panel ${opened?'open':''}><summary>跨来源整合${pending.length?' · '+pending.length+' 项待核对':''}</summary><p class="status-note">同步后自动结合当前账号内相关课程原文、任务摘要和提交状态进行关联。仅接收入口显示已提交，也可完成它覆盖的全部交付。</p>${latest?`<p role="status">${esc(statuses[latest.state]||latest.state)}</p>${latest.error?`<p class="error">${esc(latest.error)}</p>`:''}`:''}${active?button('stop','停止关联',active.id):button('start','重新分析已保存内容')}${button('load','刷新整合结果')}<p class="status-note">点击重新分析会将上述相关内容发送给当前识别服务；不会重新爬取缺失内容。</p>${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${data.proposals.filter(p=>['review','applied'].includes(p.state)).map(p=>{
   const r=p.result,value=r.result,due=value.due;
   return `<article class="candidate-row"><div><h3>${esc(value.title)}</h3><p>${esc(value.description)}</p><p>${esc(value.course||'')} · ${due?esc(due.date+(due.time?' '+due.time:'')):'无截止日期'}</p><p>${esc(r.reason)}</p><details data-relation-details="${esc(p.id)}" ${details.get(p.id)?'open':''}><summary>查看关联依据与范围</summary>${(p.before||[]).map(b=>`<p>原内容：${esc(b.title||'')} · ${esc(b.description||'')}</p>`).join('')}${r.evidence.map(e=>`<blockquote>${esc(e.quote)}</blockquote>`).join('')}${r.completion.length?'<p>依据相应提交入口覆盖的交付范围判断完成，独立行动继续保留。</p>':''}</details>${p.state==='review'?button('apply','确认整合',p.id)+button('ignore','保持分开',p.id):`<p>已整合，全部原始来源保留在任务证据中。</p>${button('undo','撤回这次整合',p.id)}`}</div></article>`;
  }).join('')}</details>`;
 }
 return{load,click,render,reset,toggle};
}

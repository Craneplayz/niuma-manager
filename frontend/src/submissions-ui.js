import {api,session,accountVersion,flushStore} from './team-store.js';
import {newId} from './id.js';
const root='/api/sources/submissions',version='submission-operations/0.1';
const states={submitted:'已提交',active:'可提交',unknown:'状态未知'};
const decisions={pending:'待核对',confirmed:'未完成',edited:'已编辑',completed:'已完成',ignored:'已忽略',deleted:'已删除/已归档'};
export function createSubmissionsUI(host){
 const esc=host.escapeHtml;let rows=[],detail=null,source=null,opened=false,busy=false,error='',notice='',generation=0,scope='unknown',links={},operation=null;
 const stamp=()=>[generation,accountVersion()],valid=s=>s[0]===generation&&s[1]===accountVersion()&&session.user&&!host.isDemo();
 function reset(){generation++;rows=[];detail=source=null;opened=busy=false;error=notice='';scope='unknown';links={};operation=null;}
 async function directory(){
  const cred=await api('/api/sources/credentials');if(!cred.source_account_id)throw new Error('请先连接并同步教学网');
  let cursor=null,result=[];do{const page=await api(root+'?source_account_id='+encodeURIComponent(cred.source_account_id)+(cursor?'&cursor='+encodeURIComponent(cursor):''));result.push(...page.records);cursor=page.next_cursor;}while(cursor);return result;
 }
 async function read(id,current){
  const [next,original]=await Promise.all([api(root+'/'+encodeURIComponent(id)),api(root+'/'+encodeURIComponent(id)+'/source')]);
  if(!current())return;detail=next;source=original;scope=next.observation.scope;
  links=Object.fromEntries(next.observation.actions.map(a=>[a.action_id,a.relation]));operation=null;
 }
 async function click(button){
  const action=button.dataset.submissionAction;if(!action)return false;
  if(busy||!session.user||host.isDemo())return true;
  const s=stamp(),current=()=>valid(s);busy=true;error=notice='';opened=true;host.render();
  try{
   await flushStore();if(!current())return true;
   if(action==='load'){const next=await directory();if(current()){rows=next;if(detail)await read(detail.record_id,current);}}
   else if(action==='detail')await read(button.dataset.record,current);
   else if(detail){
    const obs=detail.observation;
    const body={contract_version:version,record_id:detail.record_id,expected_snapshot_id:obs.snapshot.snapshot_id,expected_control_revision:obs.control_revision,kind:action};
    if(action==='associate_actions')Object.assign(body,{scope,links:obs.actions.map(a=>({action_id:a.action_id,relation:links[a.action_id]}))});
    else body.action_ids=action==='resolve_source_review'?[]:[button.dataset.actionId];
    const signature=JSON.stringify(body);if(operation?.signature!==signature)operation={signature,id:newId()};body.operation_id=operation.id;
    await api(root+'/'+encodeURIComponent(detail.record_id)+'/operations',body);
    if(!current())return true;
    operation=null;await host.refresh();if(!current())return true;await read(detail.record_id,current);
    if(current())notice=action==='reopen_actions'?'已重新打开任务，并暂停来源自动完成。':'已保存；下次同步按新的关联处理。';
   }
  }catch(e){if(current())error=e.message;}
  finally{if(current()){busy=false;host.render();}}return true;
 }
 function input(event){
  const key=event.target.dataset?.submissionField;if(!key)return false;if(busy)return true;
  if(key==='scope')scope=event.target.value;else links[key]=event.target.value;operation=null;return true;
 }
 function render(){
  return `<details class="import-history" ${opened?'open':''}><summary>教学网提交状态与任务关联</summary><p class="status-note">仅在原生作业页确认提交状态。请明确哪些行动属于该作业的提交；未知关联不会被自动完成。</p><button class="btn" data-submission-action="load" ${busy?'disabled':''}>读取／刷新作业</button>${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}${rows.map(r=>`<button class="btn quiet" data-submission-action="detail" data-record="${esc(r.record_id)}" ${busy?'disabled':''}>${esc(r.course_or_project||'')} · ${esc(r.label)}</button>`).join('')}${detail?renderDetail():''}</details>`;
 }
 function sourceMarkup(){
  return (source?.records||[]).map(r=>{
   let url=null;try{const u=new URL(r.source_url);if(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password)url=u.href;}catch{}
   const refs=r.upstream_hints?.references||[];
   return `<details><summary>查看来源原文与附件摘录</summary><p style="white-space:pre-wrap">${esc(r.text||'正文未取得')}</p>${refs.map(ref=>`<h4>${esc(ref.label||ref.ref_id)} · ${esc(ref.read_state)}</h4><p style="white-space:pre-wrap">${esc(ref.text||'尚未取得')}</p>`).join('')}${r.text_quality!=='complete'||refs.some(ref=>ref.read_state!=='complete')?'<p class="status-note">来源未完整取得；可逐项关联已知行动，整份来源范围仍需补全后确认。</p>':''}${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开原网页</a>`:''}</details>`;
  }).join('');
 }
 function renderDetail(){
  const obs=detail.observation,labels=Object.fromEntries(detail.action_labels.map(a=>[a.action_id,a.label]));
  return `<section class="submission-detail"><h3>${esc(detail.label)} · ${esc(states[obs.snapshot.state])}</h3><p>观察时间：${esc(obs.snapshot.observed_at)}</p>${sourceMarkup()}${obs.review_pending?`<p class="error">提交状态发生过变化；确认当前来源后才能恢复自动处理。</p><button class="btn" data-submission-action="resolve_source_review" ${busy?'disabled':''}>已核对当前来源状态</button>`:''}<label class="field">整份通知范围<select data-submission-field="scope" ${busy?'disabled':''}>${[['unknown','尚不确定'],['submission_only','只有作业提交行动'],['mixed','同时包含其他独立行动']].map(([v,t])=>`<option value="${v}" ${scope===v?'selected':''}>${t}</option>`).join('')}</select></label>${obs.actions.map(a=>`<div class="candidate-row"><div><strong>${esc(labels[a.action_id])}</strong><p>${esc(decisions[a.decision])}${a.source_completion_paused?' · 已暂停来源自动完成':''}</p><label class="field">与本次提交的关系<select data-submission-field="${esc(a.action_id)}" ${busy?'disabled':''}>${[['unknown','尚不确定'],['assignment_submission','本次作业提交'],['other','独立的其他行动']].map(([v,t])=>`<option value="${v}" ${links[a.action_id]===v?'selected':''}>${t}</option>`).join('')}</select></label>${a.kind==='task'&&a.relation==='assignment_submission'&&!['deleted','ignored'].includes(a.decision)?`<button class="btn" data-submission-action="${a.source_completion_paused?'resume_source_completion':'reopen_actions'}" data-action-id="${esc(a.action_id)}" ${busy?'disabled':''}>${a.source_completion_paused?'恢复来源自动完成':'重新打开并暂停自动完成'}</button>`:''}</div></div>`).join('')}<p class="status-note">选择“只有作业提交行动”需将每项行动都明确关联为本次提交；确认后，已提交来源下次同步会完成对应任务、撤下对应候选，并保留你的忽略与删除选择。</p><button class="btn" data-submission-action="associate_actions" ${busy?'disabled':''}>确认以上范围与全部关联</button></section>`;
 }
 return{render,click,input,reset};
}

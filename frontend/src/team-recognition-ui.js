import {api,dataVersion,flushStore,session,accountVersion} from './store.js';
import {icon} from './icons.js';
import {startVoice,cancelVoice,voiceSupport} from './voice.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={create:'新增',update:'更新',postpone:'延期',cancel:'取消',uncertain:'待核实'};
const batchLabels={success:'识别完成',no_tasks:'未发现任务',partial_success:'部分识别完成',failed:'识别失败'};
let host={},batches=[],loaded=false,busy=false,error='',notice='',text='',published='',quality='complete',request=null,voiceState='idle',stopVoice=null,activeController=null;
export function configureAssistant(callbacks){host=callbacks;}
export function resetAssistant(){cancelVoice();activeController?.abort();activeController=null;batches=[];loaded=false;busy=false;error='';notice='';text='';published='';quality='complete';request=null;voiceState='idle';stopVoice=null;}
window.addEventListener('niuma:account-reset',resetAssistant);
export function leaveAssistant(){cancelVoice();voiceState='idle';}
const refresh=()=>{if(location.hash==='#agent')host.render();};
const pending=()=>batches.flatMap(b=>b.items).filter(i=>i.state==='pending').length;
function deadlineLabel(d){
 if(d.status==='not_provided')return '原文未提供 DDL';
 if(d.status==='conflict')return '截止时间冲突，请核对原文';
 if(d.kind==='reference')return '参考时间，不是 DDL：'+(d.value||d.raw||'待核实');
 if(d.status==='unknown')return '截止日期待核实';
 return (d.status==='inferred'?'推断时间，待核实：':d.precision==='date'?'明确截止日期（时刻待补充）：':'明确截止时间：')+(d.value||'待核实');
}
function suggestedDue(d){
 if(d.kind!=='deadline'||d.status!=='explicit'||!d.value)return {date:'',time:''};
 if(d.precision==='date')return {date:d.value,time:''};
 if(d.precision!=='datetime'||!Number.isFinite(Date.parse(d.value)))return {date:'',time:''};
 const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(d.value));
 const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
 return {date:p.year+'-'+p.month+'-'+p.day,time:p.hour+':'+p.minute};
}
async function load(refreshWorkspace=false){
 if(!session.user)return;
 const epoch=accountVersion();
 try{if(refreshWorkspace)await host.refresh();if(epoch!==accountVersion())return;const next=await api('/api/recognition/batches');if(epoch!==accountVersion())return;batches=next;loaded=true;error='';refresh();}
 catch(e){if(epoch!==accountVersion())return;error=e.message;loaded=true;refresh();}
}
export function assistantPage(){
 if(!loaded&&session.user){loaded=true;queueMicrotask(load);}
 const support=voiceSupport();
 return `<div class="page-heading"><div><div class="eyebrow">NIUMA ASSISTANT · v0.2</div><h1>先核对原文，再加入任务。</h1><p>识别结果先成为候选，确认后才进入日历。</p></div><span class="agent-mode">${icon('spark')}文本识别</span></div>
 <div class="agent-layout"><section class="panel agent-chat"><div class="agent-chat-header"><span class="agent-avatar">${icon('spark')}</span><div><b>小牛 · 识别审核</b><small>原文 → 候选 → 人工确认</small></div><button class="btn text-btn" data-agent-action="refresh" ${busy?'disabled':''}>刷新候选</button></div>
 <div class="agent-welcome"><div class="agent-orb">${icon('spark')}</div><h2>把通知交给小牛整理。</h2><p>明确的时间、推断的时间和参考时间会分开显示。模型服务由团队 Harness 提供。</p></div>
 <form id="agent-message-form" class="agent-composer"><label for="agent-input">粘贴或说出原文</label><textarea id="agent-input" rows="5" required maxlength="4000" placeholder="粘贴课程通知正文或描述任务…">${esc(text)}</textarea>
 <div class="form-grid"><label>文本完整度<select id="agent-quality"><option value="complete" ${quality==='complete'?'selected':''}>完整正文</option><option value="partial" ${quality==='partial'?'selected':''}>部分正文</option><option value="summary" ${quality==='summary'?'selected':''}>摘要或标题</option></select></label><label>原文发布日期（可选）<input id="agent-published" type="date" value="${published}"></label></div>
 <div class="composer-bar"><button class="voice-button" type="button" data-agent-action="voice" ${busy?'disabled':''}>${icon('mic')}${voiceState==='listening'?'说完了，开始整理':'点击说话'}</button><button class="btn primary" ${busy||!session.user?'disabled':''}>${busy?'识别中…':'识别为候选'}</button></div>
 <div class="button-row"><button class="btn" type="button" data-agent-action="new-batch" ${busy||!text.trim()?'disabled':''}>重新识别（新批次）</button>${busy?'<button class="btn" type="button" data-agent-action="stop">停止等待</button>':''}</div>
 <p class="voice-notice">${support.browser?'语音由浏览器识别，可能使用浏览器语音服务。':'当前浏览器可直接输入文字。'} 原文发布日期仅作为待核实信息，不以今天推断“明天”。</p></form>
 <div class="button-row"><button class="btn" data-agent-action="demo" ${busy||!session.user?'disabled':''}>加载人工协议演示</button></div>
 ${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}</section>
 <aside class="agent-result"><div class="result-heading"><div><span class="eyebrow">REVIEW BEFORE CALENDAR</span><h2>待确认 <span>${pending()}</span></h2></div>${icon('calendar')}</div>
 ${batches.length?batches.map(batchCard).join(''):'<div class="result-empty"><h3>候选会显示在这里</h3><p>没有真实模型服务时，可以加载明确标记的人工协议演示。</p></div>'}</aside></div>`;
}
function batchCard(batch){
 return `<section class="recognition-batch"><h3>${batchLabels[batch.result.status]||'识别结果'}${batch.demo?' · 人工演示':''}</h3><small>${batch.request.records.length} 条原文 · ${batch.items.filter(i=>i.state==='pending').length} 项待确认</small>
 ${batch.result.record_results.flatMap(r=>r.warnings.map(w=>`<p class="draft-warning">${esc(w)}</p>`)).join('')}
 ${batch.result.errors.map(e=>`<p class="error">${esc(e.message)}</p>`).join('')}
 <details><summary>查看原文与批次</summary><small>${esc(batch.request.request_id)}</small>${batch.request.records.map(r=>`<p>${esc(r.text)}</p>`).join('')}</details>
 ${batch.items.map(candidateCard).join('')}</section>`;
}
function candidateCard(item){
 const c=item.candidate;
 return `<article class="draft-card"><div class="draft-top"><span>${esc(labels[c.notice_type])} · ${item.state==='pending'?'待核对':item.state==='applied'?'已应用':'已忽略'}</span></div>
 <h3>${esc(c.title)}</h3><p>${esc(c.description)}</p><p>${esc(deadlineLabel(c.deadline))}</p>
 ${c.deadline.raw?`<small>时间原话：${esc(c.deadline.raw)}</small>`:''}
 ${c.review_reasons.map(r=>`<p class="draft-warning">${esc(r)}</p>`).join('')}
 ${c.prerequisites.length?`<details><summary>原文要求</summary><ul>${c.prerequisites.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></details>`:''}
 <details><summary>证据与来源</summary>${c.evidence.map(e=>`<blockquote>${esc(e.quote)}<small>${esc(e.input_id)} · ${esc(e.field)} · 支持 ${esc(e.supports.join('、'))}</small></blockquote>`).join('')}
 ${c.sources.map(s=>{let link='';try{const u=new URL(s.url);if(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password)link=` · <a href="${esc(u.href)}" target="_blank" rel="noreferrer">打开来源</a>`;}catch{}return `<p>${esc(s.label||s.input_id)} · ${esc(s.platform)}${link}</p>`}).join('')}</details>
 ${item.state==='pending'?`<div class="button-row"><button class="btn primary" data-agent-review="${esc(item.id)}" ${busy?'disabled':''}>核对并确认</button><button class="btn" data-agent-ignore="${esc(item.id)}" ${busy?'disabled':''}>忽略</button></div>`:''}</article>`;
}
function review(item,selectedAction=''){
 const c=item.candidate,d=suggestedDue(c.deadline),tasks=host.getState().tasks;
 const action=c.notice_type==='uncertain'?selectedAction:c.notice_type;
 host.openDialog(`<form id="candidate-form" data-candidate="${esc(item.id)}">${host.dialogHeader('核对'+labels[c.notice_type])}
 ${c.notice_type==='uncertain'?`<label>处理方式<select name="action" required><option value="">请选择</option>${['create','update','postpone','cancel'].map(value=>`<option value="${value}" ${action===value?'selected':''}>${labels[value]}</option>`).join('')}</select></label>`:`<input type="hidden" name="action" value="${c.notice_type}">`}
 ${action!=='create'?`<label>目标任务<select name="target_id" required><option value="">请选择当前账号的任务</option>${tasks.map(t=>`<option value="${esc(t.id)}">${esc(t.title)}</option>`).join('')}</select></label><p id="candidate-target-preview" class="panel-note">请核对目标任务。取消任务会保留记录，已有安排不再占用时间。</p>`:''}
 ${action!=='cancel'?`
 <label>任务名称<input name="title" required value="${esc(c.title)}" maxlength="100" ${action==='postpone'?'readonly':''}></label>
 <label>任务说明<textarea name="description" rows="3" ${action==='postpone'?'readonly':''}>${esc(c.description)}</textarea></label>
 <label>课程 / 项目<input name="course" value="${esc(c.course_or_project||'')}" ${action==='postpone'?'readonly':''}></label>
 <p>${esc(deadlineLabel(c.deadline))}</p><p class="panel-note">原文只有日期时，可以先保存为“时刻待补充”；不会自动补成 23:59。没有 DDL 可以留空。</p>
 <div class="form-grid"><label>真实 DDL 日期<input type="date" name="date" value="${d.date}" ${action==='postpone'?'required':''}></label><label>具体时刻（可稍后补充）<input type="time" name="time" value="${d.time}"></label></div>`:''}
 <label><input type="checkbox" name="reviewed" required> 我已核对原文证据、目标任务和变更</label>
 <p id="candidate-error" class="error" role="alert"></p><div class="dialog-actions"><button type="button" class="btn" data-agent-action="refresh-review" data-candidate="${esc(item.id)}" hidden>刷新后重新核对</button><button type="submit" class="btn primary">${action==='cancel'?'确认取消任务':action==='create'?'确认加入任务':'确认保存变更'}</button></div></form>`);
}
export function buildResolution(item,values,tasks,tasksVersion){
 const action=values.action,target=tasks.find(t=>t.id===values.target_id);
 if(!['create','update','postpone','cancel'].includes(action))throw new Error('请选择处理方式');
 if(values.reviewed!=='on')throw new Error('请先勾选核对确认');
 if(action==='create'&&values.target_id)throw new Error('新增任务不能指定目标任务');
 if(action!=='create'&&!target)throw new Error('请选择当前账号的目标任务');
 const body={action,version:item.version,tasks_version:tasksVersion,target_id:target?.id||null,reviewed:true};
 if(action==='cancel')return body;
 if(action==='postpone'&&!values.date)throw new Error('延期需要确认新的截止日期');
 if(values.time&&!values.date)throw new Error('填写截止时刻时，请同时填写日期');
 const title=action==='postpone'?target.title:values.title?.trim();
 if(!title)throw new Error('请输入任务名称');
 body.draft={title,description:action==='postpone'?(target.notes||''):(values.description||''),course:action==='postpone'?(target.course||null):(values.course?.trim()||null),
  due:values.date?{date:values.date,precision:values.time?'datetime':'date',...(values.time?{time:values.time}:{})}:null};
 return body;
}
async function resolve(form){
 if(busy)return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 const values=Object.fromEntries(new FormData(form)),item=batches.flatMap(b=>b.items).find(i=>i.id===form.dataset.candidate);
 const errorNode=form.querySelector('#candidate-error');
 if(!item||item.state!=='pending'){errorNode.textContent='该候选已经处理，请刷新后查看任务';return;}
 const submit=form.querySelector('[type="submit"]'),label=submit.textContent;
 const fields=[...form.querySelectorAll('input,select,textarea')];
 busy=true;errorNode.textContent='';submit.disabled=true;submit.textContent='正在保存…';
 fields.forEach(field=>field.disabled=true);
 try{
  await flushStore();
  if(!current())return;
  const body=buildResolution(item,values,host.getState().tasks,dataVersion('tasks'));
  const response=await api('/api/recognition/candidates/'+item.id+'/resolve',body);
  if(!current())return;
  batches=response.batches;host.closeDialog();notice=body.action==='create'?'已加入“我的任务”':body.action==='cancel'?'任务已取消':'任务已更新';
  try{await host.refresh();if(current())host.toast(notice);}
  catch(e){if(current())error='任务已保存，但页面刷新失败：'+e.message+'。请点击“刷新候选”重新读取。';}
 }catch(e){if(current()){errorNode.textContent=e.message;form.querySelector('[data-agent-action="refresh-review"]').hidden=e.status!==409;}}
 finally{if(current()){busy=false;fields.forEach(field=>field.disabled=false);submit.disabled=false;submit.textContent=label;refresh();}}
}
export async function submitRecognitionText(value){
 text=value.trim();if(!text||busy||!session.user)return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 const signature=JSON.stringify([text,published,quality]);
 if(request?.signature!==signature)request={signature,id:crypto.randomUUID(),collectedAt:new Date().toISOString()};
 const body={contract_version:'0.2',request_id:request.id,records:[{input_id:'manual:'+request.id,text,
  text_quality:quality,platform:'unknown',published_at:{value:published||null,precision:published?'date':'unknown',basis:published?'upstream_unverified':'unknown'},
  collected_at:request.collectedAt,timezone:'Asia/Shanghai',timezone_basis:'caller_default',source_url:null,source_label:'用户粘贴文本',
  source_item_id:null,course_or_project:null,context:[],upstream_hints:null}]};
 busy=true;error='';const controller=new AbortController();activeController=controller;const timeout=setTimeout(()=>controller.abort(),30000);refresh();
 try{const next=await api('/api/recognition/batches',body,'POST',{signal:controller.signal});if(!current())return;batches=next;loaded=true;notice='识别结果已保存为候选，尚未变更任务';}
 catch(e){if(current())error=controller.signal.aborted?'等待已停止；远端可能仍在处理。请刷新候选，必要时再重试。':e.message;}
 finally{clearTimeout(timeout);if(current()){activeController=null;busy=false;refresh();}}
}
export async function handleAssistantClick(button){
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 if(busy&&!['stop','voice'].includes(button.dataset.agentAction))return;
 if(button.dataset.agentReview){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentReview);if(item)review(item);return;}
 if(button.dataset.agentIgnore){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentIgnore);if(item)api('/api/recognition/candidates/'+item.id+'/resolve',{action:'ignore',version:item.version}).then(r=>{if(current()){batches=r.batches;refresh();}}).catch(e=>{if(current()){error=e.message;refresh();}});return;}
 if(button.dataset.agentAction==='refresh'){await load(true);return;}
 if(button.dataset.agentAction==='refresh-review'){
  const id=button.dataset.candidate;host.closeDialog();await load(true);if(!current())return;
  const item=batches.flatMap(b=>b.items).find(i=>i.id===id);
  if(item?.state==='pending')review(item);else host.toast('候选已处理，请在“我的任务”查看');
  return;
 }
 if(button.dataset.agentAction==='stop'){activeController?.abort();return;}
 if(button.dataset.agentAction==='new-batch'){request=null;submitRecognitionText(text);return;}
 if(button.dataset.agentAction==='demo'){api('/api/recognition/demo',{}).then(r=>{if(current()){batches=r;loaded=true;notice='这是人工合成协议样例，不是模型结果';refresh();}}).catch(e=>{if(current()){error=e.message;refresh();}});return;}
 if(button.dataset.agentAction==='voice'){
  if(voiceState==='listening'){stopVoice?.();return;}
  stopVoice=startVoice({onInterim:value=>{if(!current())return;text=value;const field=document.querySelector('#agent-input');if(field)field.value=value;},
   onResult:value=>{if(!current())return;text=value;submitRecognitionText(value);},onError:value=>{if(!current())return;error=value;refresh();},
   onState:value=>{if(!current())return;voiceState=value;refresh();}});return;
 }
}
export function handleAssistantInput(event){
 if(event.target.id==='agent-input')text=event.target.value;
 if(event.target.id==='agent-quality')quality=event.target.value;
 if(event.target.id==='agent-published')published=event.target.value;
 const candidateForm=event.target.closest?.('#candidate-form');
 if(candidateForm&&event.target.name!=='reviewed')candidateForm.elements.reviewed.checked=false;
 if(candidateForm&&event.target.name==='action'){
  const item=batches.flatMap(b=>b.items).find(i=>i.id===candidateForm.dataset.candidate);
  if(item)review(item,event.target.value);
  return;
 }
 if(event.target.name==='target_id'){
  const form=event.target.closest('#candidate-form'),task=host.getState().tasks.find(t=>t.id===event.target.value);
  const item=batches.flatMap(b=>b.items).find(i=>i.id===form?.dataset.candidate);
  if(!form||!item)return;
  form.elements.reviewed.checked=false;
  const preview=form.querySelector('#candidate-target-preview');
  preview.textContent=task?`当前：${task.title} · DDL ${task.due?task.due+(task.dueTime?' '+task.dueTime:'（时刻待补）'):'无'} · 已安排 ${task.plans?.length||0} 段`:'尚未选择目标任务';
  if(!task)return;
  const action=form.elements.action?.value,proposal=suggestedDue(item.candidate.deadline);
  if(action==='cancel')return;
  if(action==='update'&&!proposal.date){
   form.elements.date.value=task.due||'';
   form.elements.time.value=task.dueTime||'';
  }
  if(action==='update')form.elements.course.value=item.candidate.course_or_project||task.course||'';
  if(action==='postpone'){
   form.elements.title.value=task.title;
   form.elements.description.value=task.notes||'';
   form.elements.course.value=task.course||'';
  }
 }
}
export function handleAssistantSubmit(form){
 if(form.id==='agent-message-form'){submitRecognitionText(text);return true;}
 if(form.id==='candidate-form'){resolve(form);return true;}
 return false;
}

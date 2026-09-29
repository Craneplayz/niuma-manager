import {api,dataVersion,flushStore,session,accountVersion} from './store.js';
import {icon} from './icons.js';
import {startVoice,cancelVoice,voiceSupport} from './voice.js';
import {parseSourceFile,wechatRequest} from './source-import.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={create:'新增',update:'更新',postpone:'延期',cancel:'取消',uncertain:'待核实'};
const batchLabels={success:'识别完成',no_tasks:'未发现任务',partial_success:'部分识别完成',failed:'识别失败'};
let host={},batches=[],loaded=false,busy=false,error='',notice='',text='',published='',quality='complete',request=null,voiceState='idle',stopVoice=null,activeController=null,sourceImport=null,sourceName='',sourceConsent=false,selectedMessages=new Set(),syncSources=new Set(),syncConsent=false,sourceVersion=0,sourceBusy=false,credentialStatus=null,credentialBusy=false,editCredentials=false;
export function configureAssistant(callbacks){host=callbacks;}
export function resetAssistant(){cancelVoice();activeController?.abort();activeController=null;batches=[];loaded=false;busy=false;error='';notice='';text='';published='';quality='complete';request=null;voiceState='idle';stopVoice=null;sourceImport=null;sourceName='';sourceConsent=false;selectedMessages=new Set();syncSources=new Set();syncConsent=false;sourceVersion=0;sourceBusy=false;credentialStatus=null;credentialBusy=false;editCredentials=false;}
window.addEventListener('niuma:account-reset',resetAssistant);
export function leaveAssistant(){cancelVoice();voiceState='idle';}
const refresh=()=>{if(['#agent','#sources'].includes(location.hash))host.render();};
const pending=()=>batches.flatMap(b=>b.items).filter(i=>i.state==='pending').length;
export const canAutoApplyCandidate=c=>c.notice_type==='create'&&((c.deadline.kind==='deadline'&&c.deadline.status==='explicit'&&['date','datetime'].includes(c.deadline.precision))||c.deadline.status==='not_provided');
function displayTime(d){
 if(!d.value)return '';
 if(d.precision==='date')return d.value.replaceAll('-','/');
 if(!Number.isFinite(Date.parse(d.value)))return '';
 const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:d.timezone||'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(d.value));
 const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
 return `${p.year}/${p.month}/${p.day} ${p.hour}:${p.minute}`;
}
async function loadCredentials(){
 const epoch=accountVersion();
 try{const [status,workspace]=await Promise.all([api('/api/sources/credentials'),api('/api/workspace')]);if(epoch===accountVersion()){credentialStatus=status;syncSources=new Set(workspace.sources?.value||[]);sourceVersion=workspace.sources?.version||0;refresh();}}
 catch(e){if(epoch===accountVersion()){credentialStatus={configured:false,error:e.message};refresh();}}
}
function sourceSyncPanel(){
 const configured=credentialStatus?.configured;
 const credentialForm=!credentialStatus?.loading&&(!configured||editCredentials)?`<form id="source-credential-form" class="source-sync-credentials"><label>教学网账号<input name="username" type="text" required maxlength="200" autocomplete="off" placeholder="校园卡账号" value="${esc(credentialStatus?.username||'')}"></label><label>教学网密码<input name="password" type="password" required maxlength="200" autocomplete="new-password" placeholder="保存到当前账号"></label><button class="btn" ${credentialBusy?'disabled':''}>${credentialBusy?'保存中…':'保存教学网凭据'}</button></form>`:'';
 return `<section class="agent-source-sync"><h2>选择同步来源</h2><p>已勾选的来源可在工作台同步；来源识别出的新任务直接进入日历。</p><div class="source-sync-options"><label><input type="checkbox" data-agent-sync-source="teaching_web" ${syncSources.has('teaching_web')?'checked':''} ${busy||sourceBusy||!session.user?'disabled':''}> 教学网</label><label><input type="checkbox" disabled> 微信（当前仅支持下方会话文件导入）</label></div>${session.user?`<p>${configured?'教学网账号已保存：'+esc(credentialStatus.username):credentialStatus?.loading?'正在读取教学网凭据…':'先保存教学网账号和密码，之后无需重复输入。'}</p>${configured?`<div class="button-row"><button type="button" class="btn" data-agent-action="edit-credentials">${editCredentials?'取消修改':'更换凭据'}</button><button type="button" class="btn" data-agent-action="remove-credentials">删除凭据</button></div>`:''}${credentialForm}${credentialStatus?.error?`<p class="error">${esc(credentialStatus.error)}</p>`:''}`:''}<small>密码加密保存在当前账号的服务器上；微信暂不支持直接同步。</small></section>`;
}
function syncActionPanel(){return `<section class="agent-source-sync agent-sync-action"><h2>同步已选来源</h2><p>${syncSources.size?'当前来源：'+esc([...syncSources].map(s=>s==='teaching_web'?'教学网':s).join('、')):'尚未选择来源'} · <a href="#sources">管理来源与账号</a></p><label class="source-import-consent"><input id="agent-sync-consent" type="checkbox" ${syncConsent?'checked':''} ${busy||!session.user?'disabled':''}> 我同意将所选来源原文发送给文本识别服务。</label><button type="button" class="btn primary" data-agent-action="sync-sources" ${busy||!session.user?'disabled':''}>${busy?'正在同步或识别…':'同步并识别'}</button><p id="agent-sync-error" class="error" role="alert" hidden></p></section>`;}
export function sourceManagementPage(){
 if(session.user&&!credentialStatus){credentialStatus={loading:true};queueMicrotask(loadCredentials);}
 return `<div class="page-heading"><div><div class="eyebrow">SOURCES</div><h1>来源管理</h1><p>保存账号并选择来源，再回工作台同步。</p></div><a class="btn primary" href="#agent">返回工作台</a></div>${sourceSyncPanel()}${sourceImportPanel()}`;
}
function sourceImportPanel(){
 const imported=sourceImport?.kind==='requests'?`<p>${esc(sourceName)} · ${sourceImport.requests.length} 批 v0.2 来源记录</p><div class="source-import-list">${sourceImport.requests.map((batch,index)=>`<div class="source-import-row"><span>第 ${index+1} 批 · ${batch.records.length} 条 · ${esc([...new Set(batch.records.map(r=>r.platform||'unknown'))].join('、'))}</span><button type="button" class="btn" data-agent-import-index="${index}" ${busy||sourceImport.sent.has(index)?'disabled':''}>${sourceImport.sent.has(index)?'已提交':'识别此批'}</button></div>`).join('')}</div>`:
  sourceImport?.kind==='wechat'?`<p>${esc(sourceName)} · ${esc(sourceImport.chatName)} · 仅展示最近 ${sourceImport.messages.length} 条文本消息；只发送勾选内容，不发送账号、群成员 ID 或附件。</p><div class="source-import-list">${sourceImport.messages.map((message,index)=>`<label class="source-import-message"><input type="checkbox" data-agent-wechat-index="${index}" ${selectedMessages.has(index)?'checked':''} ${busy?'disabled':''}><span><small>${esc(message.time||'时间待核实')}</small>${esc(message.content.slice(0,180))}${message.content.length>180?'…':''}</span></label>`).join('')}</div><button type="button" class="btn" data-agent-action="import-wechat" ${busy?'disabled':''}>识别选中的微信消息</button>`:'';
 return `<details class="agent-source-import" ${sourceImport?'open':''}><summary>导入来源文件（课程网 / 微信）</summary><p>可选择课程网导出的 v0.2 JSONL，或微信模块导出的单个会话 JSON；文件先在本机预览，不会自动上传。</p><label>选择 JSON 或 JSONL 文件<input id="agent-source-file" type="file" accept=".json,.jsonl,application/json" ${busy||!session.user?'disabled':''}></label>${imported}${sourceImport?`<label class="source-import-consent"><input id="agent-import-consent" type="checkbox" ${sourceConsent?'checked':''} ${busy?'disabled':''}> 我确认：仅所选记录会保存至当前账号，并发送给已配置的文本识别模型。</label>`:''}</details>`;
}
export function deadlineLabel(d){
 if(d.status==='not_provided')return '原文未提供 DDL';
 if(d.status==='conflict')return '截止时间冲突，请核对原文';
 if(d.kind==='reference')return '参考时间，不是 DDL：'+(displayTime(d)||d.raw||'待核实');
 if(d.status==='unknown')return '截止日期待核实';
 return (d.status==='inferred'?'截止时间（待确认）：':d.precision==='date'?'明确截止日期（时刻待补充）：':'明确截止时间：')+(displayTime(d)||'待核实');
}
export function suggestedDue(d){
 if(d.kind!=='deadline'||!['explicit','inferred'].includes(d.status)||!d.value)return {date:'',time:''};
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
 if(session.user&&location.hash==='#agent'&&!credentialStatus){credentialStatus={loading:true};queueMicrotask(loadCredentials);}
 const support=voiceSupport();
 return `<div class="page-heading"><div><div class="eyebrow">NIUMA ASSISTANT · v0.2</div><h1>快速识别</h1><p>输入文字后先核对候选；来源同步识别出的新任务直接进入日历。</p></div><span class="agent-mode">${icon('spark')}文本识别</span></div>
 <div class="agent-layout"><section class="panel agent-chat"><div class="agent-chat-header"><span class="agent-avatar">${icon('spark')}</span><div><b>小牛 · 快速识别</b><small>原文 → 识别 → 日程</small></div><button class="btn text-btn" data-agent-action="refresh" ${busy?'disabled':''}>刷新结果</button></div>
 ${syncActionPanel()}
 <form id="agent-message-form" class="agent-composer"><label for="agent-input">粘贴或说出原文</label><textarea id="agent-input" rows="4" required maxlength="4000" placeholder="粘贴课程通知正文或描述任务…">${esc(text)}</textarea>
 <div class="form-grid"><label>文本完整度<select id="agent-quality"><option value="complete" ${quality==='complete'?'selected':''}>完整正文</option><option value="partial" ${quality==='partial'?'selected':''}>部分正文</option><option value="summary" ${quality==='summary'?'selected':''}>摘要或标题</option></select></label><label>原文发布日期（可选）<input id="agent-published" type="date" value="${published}"></label></div>
 <div class="composer-bar"><button class="voice-button" type="button" data-agent-action="voice" ${busy?'disabled':''}>${icon('mic')}${voiceState==='listening'?'说完了，开始整理':'点击说话'}</button><button class="btn primary" ${busy||!session.user?'disabled':''}>${busy?'识别中…':'识别并预览'}</button></div>
 <div class="button-row"><button class="btn" type="button" data-agent-action="new-batch" ${busy||!text.trim()?'disabled':''}>重新识别（新批次）</button>${busy?'<button class="btn" type="button" data-agent-action="stop">停止等待</button>':''}</div>
 <p class="voice-notice">${support.browser?'语音由浏览器识别，可能使用浏览器语音服务。':'当前浏览器可直接输入文字。'} 原文发布日期仅作为待核实信息，不以今天推断“明天”。</p></form>
 ${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}</section>
 <aside class="agent-result"><div class="result-heading"><div><span class="eyebrow">RECOGNITION RESULTS</span><h2>待确认 <span>${pending()}</span></h2></div>${icon('calendar')}</div>
 ${batches.length?batches.map(batchCard).join(''):'<div class="result-empty"><h3>识别结果会显示在这里</h3><p>手动输入的结果会在这里等待确认。</p></div>'}</aside></div>`;
}
function batchCard(batch){
 const incomplete=batch.result.record_results.filter(r=>r.warnings.includes('incomplete_text')).length;
 const failed=batch.result.record_results.filter(r=>r.status==='failed').length;
 return `<section class="recognition-batch"><h3>${batchLabels[batch.result.status]||'识别结果'}${batch.demo?' · 人工演示':''}</h3><small>${batch.request.records.length} 条原文 · ${batch.items.filter(i=>i.state==='pending').length} 项待确认</small>
 ${incomplete?`<p class="draft-warning">${incomplete} 条来源只有摘要或正文不完整；请核对原文后再确认任务。</p>`:''}
 ${batch.result.record_results.flatMap(r=>r.warnings.filter(w=>w!=='incomplete_text').map(w=>`<p class="draft-warning">${esc(w)}</p>`)).join('')}
 ${failed?`<p class="error">${failed} 条识别失败，尚未生成候选。</p>`:''}
 ${batch.result.errors.length?`<details><summary>查看识别问题</summary>${batch.result.errors.map(e=>{const source=batch.request.records.find(r=>r.input_id===e.input_id);return `<p>${esc(source?.course_or_project||source?.source_label||'批次')}：${esc(e.code==='agent_unavailable'?'模型服务不可用；请检查配置、网络或额度后重试':e.code==='agent_timeout'?'识别超时；请稍后重试':e.message)}</p>`}).join('')}</details>`:''}
 ${!batch.demo&&['failed','partial_success'].includes(batch.result.status)?`<button class="btn" data-agent-retry="${esc(batch.request.request_id)}" ${busy?'disabled':''}>重试失败记录</button>`:''}
 <details><summary>查看原文与批次</summary><small>${esc(batch.request.request_id)}</small>${batch.request.records.map(r=>`<p>${esc(r.text)}</p>`).join('')}</details>
 ${batch.items.map(candidateCard).join('')}</section>`;
}
export function candidateCard(item){
 const c=item.candidate;
 return `<article class="draft-card"><div class="draft-top"><span>${esc(labels[c.notice_type])} · ${item.state==='pending'?'待核对':item.state==='applied'?'已应用':'已忽略'}</span></div>
 <h3>${esc(c.title)}</h3><p>${esc(c.description)}</p><p>${esc(deadlineLabel(c.deadline))}</p>
 ${c.deadline.raw?`<small>时间原话：${esc(c.deadline.raw)}</small>`:''}
 ${c.review_reasons.filter(r=>!(c.deadline.status==='inferred'&&r===c.deadline.basis)).map(r=>`<p class="draft-warning">${esc(r)}</p>`).join('')}
 ${c.prerequisites.length?`<details><summary>原文要求</summary><ul>${c.prerequisites.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></details>`:''}
 <details><summary>证据与来源</summary><p>${esc(c.deadline.basis)}</p>${c.evidence.map(e=>`<blockquote>${esc(e.quote)}<small>${esc(e.input_id)} · ${esc(e.field)} · 支持 ${esc(e.supports.join('、'))}</small></blockquote>`).join('')}
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
async function submitRecognitionRequest(body,onSuccess,retry=false,autoApply=false){
 if(busy||!session.user)return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 busy=true;error='';const controller=new AbortController();activeController=controller;const timeout=setTimeout(()=>controller.abort(),100000);refresh();
 try{
  const next=await api(retry?'/api/recognition/retry':'/api/recognition/batches',body,'POST',{signal:controller.signal,timeoutMs:100000});
  if(!current())return;
  batches=next;loaded=true;
  const batch=next.find(batch=>batch.request.request_id===body.request_id),result=batch?.result;
  const complete=result&&['success','no_tasks'].includes(result.status);
  let applied=0;
  for(const item of batch?.items||[]){
   if(!current())return;
   if(!autoApply||item.state!=='pending'||!canAutoApplyCandidate(item.candidate))continue;
   try{const updated=await api('/api/recognition/candidates/'+item.id+'/resolve',{action:'create',auto_apply:true,version:item.version});if(!current())return;batches=updated.batches;applied++;}
   catch(e){if(current())error='自动加入日程失败：'+e.message;}
  }
  if(applied){try{await host.refresh();}catch(e){if(current())error='任务已加入日程，但页面刷新失败：'+e.message;}if(!current())return;}
  const remaining=batches.find(b=>b.request.request_id===body.request_id)?.items.filter(i=>i.state==='pending').length||0;
  notice=`${applied?`已自动加入 ${applied} 项日程；`:''}${remaining?`${remaining} 项${autoApply?'暂未加入':'待确认'}；`:''}${complete?'识别完成':'部分记录识别失败，可重试'}`;
  if(complete)onSuccess?.();
  return Boolean(complete);
 }
 catch(e){if(current())error=controller.signal.aborted?'等待已停止；远端可能仍在处理。请刷新候选，必要时再重试。':e.message;}
 finally{clearTimeout(timeout);if(current()){activeController=null;busy=false;refresh();}}
}
export async function submitRecognitionText(value){
 text=value.trim();if(!text||busy||!session.user)return;
 const signature=JSON.stringify([text,published,quality]);
 if(request?.signature!==signature)request={signature,id:crypto.randomUUID(),collectedAt:new Date().toISOString()};
 const body={contract_version:'0.2',request_id:request.id,records:[{input_id:'manual:'+request.id,text,
  text_quality:quality,platform:'unknown',published_at:{value:published||null,precision:published?'date':'unknown',basis:published?'upstream_unverified':'unknown'},
  collected_at:request.collectedAt,timezone:'Asia/Shanghai',timezone_basis:'caller_default',source_url:null,source_label:'用户粘贴文本',
  source_item_id:null,course_or_project:null,context:[],upstream_hints:null}]};
 await submitRecognitionRequest(body);
}
export async function handleAssistantClick(button){
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 if(busy&&!['stop','voice'].includes(button.dataset.agentAction))return;
 if(button.dataset.agentReview){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentReview);if(item)review(item);return;}
 if(button.dataset.agentIgnore){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentIgnore);if(item)api('/api/recognition/candidates/'+item.id+'/resolve',{action:'ignore',version:item.version}).then(r=>{if(current()){batches=r.batches;refresh();}}).catch(e=>{if(current()){error=e.message;refresh();}});return;}
 if(button.dataset.agentAction==='refresh'){await load(true);return;}
 if(button.dataset.agentRetry){
  const batch=batches.find(b=>b.request.request_id===button.dataset.agentRetry);
  if(batch&&!batch.demo)await submitRecognitionRequest(batch.request,()=>{
   const index=sourceImport?.requests?.findIndex(r=>r.request_id===batch.request.request_id);
   if(index>=0)sourceImport.sent.add(index);
  },true,batch.request.records.every(r=>r.platform!=='unknown'));
  return;
 }
 if(button.dataset.agentAction==='edit-credentials'){editCredentials=!editCredentials;refresh();return;}
 if(button.dataset.agentAction==='remove-credentials'){
  try{const status=await api('/api/sources/credentials/remove',{});if(current()){credentialStatus=status;editCredentials=false;notice='教学网凭据已删除';refresh();}}
  catch(e){if(current()){error=e.message;refresh();}}return;
 }
 if(button.dataset.agentAction==='sync-sources'){
  const showSyncError=message=>{const node=document.querySelector('#agent-sync-error');if(node){node.textContent=message;node.hidden=false;}};
  if(!syncSources.size){showSyncError('请先选择要同步的来源');return;}
  if(!syncConsent){showSyncError('请先确认同意发送所选来源的原文');return;}
  const controller=new AbortController();activeController=controller;busy=true;error='';notice='正在获取教学网信息…';refresh();
  try{
   const result=await api('/api/sources/sync',{sources:[...syncSources]},'POST',{signal:controller.signal});
   if(!current())return;
   if(!Array.isArray(result.requests))throw new Error('同步结果格式不正确');
   if(!result.requests.length){sourceImport=null;notice=`同步完成：跳过 ${result.summary?.skipped||0} 条已成功识别且未变化的通知，没有待识别内容`;return;}
   sourceImport={kind:'requests',requests:result.requests,sent:new Set()};sourceName='教学网增量同步（每条独立识别）';sourceConsent=true;
   busy=false;activeController=null;
   const total=sourceImport.requests.length,skipped=result.summary?.skipped||0;let failed=0,attempted=0;
   for(let index=0;index<total;index++){
    if(!current())return;
    const batch=sourceImport.requests[index];
    notice=`识别第 ${index+1}/${total} 条；跳过 ${skipped} 条未变化通知`;refresh();
    const complete=await submitRecognitionRequest(batch,()=>sourceImport.sent.add(index),true,true);
    if(!current())return;
    attempted++;
    if(!complete)failed++;
    if(complete===undefined)break;
   }
   if(current()){syncConsent=false;notice=`同步识别${failed||attempted<total?'尚未全部完成':'完成'}：成功 ${sourceImport.sent.size} 条，失败 ${failed} 条，未处理 ${total-attempted} 条，跳过 ${skipped} 条未变化通知。符合条件的任务会自动加入日历；${failed?'失败记录可重试。':'可在日历查看结果。'}`;}
  }catch(e){if(current()){notice='';error=e.status===404?'后端尚未提供来源同步接口，请使用文件导入':e.message;}}
  finally{if(current()){busy=false;activeController=null;refresh();}}
  return;
 }
 if(button.dataset.agentImportIndex!==undefined){
  const index=Number(button.dataset.agentImportIndex),batch=sourceImport?.kind==='requests'&&sourceImport.requests[index];
  if(!batch||sourceImport.sent.has(index))return;
  if(!sourceConsent){error='请先确认所选来源将发送给模型';refresh();return;}
  await submitRecognitionRequest(batch,()=>{sourceImport.sent.add(index);sourceConsent=false;},true,true);return;
 }
 if(button.dataset.agentAction==='import-wechat'){
  if(sourceImport?.kind!=='wechat')return;
  if(!sourceConsent){error='请先确认所选微信消息将发送给模型';refresh();return;}
  try{const batch=wechatRequest(sourceImport.messages,selectedMessages);if(new TextEncoder().encode(JSON.stringify(batch)).length>1_900_000)throw new Error('所选消息过大，请减少数量');await submitRecognitionRequest(batch,()=>{sourceImport=null;selectedMessages=new Set();sourceConsent=false;},false,true);}
  catch(e){error=e.message;refresh();}return;
 }
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
 if(event.target.id==='agent-source-file'&&event.type==='change'){
  const file=event.target.files?.[0],epoch=accountVersion();if(!file)return;
  sourceImport=null;sourceName='';selectedMessages=new Set();sourceConsent=false;
  if(file.size>10_000_000){error='文件超过 10 MB，请缩小导出范围';refresh();return;}
  file.text().then(raw=>{if(epoch!==accountVersion())return;sourceImport=parseSourceFile(raw);sourceName=file.name;selectedMessages=new Set();sourceConsent=false;error='';notice='文件仅在本机预览，尚未发送';refresh();}).catch(e=>{if(epoch!==accountVersion())return;error=e.message;refresh();});return;
 }
 if(event.target.id==='agent-import-consent')sourceConsent=event.target.checked;
 if(event.target.id==='agent-sync-consent')syncConsent=event.target.checked;
 if(event.target.dataset?.agentSyncSource){
  if(sourceBusy)return;
  const epoch=accountVersion(),previous=new Set(syncSources),source=event.target.dataset.agentSyncSource;
  if(event.target.checked)syncSources.add(source);else syncSources.delete(source);
  sourceBusy=true;refresh();
  api('/api/data/sources',{value:[...syncSources],version:sourceVersion},'PUT').then(result=>{if(epoch===accountVersion())sourceVersion=result.version;}).catch(e=>{if(epoch===accountVersion()){syncSources=previous;error='来源选择保存失败：'+e.message;}}).finally(()=>{if(epoch===accountVersion()){sourceBusy=false;refresh();}});
 }
 if(event.target.dataset?.agentWechatIndex!==undefined){const index=Number(event.target.dataset.agentWechatIndex);if(event.target.checked)selectedMessages.add(index);else selectedMessages.delete(index);}
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
 if(form.id==='source-credential-form'){
  if(credentialBusy)return true;
  const values=Object.fromEntries(new FormData(form)),epoch=accountVersion();credentialBusy=true;
  api('/api/sources/credentials',values).then(status=>{if(epoch===accountVersion()){credentialStatus=status;editCredentials=false;notice='教学网凭据已保存；以后可直接同步';error='';}})
   .catch(e=>{if(epoch===accountVersion())error=e.message;})
   .finally(()=>{if(epoch===accountVersion()){credentialBusy=false;refresh();}});
  return true;
 }
 if(form.id==='agent-message-form'){submitRecognitionText(text);return true;}
 if(form.id==='candidate-form'){resolve(form);return true;}
 return false;
}

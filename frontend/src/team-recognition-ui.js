import {waitingPanel} from './interaction-motion.js';
import {api,dataVersion,flushStore,session,accountVersion} from './store.js';
import {icon} from './icons.js';
import {pageIntro} from './story-layout.js';
import {startVoice,cancelVoice,voiceSupport} from './voice.js';
import {parseSourceFile,wechatRequest} from './source-import.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={create:'新增',update:'更新',postpone:'延期',cancel:'取消',uncertain:'待核实'};
const platformLabels={teaching_web:'教学网',wechat:'微信',email:'邮件',manual:'手动输入',unknown:'其他'};
let host={},batches=[],loaded=false,busy=false,error='',notice='',text='',published='',quality='complete',request=null,voiceState='idle',stopVoice=null,activeController=null,sourceImport=null,sourceName='',sourceConsent=false,selectedMessages=new Set(),syncSources=new Set(),syncConsent=false,sourceVersion=0,sourceBusy=false,credentialStatus=null,credentialBusy=false,editCredentials=false,importMode='manual',activeRequestIds=new Set(),candidateBusy=new Set(),importProgress={active:false,source:'',total:0,processed:0,created:0,failed:0,skipped:0};
export function configureAssistant(callbacks){host=callbacks;}
export function resetAssistant(){reviewScope='current';newPlanningTaskIds.clear();cancelVoice();activeController?.abort();activeController=null;batches=[];loaded=false;busy=false;error='';notice='';text='';published='';quality='complete';request=null;voiceState='idle';stopVoice=null;sourceImport=null;sourceName='';sourceConsent=false;selectedMessages=new Set();syncSources=new Set();syncConsent=false;sourceVersion=0;sourceBusy=false;credentialStatus=null;credentialBusy=false;editCredentials=false;importMode='manual';activeRequestIds=new Set();candidateBusy=new Set();importProgress={active:false,source:'',total:0,processed:0,created:0,failed:0,skipped:0};}
window.addEventListener('niuma:account-reset',resetAssistant);
export function leaveAssistant(){cancelVoice();voiceState='idle';}
const refresh=()=>{if(['#agent','#settings','#sources'].includes(location.hash))host.render();};
export const canAutoApplyCandidate=(c,batch)=>{
 const source=batch?.request?.records?.find(r=>r.input_id===c.input_id);
 const external=source&&['teaching_web','wechat'].includes(source.platform);
 const d=c.deadline;
 const exactMinute=d.precision!=='datetime'||Number.isFinite(Date.parse(d.value))&&new Date(d.value).getUTCSeconds()===0&&new Date(d.value).getUTCMilliseconds()===0;
 return Boolean(!batch?.demo&&external&&c.notice_type==='create'&&c.needs_review===false&&c.review_reasons?.length===0&&(
  d.kind==='deadline'&&d.status==='explicit'&&['date','datetime'].includes(d.precision)&&Boolean(d.value)&&exactMinute||
  d.status==='not_provided'&&d.value===null));
};
let reviewScope='current',newPlanningTaskIds=new Set();
const currentBatches=()=>reviewScope==='all'||!activeRequestIds.size?batches:batches.filter(batch=>activeRequestIds.has(batch.request.request_id));
const currentItems=state=>currentBatches().flatMap(batch=>batch.items.filter(item=>item.state===state));
const sourceFor=(item)=>{const batch=batches.find(batch=>batch.items.some(candidate=>candidate.id===item.id));return batch?.request.records.find(record=>record.input_id===item.candidate.input_id);};
function beginImport(requestIds,source,total=requestIds.length,skipped=0){reviewScope='current';activeRequestIds=new Set(requestIds);candidateBusy.clear();importProgress={active:true,source,total,processed:0,created:0,failed:0,skipped};error='';notice='';}
function queuePlanning(candidateIds){
 for(const task of host.getState?.().tasks||[])if(candidateIds.some(id=>task.recognitionHistory?.some(history=>history.storageId===id)))newPlanningTaskIds.add(task.id);
}
async function planNewTasks(current){
 if(!current())return;
 const state=host.getState?.(),ids=[...newPlanningTaskIds].filter(id=>{const task=state?.tasks.find(item=>item.id===id);return task?.due&&task.status!=='done'&&!task.planningExcluded&&!task.plans?.length&&!state.schedule?.some(block=>block.taskId===id);});
 newPlanningTaskIds.clear();
 if(ids.length)await host.proposeForTasks?.(ids);
}
async function planReviewedCandidate(item,action,current){
 const batch=batches.find(batch=>batch.items.some(candidate=>candidate.id===item.id));
 if(action==='create'&&!batch?.demo&&['teaching_web','wechat'].includes(sourceFor(item)?.platform)){
  queuePlanning([item.id]);await planNewTasks(current);
 }
}
function displayTime(d){
 if(!d.value)return '';
 if(d.precision==='date')return d.value.replaceAll('-','/');
 if(!Number.isFinite(Date.parse(d.value)))return '';
 const stamp=new Date(d.value),withSeconds=stamp.getUTCSeconds()!==0||stamp.getUTCMilliseconds()!==0;
 const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:d.timezone||'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(withSeconds?{second:'2-digit'}:{}),hourCycle:'h23'}).formatToParts(stamp);
 const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
 return `${p.year}/${p.month}/${p.day} ${p.hour}:${p.minute}${withSeconds?':'+p.second:''}`;
}
async function loadCredentials(){
 const epoch=accountVersion();
 try{const [status,workspace]=await Promise.all([api('/api/sources/credentials'),api('/api/workspace')]);if(epoch===accountVersion()){credentialStatus=status;syncSources=new Set(workspace.sources?.value||[]);sourceVersion=workspace.sources?.version||0;refresh();}}
 catch(e){if(epoch===accountVersion()){credentialStatus={configured:false,error:e.message};refresh();}}
}
function sourceSyncPanel(){
 const configured=credentialStatus?.configured;
 const credentialForm=!credentialStatus?.loading&&(!configured||editCredentials)?`<form id="source-credential-form" class="source-sync-credentials"><label>教学网账号<input name="username" type="text" required maxlength="200" autocomplete="off" placeholder="校园卡账号" value="${esc(credentialStatus?.username||'')}"></label><label>教学网密码<input name="password" type="password" required maxlength="200" autocomplete="new-password" placeholder="保存到当前账号"></label><button class="btn" ${credentialBusy?'disabled':''}>${credentialBusy?'保存中…':'保存教学网凭据'}</button></form>`:'';
 return `<section class="agent-source-sync"><h2>选择同步来源</h2><div class="source-sync-options"><label><input type="checkbox" data-agent-sync-source="teaching_web" ${syncSources.has('teaching_web')?'checked':''} ${busy||sourceBusy||!session.user?'disabled':''}> 教学网</label><label><input type="checkbox" disabled> 微信（仅支持文件导入）</label></div>${session.user?`<p>${configured?'教学网账号已保存：'+esc(credentialStatus.username):credentialStatus?.loading?'正在读取教学网凭据…':'请先保存教学网账号和密码。'}</p>${configured?`<div class="button-row"><button type="button" class="btn" data-agent-action="edit-credentials">${editCredentials?'取消修改':'更换凭据'}</button><button type="button" class="btn" data-agent-action="remove-credentials">删除凭据</button></div>`:''}${credentialForm}${credentialStatus?.error?`<p class="error">${esc(credentialStatus.error)}</p>`:''}`:''}</section>`;
}
function syncActionPanel(){return `<section class="agent-source-sync agent-sync-action"><h2>同步已选来源</h2><p>${syncSources.size?'当前来源：'+esc([...syncSources].map(s=>s==='teaching_web'?'教学网':s).join('、')):'尚未选择来源'} · <a href="#settings">管理来源与账号</a></p><label class="source-import-consent"><input id="agent-sync-consent" type="checkbox" ${syncConsent?'checked':''} ${busy||!session.user?'disabled':''}> 我同意将所选来源原文发送给文本识别服务。</label><button type="button" class="btn primary" data-agent-action="${busy?'stop':'sync-sources'}" ${!session.user?'disabled':''}>${busy?'停止导入':'同步并识别'}</button><p id="agent-sync-error" class="error" role="alert" hidden></p></section>`;}
export function sourceManagementPage(embedded=false){
 if(session.user&&!credentialStatus){credentialStatus={loading:true};queueMicrotask(loadCredentials);}
 const content=`${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}${sourceSyncPanel()}${sourceImportPanel()}`;
 return embedded?`<div class="settings-source-management"><hr><h2>来源管理</h2>${content}</div>`:`<div class="page-heading"><h1>来源管理</h1></div>${content}`;
}
function sourceImportPanel(standalone=false){
 const imported=sourceImport?.kind==='requests'?`<p>${esc(sourceName)} · ${sourceImport.requests.length} 批记录</p><div class="source-import-list">${sourceImport.requests.map((batch,index)=>`<div class="source-import-row"><span>第 ${index+1} 批 · ${batch.records.length} 条 · ${esc([...new Set(batch.records.map(r=>platformLabels[r.platform]||'其他'))].join('、'))}</span><button type="button" class="btn" data-agent-import-index="${index}" ${busy||sourceImport.sent.has(index)?'disabled':''}>${sourceImport.sent.has(index)?'已提交':'识别此批'}</button></div>`).join('')}</div>`:
  sourceImport?.kind==='wechat'?`<p>${esc(sourceName)} · ${esc(sourceImport.chatName)} · 仅展示最近 ${sourceImport.messages.length} 条文本消息；只发送勾选内容，不发送账号、群成员 ID 或附件。</p><div class="source-import-list">${sourceImport.messages.map((message,index)=>`<label class="source-import-message"><input type="checkbox" data-agent-wechat-index="${index}" ${selectedMessages.has(index)?'checked':''} ${busy?'disabled':''}><span><small>${esc(message.time||'时间待核实')}</small>${esc(message.content.slice(0,180))}${message.content.length>180?'…':''}</span></label>`).join('')}</div><button type="button" class="btn" data-agent-action="import-wechat" ${busy?'disabled':''}>识别选中的微信消息</button>`:'';
 const content=`<p>文件先在本机预览，不会自动上传。</p><label>${standalone?'选择微信导出文件':'选择文件'}<input id="agent-source-file" type="file" accept=".json,.jsonl,application/json" ${busy||!session.user?'disabled':''}></label>${imported}${sourceImport?`<label class="source-import-consent"><input id="agent-import-consent" type="checkbox" ${sourceConsent?'checked':''} ${busy?'disabled':''}> 我同意将所选记录发送给文本识别服务。</label>`:''}${busy?'<button type="button" class="btn" data-agent-action="stop">停止导入</button>':''}`;
 return standalone?`<div class="agent-source-import source-import-standalone">${content}</div>`:`<details class="agent-source-import" ${sourceImport?'open':''}><summary>导入来源文件（课程网 / 微信）</summary>${content}</details>`;
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
 const stamp=new Date(d.value);
 const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(stamp);
 const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
 return {date:p.year+'-'+p.month+'-'+p.day,time:stamp.getUTCSeconds()||stamp.getUTCMilliseconds()?'':p.hour+':'+p.minute};
}
async function load(refreshWorkspace=false){
 if(!session.user)return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 try{if(refreshWorkspace)await host.refresh();if(!current())return;const next=await api('/api/recognition/batches');if(!current())return;batches=next;error='';loaded=true;refresh();}
 catch(e){if(epoch!==accountVersion())return;error=e.message;loaded=true;refresh();}
}
async function autoApplyCandidates(requestId,current,previousIds,resumeAutoApply=false){
 let applied=0,failed=0;const appliedIds=[];
 const items=batches.filter(batch=>!batch.demo&&batch.request.request_id===requestId).flatMap(batch=>batch.items.filter(item=>(!previousIds.has(item.id)||resumeAutoApply&&item.version===1)&&item.state==='pending'&&canAutoApplyCandidate(item.candidate,batch)));
 for(const item of items){
  if(!current())return {applied,failed};
  try{const updated=await api('/api/recognition/candidates/'+item.id+'/resolve',{action:'create',auto_apply:true,version:item.version});if(!current())return {applied,failed};batches=updated.batches;applied++;appliedIds.push(item.id);}
  catch(e){if(current()){failed++;error='自动加入任务失败：'+e.message+'；可再次同步补上未创建的任务。';}}
 }
 if(applied){try{await host.refresh();if(current())queuePlanning(appliedIds);}catch(e){if(current())error='任务已创建，但页面刷新失败：'+e.message;}}
 return {applied,failed};
}
function manualImportPanel(support){return `<form id="agent-message-form" class="agent-composer import-manual-composer"><label for="agent-input">粘贴或说出原文</label><textarea id="agent-input" rows="4" required maxlength="4000" placeholder="粘贴课程通知正文或描述任务…">${esc(text)}</textarea>
 <div class="form-grid"><label>文本完整度<select id="agent-quality"><option value="complete" ${quality==='complete'?'selected':''}>完整正文</option><option value="partial" ${quality==='partial'?'selected':''}>部分正文</option><option value="summary" ${quality==='summary'?'selected':''}>摘要或标题</option></select></label><label>原文发布日期（可选）<input id="agent-published" type="date" value="${published}"></label></div>
 <div class="composer-bar"><button class="voice-button" type="button" data-agent-action="voice" ${busy?'disabled':''}>${icon('mic')}${voiceState==='listening'?'说完了，开始整理':'点击说话'}</button><button class="btn primary" ${busy||!session.user?'disabled':''}>${busy?'识别中…':'识别通知'}</button></div>
 <div class="button-row">${text.trim()?`<button class="btn" type="button" data-agent-action="new-batch" ${busy?'disabled':''}>重新识别</button>`:''}${busy?'<button class="btn" type="button" data-agent-action="stop">停止等待</button>':''}</div>
 ${support.browser?'<p class="voice-notice">语音输入可能使用浏览器语音服务。</p>':''}</form>`;}
function importModePanel(support){
 if(importMode==='wechat')return sourceImportPanel(true);
 if(importMode==='manual')return manualImportPanel(support);
 return syncActionPanel();
}
const importModes=()=>[['manual','粘贴通知'],...(syncSources.has('teaching_web')?[['teaching_web','教学网']]:[]),['wechat','微信']];
export function assistantPage(){
 if(!loaded&&session.user){loaded=true;queueMicrotask(load);}
 if(session.user&&location.hash==='#agent'&&!credentialStatus){credentialStatus={loading:true};queueMicrotask(loadCredentials);}
 const support=voiceSupport();
 const review=currentItems('pending'),ignored=currentItems('ignored');
 const importStarted=importProgress.active||activeRequestIds.size>0;
 const allPending=batches.flatMap(batch=>batch.items).filter(item=>item.state==='pending').length,allIgnored=batches.flatMap(batch=>batch.items).filter(item=>item.state==='ignored').length;
 const showReview=importStarted||allPending||allIgnored;
 const failedBatches=batches.filter(batch=>(reviewScope==='all'||activeRequestIds.has(batch.request.request_id))&&!batch.demo&&['failed','partial_success'].includes(batch.result.status));
 const progressTotal=Math.max(importProgress.total,1),progress=Math.min(100,Math.round(importProgress.processed/progressTotal*100));
 const modes=importModes();if(!modes.some(([value])=>value===importMode))importMode='manual';
 return `<div class="story-capture"><div class="story-capture-copy">${pageIntro('agent')}<div class="story-capture-note"><span>从哪里开始？</span><p>粘贴一条通知，或选择已连接的教学网、微信导出文件。原文与来源会和任务一起保留。</p><a href="#settings" class="story-link">管理通知来源 ${icon('arrow')}</a></div></div><section class="panel agent-chat import-entry-card"><div class="agent-chat-header import-entry-header"><div class="import-source-tabs" role="tablist" aria-label="导入方式">${modes.map(([value,label])=>`<button type="button" role="tab" aria-selected="${importMode===value}" class="import-source-tab ${importMode===value?'active':''}" data-agent-source-mode="${value}" ${busy&&importMode!==value?'disabled':''}>${label}</button>`).join('')}</div><button class="btn text-btn" data-agent-action="refresh" ${busy?'disabled':''}>刷新结果</button></div>
 <div class="import-mode-panel">${importModePanel(support)}</div>
 ${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}
 ${failedBatches.length?`<div class="agent-failures">${failedBatches.map(batch=>`<p class="draft-warning">${batch.result.record_results.some(r=>r.warnings.includes('incomplete_text'))?'来源正文不完整；':''}${batch.result.errors.map(e=>{const source=batch.request.records.find(r=>r.input_id===e.input_id);return esc(source?.course_or_project||source?.source_label||'通知')+'：'+esc(e.code==='agent_timeout'?'识别超时；请稍后重试':e.code==='agent_unavailable'?'模型服务不可用；请检查配置、网络或额度后重试':e.message)}).join('；')}</p><button class="btn" data-agent-retry="${esc(batch.request.request_id)}" ${busy?'disabled':''}>重试失败记录</button>`).join('')}</div>`:''}</section></div><div class="notification-import">
 ${busy?waitingPanel('正在导入并识别通知','识别结果返回后会更新下方内容；你可以随时停止等待。'):''}
 ${importStarted?`<section class="import-progress ${importProgress.active?'active':''}" aria-label="导入进度"><div><strong>${esc(importProgress.source||'本次导入')}</strong><span>${importProgress.active?`${importProgress.processed} / ${importProgress.total||'…'}`:'导入完成'}</span></div><div class="import-progress-track"><i style="width:${importProgress.active&&importProgress.total?progress:100}%"></i></div><small>已创建 ${importProgress.created} 项${importProgress.failed?` · 失败 ${importProgress.failed} 项`:''}${importProgress.skipped?` · 跳过 ${importProgress.skipped} 项`:''}</small></section>
 `:''}
 ${showReview?`<div class="button-row" role="group" aria-label="候选范围"><button class="btn" data-agent-review-scope="current" aria-pressed="${reviewScope==='current'&&activeRequestIds.size>0}" ${!activeRequestIds.size?'disabled':''}>本次导入</button><button class="btn" data-agent-review-scope="all" aria-pressed="${reviewScope==='all'||!activeRequestIds.size}">账号全部候选 · 待核对 ${allPending} · 已忽略 ${allIgnored}</button></div>
 <section class="import-review-section"><div class="result-heading"><h2>待核对 <span>${review.length}</span></h2></div>${review.map(candidateCard).join('')}${!review.length?'<div class="result-empty compact"><h3>暂无待核对内容</h3></div>':''}</section>
 <details class="import-ignored-section"><summary>不加入任务 <span>${ignored.length}</span></summary><div>${ignored.map(candidateCard).join('')}${!ignored.length?'<div class="result-empty compact"><h3>暂无内容</h3></div>':''}</div></details>`:''}</div>`;
}
export function candidateCard(item){
 const c=item.candidate,source=sourceFor(item),working=candidateBusy.has(item.id);
 const sourceLabel=c.sources?.[0]?.label||source?.course_or_project||source?.source_label||platformLabels[source?.platform]||'通知来源';
 return `<article class="import-review-card ${item.state==='ignored'?'ignored':''}" data-candidate-card="${esc(item.id)}">
 ${item.state==='pending'?`<div class="candidate-actions" role="group" aria-label="候选操作"><button class="candidate-create-button" data-agent-confirm="${esc(item.id)}" aria-label="创建任务" title="加入任务" ${working?'disabled':''}>${working?'…':icon('check')}</button><button class="candidate-dismiss-button" data-agent-ignore="${esc(item.id)}" aria-label="不加入任务" title="不加入任务" ${working?'disabled':''}>${icon('close')}</button></div>`:''}
 ${item.state==='ignored'?`<div class="candidate-actions"><button class="candidate-restore-button" data-agent-restore="${esc(item.id)}" ${working?'disabled':''}>${working?'撤回中…':'撤回'}</button></div>`:''}
 <div class="candidate-content"><div class="candidate-type">${esc(labels[c.notice_type])}</div><h3>${esc(c.title)}</h3><div class="candidate-meta"><span>${icon('clock')}${esc(deadlineLabel(c.deadline))}</span>${c.course_or_project?`<span>${icon('book')}${esc(c.course_or_project)}</span>`:''}</div><p class="candidate-source">来源：${esc(sourceLabel)}</p>
 <details class="candidate-evidence"><summary>查看证据</summary>${c.description?`<p>${esc(c.description)}</p>`:''}${c.deadline.basis?`<p>${esc(c.deadline.basis)}</p>`:''}${(c.review_reasons||[]).filter(reason=>reason!==c.deadline.basis).map(reason=>`<p class="draft-warning">${esc(reason)}</p>`).join('')}${(c.evidence||[]).map(e=>`<blockquote>${esc(e.quote)}</blockquote>`).join('')}${(c.sources||[]).map(s=>{let link='';try{const u=new URL(s.url);if(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password)link=`<a href="${esc(u.href)}" target="_blank" rel="noreferrer">打开来源</a>`;}catch{}return `<p>${esc(s.label||platformLabels[s.platform]||'来源')} ${link}</p>`}).join('')}</details></div></article>`;
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
 <p>${esc(deadlineLabel(c.deadline))}</p><p class="panel-note">${c.deadline.precision==='datetime'&&c.deadline.value&&(new Date(c.deadline.value).getUTCSeconds()||new Date(c.deadline.value).getUTCMilliseconds())?'原文时间精确到秒，当前日程只支持分钟；请核对后手动填写时刻。':'原文只有日期时，可以先保存为“时刻待补充”；不会自动补成 23:59。没有 DDL 可以留空。'}</p>
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
export function buildQuickResolution(item,tasksVersion){
 const c=item.candidate,d=suggestedDue(c.deadline);
 return {action:'create',version:item.version,tasks_version:tasksVersion,target_id:null,reviewed:true,draft:{title:c.title,description:c.description||'',course:c.course_or_project||null,due:d.date?{date:d.date,precision:d.time?'datetime':'date',...(d.time?{time:d.time}:{})}:null}};
}
async function resolve(form){
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 const values=Object.fromEntries(new FormData(form)),item=batches.flatMap(b=>b.items).find(i=>i.id===form.dataset.candidate);
 const errorNode=form.querySelector('#candidate-error');
 if(!item||item.state!=='pending'){errorNode.textContent='该候选已经处理，请刷新后查看任务';return;}
 if(candidateBusy.has(item.id))return;
 const submit=form.querySelector('[type="submit"]'),label=submit.textContent;
 const fields=[...form.querySelectorAll('input,select,textarea')];
 candidateBusy.add(item.id);errorNode.textContent='';submit.disabled=true;submit.textContent='正在保存…';
 fields.forEach(field=>field.disabled=true);
 try{
  await flushStore();
  if(!current())return;
  const body=buildResolution(item,values,host.getState().tasks,dataVersion('tasks'));
  const response=await api('/api/recognition/candidates/'+item.id+'/resolve',body);
  if(!current())return;
  batches=response.batches;host.closeDialog();notice=body.action==='create'?'已加入任务中心':body.action==='cancel'?'任务已取消':'任务已更新';
  try{
   await host.refresh();
   if(!current())return;
   host.toast(notice);
   await planReviewedCandidate(item,body.action,current);
  }
  catch(e){if(current())error='任务已保存，但页面刷新失败：'+e.message+'。请点击“刷新候选”重新读取。';}
 }catch(e){if(current()){errorNode.textContent=e.message;form.querySelector('[data-agent-action="refresh-review"]').hidden=e.status!==409;}}
 finally{candidateBusy.delete(item.id);if(current()){fields.forEach(field=>field.disabled=false);submit.disabled=false;submit.textContent=label;refresh();}}
}
async function submitRecognitionRequest(body,onSuccess,retry=false,autoApply=false,planAfter=true,resumeAutoApply=false){
 if(busy||!session.user)return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 const external=autoApply&&body.records.some(record=>['teaching_web','wechat'].includes(record.platform));
 busy=true;error='';const controller=new AbortController();activeController=controller;const timeout=setTimeout(()=>controller.abort(),100000);refresh();
 try{
  const existing=external?await api('/api/recognition/batches',undefined,'GET',{signal:controller.signal,timeoutMs:100000}):batches;
  if(!current())return;
  const previousIds=new Set(existing.find(batch=>batch.request.request_id===body.request_id)?.items.map(item=>item.id)||[]);
  const next=await api(retry?'/api/recognition/retry':'/api/recognition/batches',body,'POST',{signal:controller.signal,timeoutMs:100000});
  if(!current())return;
  batches=next;loaded=true;
  const batch=next.find(batch=>batch.request.request_id===body.request_id),result=batch?.result;
  const recognized=result&&['success','no_tasks'].includes(result.status);
  const {applied,failed}=autoApply?await autoApplyCandidates(body.request_id,current,previousIds,resumeAutoApply):{applied:0,failed:0};
  const complete=recognized&&!failed;
  if(!current())return;
  if(planAfter&&external)await planNewTasks(current);
  if(!current())return;
  if(activeRequestIds.has(body.request_id))importProgress.created+=applied;
  const remaining=batches.find(b=>b.request.request_id===body.request_id)?.items.filter(item=>item.state==='pending').length||0;
  const filtered=result?.record_results?.filter(r=>r.warnings.some(w=>w.startsWith('history_filtered:')||w.startsWith('no_action:'))).length||0;
  notice=`${applied?`已自动加入 ${applied} 项任务；`:''}${remaining?`${remaining} 项待处理；`:''}${filtered?`${filtered} 条资讯或历史通知未生成任务；`:''}${failed?`${failed} 项自动加入失败，请再次同步；`:''}${recognized?'识别完成':'部分记录识别失败，可重试'}`;
  if(complete)onSuccess?.();
  if(activeRequestIds.has(body.request_id)){importProgress.processed++;if(!complete)importProgress.failed++;}
  return Boolean(complete);
 }
 catch(e){if(current()){if(activeRequestIds.has(body.request_id)){importProgress.processed++;importProgress.failed++;}error=controller.signal.aborted?'等待已停止；远端可能仍在处理。请刷新候选，必要时再重试。':e.message;}}
 finally{clearTimeout(timeout);if(current()){activeController=null;busy=false;refresh();}}
}
export async function submitRecognitionText(value){
 text=value.trim();if(!text||busy||!session.user)return;
 const signature=JSON.stringify([text,published,quality]);
 if(request?.signature!==signature)request={signature,id:crypto.randomUUID(),collectedAt:new Date().toISOString()};
 const body={contract_version:'0.2',request_id:request.id,records:[{input_id:'manual:'+request.id,text,
  text_quality:quality,platform:'manual',published_at:{value:published||null,precision:published?'date':'unknown',basis:published?'upstream_unverified':'unknown'},
  collected_at:request.collectedAt,timezone:'Asia/Shanghai',timezone_basis:'caller_default',source_url:null,source_label:'用户粘贴文本',
  source_item_id:null,course_or_project:null,context:[],upstream_hints:null}]};
 beginImport([body.request_id],'手动通知',1);
 await submitRecognitionRequest(body,undefined,false,true);
 importProgress.active=false;refresh();
}
async function quickCreate(item,current){
 if(!item||item.state!=='pending'||candidateBusy.has(item.id))return;
 candidateBusy.add(item.id);error='';refresh();
 try{
  await flushStore();if(!current())return;
  const response=await api('/api/recognition/candidates/'+item.id+'/resolve',buildQuickResolution(item,dataVersion('tasks')));
  if(!current())return;batches=response.batches;importProgress.created++;
  await host.refresh?.();if(current()){host.toast?.('任务已创建');await planReviewedCandidate(item,'create',current);}
 }catch(e){if(current())error=e.message;}
 finally{candidateBusy.delete(item.id);if(current())refresh();}
}
async function ignoreCandidate(item,current){
 if(!item||item.state!=='pending'||candidateBusy.has(item.id))return;
 candidateBusy.add(item.id);refresh();
 try{const response=await api('/api/recognition/candidates/'+item.id+'/resolve',{action:'ignore',version:item.version});if(current())batches=response.batches;}
 catch(e){if(current())error=e.message;}
 finally{candidateBusy.delete(item.id);if(current())refresh();}
}
async function restoreCandidate(item,current){
 if(!item||item.state!=='ignored'||candidateBusy.has(item.id))return;
 candidateBusy.add(item.id);refresh();
 try{const response=await api('/api/recognition/candidates/'+item.id+'/resolve',{action:'reopen',version:item.version});if(current())batches=response.batches;}
 catch(e){if(current())error=e.message;}
 finally{candidateBusy.delete(item.id);if(current())refresh();}
}
export async function handleAssistantClick(button){
 const epoch=accountVersion(),current=()=>epoch===accountVersion();
 if(['current','all'].includes(button.dataset.agentReviewScope)){reviewScope=button.dataset.agentReviewScope;refresh();return;}
 if(button.dataset.agentSourceMode){if(!busy&&importModes().some(([value])=>value===button.dataset.agentSourceMode)){importMode=button.dataset.agentSourceMode;error='';notice='';refresh();}return;}
 const candidateAction=button.dataset.agentConfirm||button.dataset.agentReview||button.dataset.agentIgnore||button.dataset.agentRestore;
 if(busy&&!candidateAction&&!['stop','voice'].includes(button.dataset.agentAction))return;
 if(button.dataset.agentEditTask){host.editTask?.(button.dataset.agentEditTask);return;}
 if(button.dataset.agentConfirm){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentConfirm);if(item?.candidate.notice_type==='create')await quickCreate(item,current);else if(item)review(item);return;}
 if(button.dataset.agentReview){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentReview);if(item)review(item);return;}
 if(button.dataset.agentIgnore){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentIgnore);await ignoreCandidate(item,current);return;}
 if(button.dataset.agentRestore){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentRestore);await restoreCandidate(item,current);return;}
 if(button.dataset.agentAction==='refresh'){await load(true);return;}
 if(button.dataset.agentRetry){
  const batch=batches.find(b=>b.request.request_id===button.dataset.agentRetry);
  if(batch&&!batch.demo)await submitRecognitionRequest(batch.request,()=>{
   const index=sourceImport?.requests?.findIndex(r=>r.request_id===batch.request.request_id);
   if(index>=0)sourceImport.sent.add(index);
  },true,true);
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
  beginImport([],'教学网');sourceImport=null;
  const controller=new AbortController();activeController=controller;busy=true;error='';notice='正在获取教学网信息，通常约 2 分钟，最多等待 5 分钟…';refresh();
  try{
   const result=await api('/api/sources/sync',{sources:[...syncSources]},'POST',{signal:controller.signal,timeoutMs:310000});
   if(!current())return;
   if(!Array.isArray(result.requests))throw new Error('同步结果格式不正确');
   if(!result.requests.length){sourceImport=null;const skipped=result.summary?.skipped||0;beginImport([],'教学网',0,skipped);importProgress.active=false;notice=`同步完成，暂无待识别内容${skipped?`；${skipped} 条已识别通知没有变化`:''}`;return;}
   sourceImport={kind:'requests',requests:result.requests,sent:new Set()};sourceName='教学网增量同步（每条独立识别）';sourceConsent=true;
   busy=false;activeController=null;
   const total=sourceImport.requests.length,skipped=result.summary?.skipped||0,failureMessages=[];let failed=0,attempted=0;
   beginImport(sourceImport.requests.map(request=>request.request_id),'教学网',total,skipped);
   for(let index=0;index<total;index++){
    if(!current())return;
    const batch=sourceImport.requests[index];
    notice=`识别第 ${index+1}/${total} 条；跳过 ${skipped} 条未变化通知`;refresh();
    const complete=await submitRecognitionRequest(batch,()=>sourceImport.sent.add(index),true,true,false,true);
    if(!current())return;
    attempted++;
    if(!complete){failed++;if(error)failureMessages.push(error);}
    if(complete===undefined)break;
   }
   await planNewTasks(current);
   if(current()){if(failureMessages.length)error=[...new Set(failureMessages)].join('；');importProgress.active=false;syncConsent=false;notice=`同步识别${failed||attempted<total?'尚未全部完成':'完成'}：成功 ${sourceImport.sent.size} 条，失败 ${failed} 条，未处理 ${total-attempted} 条，跳过 ${skipped} 条未变化通知。${failed?'未完成内容可再次同步，识别失败记录可重试。':''}`;}
  }catch(e){if(current()){notice='';error=e.status===404?'后端尚未提供来源同步接口，请使用文件导入':e.message;}}
  finally{if(current()){busy=false;activeController=null;importProgress.active=false;refresh();}}
  return;
 }
 if(button.dataset.agentImportIndex!==undefined){
  const index=Number(button.dataset.agentImportIndex),batch=sourceImport?.kind==='requests'&&sourceImport.requests[index];
  if(!batch||sourceImport.sent.has(index))return;
  if(!sourceConsent){error='请先确认所选来源将发送给模型';refresh();return;}
  beginImport([batch.request_id],sourceName||'来源文件',1);
  await submitRecognitionRequest(batch,()=>{sourceImport.sent.add(index);sourceConsent=false;},true,true);importProgress.active=false;refresh();return;
 }
 if(button.dataset.agentAction==='import-wechat'){
  if(sourceImport?.kind!=='wechat')return;
  if(!sourceConsent){error='请先确认所选微信消息将发送给模型';refresh();return;}
  try{const batch=wechatRequest(sourceImport.messages,selectedMessages);if(new TextEncoder().encode(JSON.stringify(batch)).length>1_900_000)throw new Error('所选消息过大，请减少数量');beginImport([batch.request_id],sourceName||'微信导入',1);await submitRecognitionRequest(batch,()=>{sourceImport=null;selectedMessages=new Set();sourceConsent=false;},false,true);importProgress.active=false;refresh();}
  catch(e){error=e.message;refresh();}return;
 }
 if(button.dataset.agentAction==='refresh-review'){
  const id=button.dataset.candidate;host.closeDialog();await load(true);if(!current())return;
  const item=batches.flatMap(b=>b.items).find(i=>i.id===id);
  if(item?.state==='pending')review(item);else host.toast('候选已处理，请在任务中心查看');
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

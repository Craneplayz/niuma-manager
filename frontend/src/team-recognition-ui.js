import {newId} from './id.js';
import {ImportJourney,scrollToReview,rewriteReview,groupImportBatches,slideToImportPage} from './import-journey.js';
import {animateReviewedCandidate,isReviewFlying} from './review-motion.js';
import {transitionView} from './view-motion.js';
import {waitingPanel} from './interaction-motion.js';
import {api,dataVersion,flushStore,session,accountVersion} from './store.js';
import {icon} from './icons.js';
import {pageIntro} from './story-layout.js';
import {startVoice,cancelVoice,voiceSupport} from './voice.js';
import {parseSourceFile,wechatRequest,wechatEnabled,readManualTextFile} from './source-import.js';
import {configureReprocessing,reprocessingPanel} from './reprocessing-ui.js';
import {wechatMediaPanel} from './team-wechat-media-ui.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={create:'新增',update:'更新',postpone:'延期',cancel:'取消',uncertain:'待核实'};
const platformLabels={teaching_web:'教学网',wechat:'微信',email:'邮件',manual:'手动输入',unknown:'其他'};
let host={},batches=[],loaded=false,busy=false,error='',notice='',text='',published='',quality='complete',request=null,voiceState='idle',stopVoice=null,activeController=null,sourceImport=null,sourceName='',sourceConsent=false,selectedMessages=new Set(),syncSources=new Set(),syncConsent=false,sourceVersion=0,sourceBusy=false,credentialStatus=null,credentialBusy=false,editCredentials=false,importMode='manual',activeRequestIds=new Set(),candidateBusy=new Set(),importProgress={active:false,source:'',total:0,processed:0,created:0,failed:0,incomplete:0,skipped:0};
export function configureAssistant(callbacks){host=callbacks;configureReprocessing({render:callbacks.render,finished:async()=>{loaded=false;await callbacks.refresh?.();}});}
const journey=new ImportJourney();let journeyTransition=false,reviewEntered=false,importId='',importTime='',openHistory=new Set();
let manualFileVersion=0;
export function resetAssistant(){manualFileVersion++;reviewEntered=false;importId='';openHistory.clear();journey.reset();journeyTransition=false;newPlanningTaskIds.clear();cancelVoice();activeController?.abort();activeController=null;batches=[];loaded=false;busy=false;error='';notice='';text='';published='';quality='complete';request=null;voiceState='idle';stopVoice=null;sourceImport=null;sourceName='';sourceConsent=false;selectedMessages=new Set();syncSources=new Set();syncConsent=false;sourceVersion=0;sourceBusy=false;credentialStatus=null;credentialBusy=false;editCredentials=false;importMode='manual';activeRequestIds=new Set();candidateBusy=new Set();importProgress={active:false,source:'',total:0,processed:0,created:0,failed:0,incomplete:0,skipped:0};}
window.addEventListener('niuma:account-reset',resetAssistant);
let candidateRevision=0;
window.addEventListener('niuma:recognition-changed',()=>{candidateRevision++;batches=[];loaded=false;refresh();});
export function leaveAssistant(){cancelVoice();voiceState='idle';}
const refresh=()=>{if(journey.phase==='done'&&journey.snapshot(batches).pending)journey.phase='review';enterReviewOnResult();if(['#agent','#settings','#sources'].includes(location.hash))host.render();if(location.hash==='#agent')queueMicrotask(advanceJourney);};
function enterReviewOnResult(){
 if(reviewEntered||journey.phase!=='importing'||location.hash!=='#agent'||!journey.items(batches).length)return;
 reviewEntered=true;queueMicrotask(scrollToReview);
}
globalThis.document?.addEventListener?.('toggle',event=>{const id=event.target.dataset?.importHistory;if(!id)return;if(event.target.open)openHistory.add(id);else openHistory.delete(id);},true);
async function advanceJourney(){
 if(!host.showImportedTasks||!journey.active||journeyTransition||busy||importProgress.active||candidateBusy.size||location.hash!=='#agent')return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion()&&location.hash==='#agent';
 const snapshot=journey.snapshot(batches,host.getState().tasks);
 if(journey.phase==='importing'){journey.finish(Boolean(importProgress.failed||importProgress.incomplete||error||importProgress.processed<importProgress.total));host.render();journeyTransition=true;await rewriteReview();journeyTransition=false;if(!current())return;}
 if(journey.phase==='review'&&snapshot.pending===0&&(journey.seen||snapshot.added.length||importProgress.total>0)){journeyTransition=true;journey.phase='done';host.render();await rewriteReview();if(current())newPlanningTaskIds.clear();journeyTransition=false;}
}
export const canAutoApplyCandidate=(c,batch)=>{
 const source=batch?.request?.records?.find(r=>r.input_id===c.input_id);
 const external=source&&['teaching_web','wechat'].includes(source.platform);
 const d=c.deadline;
 const exactMinute=d.precision!=='datetime'||Number.isFinite(Date.parse(d.value))&&new Date(d.value).getUTCSeconds()===0&&new Date(d.value).getUTCMilliseconds()===0;
 return Boolean(!batch?.demo&&external&&c.notice_type==='create'&&c.needs_review===false&&c.review_reasons?.length===0&&(
  d.kind==='deadline'&&d.status==='explicit'&&['date','datetime'].includes(d.precision)&&Boolean(d.value)&&exactMinute||
  d.status==='not_provided'&&d.value===null));
};
let newPlanningTaskIds=new Set();
const needsSourceCompletion=batch=>batch?.request.contract_version==='teaching-workspace/0.1'
 && batch.result.errors?.length>0 && batch.result.errors.every(error=>error.code==='incomplete_source');
function sourceCompletionCard(batch){
 const source=batch.request.records[0];
 const references=source?.upstream_hints?.references||[];
 const gaps=[...(source?.text_quality!=='complete'?['正文仅取得标题、摘要或部分内容']:[]),
  ...references.filter(ref=>ref.read_state!=='complete').map(ref=>`${ref.label||'附件或详情'}（${ref.read_state==='unsupported'?'暂不支持读取':ref.read_state==='failed'?'读取失败':ref.read_state==='partial'?'仅部分读取':'尚未读取'}）`)];
 let link='';
 try{const url=new URL(source.source_url);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password)link=`<a href="${esc(url.href)}" target="_blank" rel="noreferrer">查看来源</a>`;}catch{}
 return `<div class="source-incomplete-record"><p><strong>${esc(source?.course_or_project||source?.source_label||'通知')}</strong> · ${esc(source?.text?.split('\n')[0]?.slice(0,120)||'来源待补全')}</p><p>${esc(gaps.join('；')||'来源内容未完整取得')}</p>${link}</div>`;
}
const sourceFor=(item)=>{const batch=batches.find(batch=>batch.items.some(candidate=>candidate.id===item.id));return batch?.request.records.find(record=>record.input_id===item.candidate.input_id);};
function beginImport(requestIds,source,total=requestIds.length,skipped=0){journey.begin(requestIds,host.getState?.().tasks||[],source);reviewEntered=false;importId=newId();importTime=new Date().toISOString();activeRequestIds=new Set(requestIds);candidateBusy.clear();importProgress={active:true,source,total,processed:0,created:0,failed:0,incomplete:0,skipped};error='';notice='';}
function queuePlanning(candidateIds){
 for(const task of host.getState?.().tasks||[])if(candidateIds.some(id=>task.recognitionHistory?.some(history=>history.storageId===id)))newPlanningTaskIds.add(task.id);
}
async function planNewTasks(current){
 if(journey.active&&host.showImportedTasks)return;
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
 return `<section class="agent-source-sync"><h2>选择同步来源</h2><div class="source-sync-options"><label><input type="checkbox" data-agent-sync-source="teaching_web" ${syncSources.has('teaching_web')?'checked':''} ${busy||sourceBusy||!session.user?'disabled':''}> 教学网</label>${wechatEnabled?'<label><input type="checkbox" disabled> 微信（见下方本机采集器）</label>':''}</div>${session.user?`<p>${configured?'教学网账号已保存：'+esc(credentialStatus.username):credentialStatus?.loading?'正在读取教学网凭据…':'请先保存教学网账号和密码。'}</p>${configured?`<div class="button-row"><button type="button" class="btn" data-agent-action="edit-credentials">${editCredentials?'取消修改':'更换凭据'}</button><button type="button" class="btn" data-agent-action="remove-credentials">删除凭据</button></div>`:''}${credentialForm}${credentialStatus?.error?`<p class="error">${esc(credentialStatus.error)}</p>`:''}`:''}</section>`;
}
function syncActionPanel(){return `<section class="agent-source-sync agent-sync-action"><h2>同步已选来源</h2><p>${syncSources.size?'当前来源：'+esc([...syncSources].map(s=>s==='teaching_web'?'教学网':s).join('、')):'尚未选择来源'} · <a href="#settings">管理来源与账号</a></p><label class="source-import-consent"><input id="agent-sync-consent" type="checkbox" ${syncConsent?'checked':''} ${busy||!session.user?'disabled':''}> 我同意将所选来源原文发送给文本识别服务。</label><button type="button" class="btn primary" data-agent-action="${busy?'stop':'sync-sources'}" ${!session.user?'disabled':''}>${busy?'停止导入':'同步并识别'}</button>${importWaiting()}<p id="agent-sync-error" class="error" role="alert" hidden></p></section>`;}
export function sourceManagementPage(embedded=false){
 if(session.user&&!credentialStatus){credentialStatus={loading:true};queueMicrotask(loadCredentials);}
 const content=`${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice&&!busy&&!['review','done'].includes(journey.phase)?`<p role="status">${esc(notice)}</p>`:''}${sourceSyncPanel()}${reprocessingPanel()}${sourceImportPanel()}${wechatMediaPanel()}`;
 return embedded?`<div class="settings-source-management"><hr><h2>来源管理</h2>${content}</div>`:`<div class="page-heading"><h1>来源管理</h1></div>${content}`;
}
function sourceImportPanel(standalone=false){
 const imported=sourceImport?.kind==='requests'?`<p>${esc(sourceName)} · ${sourceImport.requests.length} 批记录</p><div class="source-import-list">${sourceImport.requests.map((batch,index)=>`<div class="source-import-row"><span>第 ${index+1} 批 · ${batch.records.length} 条 · ${esc([...new Set(batch.records.map(r=>platformLabels[r.platform]||'其他'))].join('、'))}</span><button type="button" class="btn" data-agent-import-index="${index}" ${busy||sourceImport.sent.has(index)?'disabled':''}>${sourceImport.sent.has(index)?'已提交':'识别此批'}</button></div>`).join('')}</div>`:
  sourceImport?.kind==='wechat'?`<p>${esc(sourceName)} · ${esc(sourceImport.chatName)} · 仅展示最近 ${sourceImport.messages.length} 条文本消息；只发送勾选内容，不发送账号、群成员 ID 或附件。</p><div class="source-import-list">${sourceImport.messages.map((message,index)=>`<label class="source-import-message"><input type="checkbox" data-agent-wechat-index="${index}" ${selectedMessages.has(index)?'checked':''} ${busy?'disabled':''}><span><small>${esc(message.time||'时间待核实')}</small>${esc(message.content.slice(0,180))}${message.content.length>180?'…':''}</span></label>`).join('')}</div><button type="button" class="btn" data-agent-action="import-wechat" ${busy?'disabled':''}>识别选中的微信消息</button>`:'';
 const textPreview=sourceImport?.kind==='text'?`<label>预览并选择要识别的文字<textarea id="agent-import-text" rows="6" maxlength="4000">${esc(sourceImport.text)}</textarea></label><p>每次最多 4000 字，可编辑后分段识别。JSON 引用的附件不会自动获取，请在附件区主动选择原件。</p><button type="button" class="btn" data-agent-action="import-text" ${busy?'disabled':''}>识别所选文字</button>`:'';
 const content=`<p>文件先在本机预览，不会自动上传；只处理用户自行导出的记录，不连接微信。</p><label>${standalone?'选择自行导出的 JSON 记录':'选择文件'}<input id="agent-source-file" type="file" accept=".json,.jsonl,application/json" ${busy||!session.user?'disabled':''}></label>${imported}${textPreview}${sourceImport?`<label class="source-import-consent"><input id="agent-import-consent" type="checkbox" ${sourceConsent?'checked':''} ${busy?'disabled':''}> 我同意将所选记录发送给文本识别服务。</label>`:''}${busy?'<button type="button" class="btn" data-agent-action="stop">停止导入</button>':''}`;
 return standalone?`<div class="agent-source-import source-import-standalone">${content}${importWaiting()}</div>`:`<details class="agent-source-import" ${sourceImport?'open':''}><summary>导入来源文件（${wechatEnabled?'课程网 / 微信':'课程网'}）</summary>${content}</details>`;
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
 const revision=candidateRevision;
 if(!session.user)return;
 const epoch=accountVersion(),current=()=>epoch===accountVersion()&&revision===candidateRevision;
 try{if(refreshWorkspace)await host.refresh();if(!current())return;const next=await api('/api/recognition/batches');if(!current())return;batches=next;error='';loaded=true;refresh();}
 catch(e){if(!current())return;error=e.message;loaded=true;refresh();}
}
export async function openMediaCandidate(id){
 await load(true);
 const item=batches.flatMap(batch=>batch.items).find(item=>item.id===id);
 if(item?.state==='pending')review(item);
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
 ${!wechatEnabled?`<label class="manual-text-file">导入自行导出的文字或 JSON<input id="agent-manual-file" type="file" accept=".txt,.json,.jsonl,text/plain,application/json" ${busy||!session.user?'disabled':''}></label><p class="panel-note">每次最多 4000 字；先在本机预览和编辑，点击“识别通知”才发送至服务端及模型。请只提交未加密、已获授权的内容。图片或文档附件请在来源管理的“主动上传材料”中选择。</p>`:''}
 <div class="form-grid"><label>文本完整度<select id="agent-quality"><option value="complete" ${quality==='complete'?'selected':''}>完整正文</option><option value="partial" ${quality==='partial'?'selected':''}>部分正文</option><option value="summary" ${quality==='summary'?'selected':''}>摘要或标题</option></select></label><label>原文发布日期（可选）<input id="agent-published" type="date" value="${published}"></label></div>
 <div class="composer-bar"><button class="voice-button" type="button" data-agent-action="voice" ${busy?'disabled':''}>${icon('mic')}${voiceState==='listening'?'说完了，开始整理':'点击说话'}</button><button class="btn primary" ${busy||!session.user?'disabled':''}>${busy?'识别中…':'识别通知'}</button></div>
 <div class="button-row">${text.trim()?`<button class="btn" type="button" data-agent-action="new-batch" ${busy?'disabled':''}>重新识别</button>`:''}${busy?'<button class="btn" type="button" data-agent-action="stop">停止等待</button>':''}</div>
 ${importWaiting()}${support.browser?'<p class="voice-notice">语音输入可能使用浏览器语音服务。</p>':''}</form>`;}
function importModePanel(support){
 if(importMode==='wechat'||importMode==='export')return sourceImportPanel(true);
 if(importMode==='manual')return manualImportPanel(support);
 return syncActionPanel();
}
const importModes=()=>[['manual','粘贴通知'],...(syncSources.has('teaching_web')?[['teaching_web','教学网']]:[]),...(wechatEnabled?[['wechat','微信']]:[['export','导出记录']])];
export function assistantPage(){if(journey.active&&host.showImportedTasks)queueMicrotask(advanceJourney);
 if(!loaded&&session.user){loaded=true;queueMicrotask(load);}
 if(session.user&&location.hash==='#agent'&&!credentialStatus){credentialStatus={loading:true};queueMicrotask(loadCredentials);}
 const support=voiceSupport();
 const incompleteBatches=batches.filter(needsSourceCompletion);
 const failedBatches=batches.filter(batch=>activeRequestIds.has(batch.request.request_id)&&!batch.demo&&!needsSourceCompletion(batch)&&['failed','partial_success'].includes(batch.result.status));
 const modes=importModes();if(!modes.some(([value])=>value===importMode))importMode='manual';
 return `<div class="capture-pages"><section class="capture-entry-page" id="import-entry"><div class="story-capture"><div class="story-capture-copy">${pageIntro('agent')}<div class="story-capture-note"><a href="#settings" class="story-link">管理通知来源 ${icon('arrow')}</a></div></div><section class="panel agent-chat import-entry-card"><div class="agent-chat-header import-entry-header"><div class="import-source-tabs" role="tablist" aria-label="导入方式">${modes.map(([value,label])=>`<button type="button" role="tab" aria-selected="${importMode===value}" class="import-source-tab ${importMode===value?'active':''}" data-agent-source-mode="${value}" ${busy&&importMode!==value?'disabled':''}>${label}</button>`).join('')}</div><button class="btn text-btn" data-agent-action="refresh" ${busy?'disabled':''}>刷新结果</button></div>
 <div class="import-mode-panel">${importModePanel(support)}</div>
 ${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice&&!busy&&!['review','done'].includes(journey.phase)?`<p role="status">${esc(notice)}</p>`:''}
 ${failedBatches.length?`<div class="agent-failures">${failedBatches.map(batch=>`<p class="draft-warning">${batch.result.record_results.some(r=>r.warnings.includes('incomplete_text'))?'来源正文不完整；':''}${batch.result.errors.map(e=>{const source=batch.request.records.find(r=>r.input_id===e.input_id);return esc(source?.course_or_project||source?.source_label||'通知')+'：'+esc(e.code==='agent_timeout'?'识别超时；请稍后重试':e.code==='agent_unavailable'?'模型服务不可用；请检查配置、网络或额度后重试':e.message)}).join('；')}</p><button class="btn" data-agent-retry="${esc(batch.request.request_id)}" ${busy?'disabled':''}>重试失败记录</button>`).join('')}</div>`:''} ${incompleteBatches.length?`<details class="agent-source-incomplete"><summary>来源待补全 · ${incompleteBatches.length} 条</summary><p>已读部分未发现任务，仍有内容未完整读取，尚不能判定为无任务。重试识别不会补抓网页或读取缺失附件；可查看来源，将完整要求粘贴识别，或待采集支持后重新同步。</p>${incompleteBatches.map(sourceCompletionCard).join('')}</details>`:''}</section></div><button class="capture-scroll-cue" data-agent-scroll="review">向下查看待核对 ${icon('arrow')}</button></section>
 <section class="notification-import capture-review-page" id="import-review">
 <header class="capture-review-heading"><h1>待核对</h1><div class="review-dock"><button class="btn text-btn" data-agent-scroll="entry">↑ 返回通知导入</button><button class="btn review-task-center" data-agent-action="open-import-tasks" data-review-task-center>${envelopeIcon()}<span>任务中心</span></button></div></header>
 ${importProgress.active?`<div class="review-live-progress" role="status"><span class="motion-waiting-orb" aria-hidden="true"></span><span>正在导入并识别通知 · ${importProgress.processed} / ${importProgress.total||'…'}<small>新的识别结果会继续出现在下方。</small></span></div>`:''}
 ${activeRequestIds.size&&(!["review","done"].includes(journey.phase)||importProgress.failed||importProgress.skipped)?`<p class="import-counts">已创建 ${importProgress.created} 项${importProgress.failed?` · 失败 ${importProgress.failed} 项`:""}${importProgress.incomplete?` · 来源待补全 ${importProgress.incomplete} 条`:""}${importProgress.skipped?` · 跳过 ${importProgress.skipped} 项`:""}</p>`:""}${error?`<p class="error" role="alert">${esc(error)}</p>`:''}
 <section class="current-import-section"><div class="result-heading"><h2>本次导入 <span>${batches.filter(b=>activeRequestIds.has(b.request.request_id)).flatMap(b=>b.items).filter(i=>i.state==='pending').length}</span></h2>${journeySummary()}</div>
 ${journey.phase==='done'?completionNote():batches.filter(b=>activeRequestIds.has(b.request.request_id)).flatMap(b=>b.items).filter(i=>i.state==='pending').map(candidateCard).join('')||`<div class="result-empty compact"><p>${importProgress.active?'识别结果返回后会出现在这里。':activeRequestIds.size?'本次暂无待核对内容。':'开始一次导入，新的待核对内容会出现在这里。'}</p></div>`}
 ${activeRequestIds.size?`<details class="import-ignored-section"><summary>本次不加入任务 · ${journey.snapshot(batches).ignored}</summary>${journey.items(batches).filter(i=>i.state==='ignored').map(candidateCard).join('')}</details>`:''}</section>
 <section class="history-import-section"><div class="result-heading"><h2>历史导入 <span>${groupImportBatches(batches.filter(b=>!activeRequestIds.has(b.request.request_id))).length}</span></h2><p>按导入批次保留，随时展开继续核对。</p></div>${historyImports()}</section>
 </section></div>`;
}
function importWaiting(){return busy?waitingPanel(importProgress.total?'正在导入并识别通知':'正在读取教学网通知',importProgress.total?`已处理 ${importProgress.processed} / ${importProgress.total} 条。${reviewEntered?'新的结果会继续追加到待核对页。':'第一条结果返回后会进入待核对页。'}`:'正在读取课程与通知，识别结果出现后会进入待核对页。'):'';}
function historyImports(){
 const groups=groupImportBatches(batches.filter(batch=>!activeRequestIds.has(batch.request.request_id)));
 return groups.map(group=>{
  const items=group.batches.flatMap(batch=>batch.items),pending=items.filter(item=>item.state==='pending'),ignored=items.filter(item=>item.state==='ignored');
  const time=group.createdAt?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(group.createdAt)):'导入时间未记录';
  return `<details class="import-history-batch" data-import-history="${esc(group.id)}" ${openHistory.has(group.id)?'open':''}><summary><span><strong>${esc(group.source)}</strong><small>${esc(time)} · ${group.batches.reduce((n,b)=>n+b.request.records.length,0)} 条通知</small></span><span class="history-pending">${pending.length?'待核对 '+pending.length:'已处理'}</span></summary><div class="history-batch-content"><div class="history-batch-actions"><p>待核对 ${pending.length} · 不加入 ${ignored.length} · 已处理 ${items.length-pending.length-ignored.length}</p><button class="btn text-btn" data-agent-delete-import="${esc(group.id)}" ${busy?'disabled':''}>删除此批记录</button></div>${pending.map(candidateCard).join('')}${!pending.length?'<p class="panel-note">这批通知没有待核对内容。</p>':''}${ignored.length?`<details class="import-ignored-section"><summary>不加入任务 · ${ignored.length}</summary>${ignored.map(candidateCard).join('')}</details>`:''}${group.batches.filter(b=>!needsSourceCompletion(b)&&['failed','partial_success'].includes(b.result.status)).map(b=>`<p class="error">部分通知识别失败</p><button class="btn" data-agent-retry="${esc(b.request.request_id)}" ${busy?'disabled':''}>重试失败记录</button>`).join('')}</div></details>`;
 }).join('')||'<p class="panel-note">还没有历史导入记录。</p>';
}
function envelopeIcon(){return '<svg viewBox="0 0 32 24" fill="none" aria-hidden="true"><rect x="2" y="3" width="28" height="19" rx="3"/><path d="m3 5 13 10L29 5M3 21l9-9m17 9-9-9"/></svg>';}
function completionNote(){const added=journey.snapshot(batches,host.getState?.().tasks||[]).added.length;return `<div class="journey-complete" role="status"><svg class="journey-complete-mark" viewBox="0 0 104 88" fill="none" aria-hidden="true"><g class="complete-envelope"><path class="complete-envelope-back" d="M17 41q0-3 3-5l28-19q4-3 8 0l28 19q3 2 3 5v31q0 6-6 6H23q-6 0-6-6Z"/></g><g class="complete-paper"><path class="complete-letter" d="M35 9h28q2 0 4 2l4 4q2 2 2 4v41H31V13q0-4 4-4Z"/><path d="M65 9v8h8M39 25h25m-25 8h21m-21 8h16"/></g><g class="complete-envelope"><path class="complete-envelope-front" d="M17 39 48 61q4 3 8 0l31-22v33q0 6-6 6H23q-6 0-6-6Z"/><path d="m19 76 24-21m42 21L61 55"/></g><path class="complete-check" d="m43 68 6 6 12-13"/></svg><h3>这批通知，已经整理好了</h3><p data-completion-copy>本次导入与核对已全部完成${added?`，新增 ${added} 项任务已放入任务中心。`:`，可进入任务中心查看。`}</p><button class="btn journey-complete-action" data-agent-action="open-import-tasks">${added?`查看本次新增任务`:`进入任务中心`}<span aria-hidden="true">→</span></button></div>`;}
function journeySummary(){
 const title={review:'通知已全部导入',done:'本次核对已完成',failed:importProgress.incomplete?'来源待补全，请查看来源':'部分导入未完成，可重试'}[journey.phase];
 return title?`<span class="journey-summary" role="status">${title}</span>`:'';
}
export function candidateCard(item){
 const c=item.candidate,source=sourceFor(item),working=candidateBusy.has(item.id);
 const sourceLabel=c.sources?.[0]?.label||source?.course_or_project||source?.source_label||platformLabels[source?.platform]||'通知来源';
 return `<article class="import-review-card ${item.state==='ignored'?'ignored':''}" data-candidate-card="${esc(item.id)}"${isReviewFlying(item.id)?' data-review-flying="true"':''}>
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
 const epoch=accountVersion(),revision=candidateRevision,current=()=>epoch===accountVersion()&&revision===candidateRevision;
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
  if(body.action==='create'){host.closeDialog();await animateReviewedCandidate(item.id,()=>current()&&location.hash==='#agent');if(!current())return;}
  batches=response.batches;host.closeDialog();notice=body.action==='create'?'已加入「看清事情」的任务中心':body.action==='cancel'?'任务已取消':'任务已更新';
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
 busy=true;error='';notice='正在识别，高峰时会自动排队…';const controller=new AbortController();activeController=controller;const timeout=setTimeout(()=>controller.abort(),280000);refresh();
 try{
  if(importId&&activeRequestIds.has(body.request_id))await api('/api/recognition/imports',{import_id:importId,created_at:importTime,source:journey.source,request_ids:[body.request_id]},'POST',{signal:controller.signal});
  const existing=external?await api('/api/recognition/batches',undefined,'GET',{signal:controller.signal,timeoutMs:100000}):batches;
  if(!current())return;
  const previousIds=new Set(existing.find(batch=>batch.request.request_id===body.request_id)?.items.map(item=>item.id)||[]);
  const payload=body.contract_version==='teaching-workspace/0.1'?{contract_version:'teaching-run/0.1',request_id:body.request_id}:body;
  const next=await api(retry?'/api/recognition/retry':'/api/recognition/batches',payload,'POST',{signal:controller.signal,timeoutMs:280000});
  if(!current())return;
  batches=next;loaded=true;refresh();
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
  notice=`${applied?`已自动加入 ${applied} 项任务；`:''}${remaining?`${remaining} 项待处理；`:''}${filtered?`${filtered} 条资讯或历史通知未生成任务；`:''}${failed?`${failed} 项自动加入失败，请再次同步；`:''}${recognized?'识别完成':needsSourceCompletion(batch)?'来源待补全，请查看下方缺失内容；无需重复重试。':'部分记录识别失败，可重试'}`;
  if(complete)onSuccess?.();
  if(activeRequestIds.has(body.request_id)){importProgress.processed++;if(needsSourceCompletion(batch))importProgress.incomplete++;else if(!complete)importProgress.failed++;}
  return Boolean(complete);
 }
 catch(e){if(current()){if(activeRequestIds.has(body.request_id)){importProgress.processed++;importProgress.failed++;}error=controller.signal.aborted?'等待已停止；远端可能仍在处理。请刷新候选，必要时再重试。':e.message;}}
 finally{clearTimeout(timeout);if(current()){activeController=null;busy=false;refresh();}}
}
export async function submitRecognitionText(value){
 manualFileVersion++;
 text=value.trim();if(!text||busy||!session.user)return;
 const signature=JSON.stringify([text,published,quality]);
 if(request?.signature!==signature)request={signature,id:newId(),collectedAt:new Date().toISOString()};
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
  if(!current())return;
  await animateReviewedCandidate(item.id,()=>current()&&location.hash==='#agent');if(!current())return;
  batches=response.batches;importProgress.created++;
  await host.refresh?.();if(current()){host.toast?.('已加入「看清事情」的任务中心');await planReviewedCandidate(item,'create',current);}
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
 const revision=candidateRevision,candidateCurrent=()=>current()&&revision===candidateRevision;
 if(button.dataset.agentAction==='open-import-tasks'){const ids=journey.snapshot(batches,host.getState?.().tasks||[]).added;if(host.showImportedTasks)host.showImportedTasks(ids);else location.hash='tasks';return;}
 if(button.dataset.agentScroll){slideToImportPage(button.dataset.agentScroll==='review'?'#import-review':'.story-header');return;}
 if(button.dataset.agentDeleteImport){const group=groupImportBatches(batches).find(g=>g.id===button.dataset.agentDeleteImport);if(!group||busy)return;host.openDialog(`${host.dialogHeader('删除这批导入记录？')}<p>这批通知及待核对内容将从导入历史中移除，已经加入任务中心的任务会保留。</p><div class="dialog-actions"><button class="btn" data-action="close">取消</button><button class="btn danger" data-agent-confirm-delete-import="${esc(group.id)}">删除此批记录</button></div>`);return;}
 if(button.dataset.agentConfirmDeleteImport){const group=groupImportBatches(batches).find(g=>g.id===button.dataset.agentConfirmDeleteImport);if(!group||busy)return;try{const next=await api('/api/recognition/imports/archive',{request_ids:group.batches.map(b=>b.request.request_id)});if(!current())return;batches=next;openHistory.delete(group.id);host.closeDialog();host.toast?.('这批导入记录已移除');refresh();}catch(e){if(current()){host.closeDialog();error=e.message;refresh();}}return;}
 if(button.dataset.agentSourceMode){if(!busy&&importModes().some(([value])=>value===button.dataset.agentSourceMode)){transitionView(()=>{importMode=button.dataset.agentSourceMode;error='';notice='';refresh();},{scope:'import'});}return;}
 const candidateAction=button.dataset.agentConfirm||button.dataset.agentReview||button.dataset.agentIgnore||button.dataset.agentRestore;
 if(busy&&!candidateAction&&!['stop','voice'].includes(button.dataset.agentAction))return;
 if(button.dataset.agentEditTask){host.editTask?.(button.dataset.agentEditTask);return;}
 if(button.dataset.agentConfirm){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentConfirm);if(item?.candidate.notice_type==='create')await quickCreate(item,candidateCurrent);else if(item)review(item);return;}
 if(button.dataset.agentReview){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentReview);if(item)review(item);return;}
 if(button.dataset.agentIgnore){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentIgnore);await ignoreCandidate(item,candidateCurrent);return;}
 if(button.dataset.agentRestore){const item=batches.flatMap(b=>b.items).find(i=>i.id===button.dataset.agentRestore);await restoreCandidate(item,candidateCurrent);return;}
 if(button.dataset.agentAction==='refresh'){await load(true);return;}
 if(button.dataset.agentRetry){
  const batch=batches.find(b=>b.request.request_id===button.dataset.agentRetry);
  if(needsSourceCompletion(batch)){notice='来源待补全：重试不会补抓网页或读取缺失附件，请查看来源或补全后重新同步。';refresh();return;}
  if(batch?.request.contract_version==='0.3.1'){location.hash='#settings';return;}
  if(batch&&!batch.demo){beginImport([batch.request.request_id],'重试识别',1);await submitRecognitionRequest(batch.request,()=>{
   const index=sourceImport?.requests?.findIndex(r=>r.request_id===batch.request.request_id);
   if(index>=0)sourceImport.sent.add(index);
  },true,true);importProgress.active=false;refresh();}
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
   if(!result.requests.length){sourceImport=null;const skipped=result.summary?.skipped||0;beginImport([],'教学网',0,skipped);importProgress.active=false;notice=`同步完成，暂无待识别内容${skipped?`；跳过 ${skipped} 条无需重复识别的来源`:''}。可在“账号全部结果”查看来源待补全记录。`;return;}
   sourceImport={kind:'requests',requests:result.requests,sent:new Set()};sourceName='教学网增量同步（每条独立识别）';sourceConsent=true;
   busy=false;activeController=null;
   const total=sourceImport.requests.length,skipped=result.summary?.skipped||0,failureMessages=[];let failed=0,incomplete=0,attempted=0;
   beginImport(sourceImport.requests.map(request=>request.request_id),'教学网',total,skipped);
   for(let index=0;index<total;index++){
    if(!current())return;
    const batch=sourceImport.requests[index];
    notice=`识别第 ${index+1}/${total} 条；跳过 ${skipped} 条无需重复识别的来源`;refresh();
    const complete=await submitRecognitionRequest(batch,()=>sourceImport.sent.add(index),true,true,false,true);
    if(!current())return;
    attempted++;
    if(complete===false&&needsSourceCompletion(batches.find(item=>item.request.request_id===batch.request_id)))incomplete++;
    else if(!complete){failed++;if(error)failureMessages.push(error);}
    if(complete===undefined)break;
   }
   await planNewTasks(current);
   if(current()){if(failureMessages.length)error=[...new Set(failureMessages)].join('；');importProgress.active=false;syncConsent=false;notice=`同步识别${failed||incomplete||attempted<total?'尚未全部完成':'完成'}：成功 ${sourceImport.sent.size} 条，失败 ${failed} 条，来源待补全 ${incomplete} 条，未处理 ${total-attempted} 条，跳过 ${skipped} 条无需重复识别的来源。${failed?'未完成内容可再次同步，识别失败记录可重试。':''}${incomplete?'请查看“来源待补全”；重复重试不会补抓内容。':''}`;}
  }catch(e){if(current()){notice='';error=e.status===404?'后端尚未提供来源同步接口，请使用文件导入':e.message;}}
  finally{if(current()){busy=false;activeController=null;importProgress.active=false;refresh();}}
  return;
 }
 if(button.dataset.agentImportIndex!==undefined){
  const index=Number(button.dataset.agentImportIndex),batch=sourceImport?.kind==='requests'&&sourceImport.requests[index];
  if(!batch||sourceImport.sent.has(index))return;
  if(!sourceConsent){error='请先确认所选来源将发送给模型';refresh();return;}
  beginImport([batch.request_id],sourceName||'来源文件',1);
  const submitted=wechatEnabled?batch:{...batch,request_id:'manual-import:'+newId(),records:batch.records.map(record=>({...record,platform:'manual'}))};
  await submitRecognitionRequest(submitted,()=>{sourceImport.sent.add(index);sourceConsent=false;},true,wechatEnabled);importProgress.active=false;refresh();return;
 }
 if(button.dataset.agentAction==='import-wechat'){
  if(sourceImport?.kind!=='wechat')return;
  if(!sourceConsent){error='请先确认所选微信消息将发送给模型';refresh();return;}
  try{const batch=wechatRequest(sourceImport.messages,selectedMessages);if(new TextEncoder().encode(JSON.stringify(batch)).length>1_900_000)throw new Error('所选消息过大，请减少数量');beginImport([batch.request_id],sourceName||'微信导入',1);await submitRecognitionRequest(batch,()=>{sourceImport=null;selectedMessages=new Set();sourceConsent=false;},false,true);importProgress.active=false;refresh();}
  catch(e){error=e.message;refresh();}return;
 }
 if(button.dataset.agentAction==='import-text'){
  if(sourceImport?.kind!=='text')return;
  if(!sourceConsent){error='请先确认所选文字将发送给模型';refresh();return;}
  if(sourceImport.text.length>4000){error='请将所选文字缩小至 4000 字以内，或分段识别';refresh();return;}
  await submitRecognitionText(sourceImport.text);return;
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
 if(event.target.id==='agent-manual-file'&&event.type==='change'){
  if(busy||!session.user)return;
  const file=event.target.files?.[0],epoch=accountVersion(),version=++manualFileVersion;if(!file)return;
  readManualTextFile(file).then(value=>{if(epoch!==accountVersion()||version!==manualFileVersion||busy)return;text=value;request=null;error='';notice='文字已在本机预览，尚未发送；请核对后点击识别通知。';refresh();}).catch(e=>{if(epoch!==accountVersion()||version!==manualFileVersion)return;error=e.message;refresh();});return;
 }
 if(event.target.id==='agent-source-file'&&event.type==='change'){
  const file=event.target.files?.[0],epoch=accountVersion();if(!file)return;
  sourceImport=null;sourceName='';selectedMessages=new Set();sourceConsent=false;
  if(file.size>10_000_000){error='文件超过 10 MB，请缩小导出范围';refresh();return;}
  file.text().then(raw=>{if(epoch!==accountVersion())return;sourceImport=parseSourceFile(raw);sourceName=file.name;selectedMessages=new Set();sourceConsent=false;error='';notice='文件仅在本机预览，尚未发送';refresh();}).catch(e=>{if(epoch!==accountVersion())return;error=e.message;refresh();});return;
 }
 if(event.target.id==='agent-import-consent')sourceConsent=event.target.checked;
 if(event.target.id==='agent-import-text'&&sourceImport?.kind==='text'){sourceImport.text=event.target.value;sourceConsent=false;}
 if(event.target.id==='agent-sync-consent')syncConsent=event.target.checked;
 if(event.target.dataset?.agentSyncSource){
  if(sourceBusy)return;
  const epoch=accountVersion(),previous=new Set(syncSources),source=event.target.dataset.agentSyncSource;
  if(event.target.checked)syncSources.add(source);else syncSources.delete(source);
  sourceBusy=true;refresh();
  api('/api/data/sources',{value:[...syncSources],version:sourceVersion},'PUT').then(result=>{if(epoch===accountVersion())sourceVersion=result.version;}).catch(e=>{if(epoch===accountVersion()){syncSources=previous;error='来源选择保存失败：'+e.message;}}).finally(()=>{if(epoch===accountVersion()){sourceBusy=false;refresh();}});
 }
 if(event.target.dataset?.agentWechatIndex!==undefined){const index=Number(event.target.dataset.agentWechatIndex);if(event.target.checked)selectedMessages.add(index);else selectedMessages.delete(index);}
 if(event.target.id==='agent-input'){manualFileVersion++;text=event.target.value;}
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

import {api,session,accountVersion} from './store.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const labels={queued:'排队中',running:'识别中',succeeded:'已完成',partial_failed:'部分失败',failed:'处理失败'};
let host={},jobs=[],credential=null,token='',loading=false,busy=false,error='',notice='',timer=null,removeConfirmation=false;
let sourceRevision=0;
export function configureWechatMedia(callbacks){host=callbacks;}
function active(){return ['#sources','#settings'].includes(location.hash);}
function refresh(){if(active())host.render?.();}
function reset(){sourceRevision++;clearTimeout(timer);timer=null;jobs=[];credential=null;token='';loading=false;busy=false;error='';notice='';removeConfirmation=false;}
window.addEventListener('niuma:account-reset',reset);
function schedule(){
 clearTimeout(timer);
 if(active()&&jobs.some(job=>['queued','running'].includes(job.status)))timer=setTimeout(load,3000);
}
export async function load(){
 if(!session.user||loading||!active())return;
 const epoch=accountVersion(),revision=sourceRevision;loading=true;
 try{
  const previous=new Map(jobs.map(job=>[job.job_id,job.status]));
  const [state,list]=await Promise.all([api('/api/sources/wechat/credential'),api('/api/sources/wechat/imports')]);
  if(epoch!==accountVersion()||revision!==sourceRevision)return;
  credential=state;jobs=list.jobs;error='';
  if(jobs.some(job=>['succeeded','partial_failed'].includes(job.status)&&job.result?.tasks?.length&&['queued','running'].includes(previous.get(job.job_id)))){
   const before=new Set(host.getState?.()?.tasks?.map(task=>task.id)||[]);
   await host.refresh?.();
   if(epoch===accountVersion()){
    const added=(host.getState?.()?.tasks||[]).filter(task=>!before.has(task.id)&&task.due&&!task.plans?.length).map(task=>task.id);
    if(added.length)await host.proposeForTasks?.(added);
   }
  }
 }catch(e){if(epoch===accountVersion()&&revision===sourceRevision){credential={configured:false};error=e.message;}}
 finally{if(epoch===accountVersion()){loading=false;refresh();schedule();}}
}
function evidence(item,job){
 const source=job.sources?.[item.input_id]||{};
 const name=item.asset_id?source.assets?.[item.asset_id]:null;
 const loc=item.locator;
 const position=loc?.page?'第 '+loc.page+' 页':loc?.paragraph?'第 '+loc.paragraph+' 段':'';
 return `<li><span>${esc(source.label||'微信消息')}${name?' · '+esc(name):''}${position?' · '+esc(position):''}${item.field==='attachment_file'?' · 模型读取':''}</span><blockquote>${esc(item.quote)}</blockquote></li>`;
}
function deadlineText(deadline){
 if(deadline.status==='not_provided')return '待补日期';
 if(!deadline.value)return '日期待核对';
 if(deadline.precision==='date')return deadline.value.replaceAll('-','/');
 const value=new Date(deadline.value);
 if(!Number.isFinite(value.getTime()))return '日期待核对';
 return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(value);
}
function result(job){
 const data=job.result;
 if(!data)return '';
 const tasks=data.tasks||[];
 const errors=[...new Map([...(data.errors||[]),...(job.last_attempt_errors||[])].map(error=>[JSON.stringify(error),error])).values()];
 const retry=errors.some(item=>item.retryable&&['read','recognize'].includes(item.stage)&&['agent_timeout','agent_unavailable','file_read_failed'].includes(item.code));
 return `<div class="wechat-media-result">${tasks.map(task=>{const item=job.items?.find(item=>item.candidate_id===task.candidate_id);return `<article class="draft-card"><h3>${esc(task.title)}</h3><p>${esc(task.description)}</p><p>${esc(deadlineText(task.deadline))} · ${item?.state==='applied'?'已加入任务':item?.state==='ignored'?'已忽略':task.needs_review?'待核对':'已识别'}</p><details><summary>来源依据</summary><ul>${task.evidence.map(item=>evidence(item,job)).join('')}</ul></details>${item?.state==='pending'?`<div class="button-row"><button class="btn primary" data-media-action="review" data-media-candidate="${esc(item.id)}" ${busy?'disabled':''}>核对并确认</button><button class="btn" data-media-action="ignore" data-media-candidate="${esc(item.id)}" ${busy?'disabled':''}>忽略</button></div>`:''}</article>`;}).join('')}
 ${data.status==='no_tasks'?'<p>已完整识别，未发现任务。</p>':''}
 ${errors.map(item=>`<p class="error" role="alert">${esc(item.message)}${item.asset_id?' · 附件 '+esc(item.asset_id):''}${item.stage==='read'||item.stage==='collect'||item.stage==='upload'?'；请在本机重新获取或处理附件。':''}</p>`).join('')}
 ${retry?`<button class="btn" data-media-action="retry" data-media-job="${esc(job.job_id)}" ${busy?'disabled':''}>重试识别失败部分</button>`:''}</div>`;
}
export function wechatMediaPanel(){
 if(session.user&&credential===null&&!loading)queueMicrotask(load);
 return `<section class="panel wechat-media-panel"><h2>微信附件同步</h2><p>本机采集器可将图片或文件送到此账号的后端和模型；上传原件前请确认其内容可以发送。读取不支持或失败时会明确提示，不会当作无任务。</p>
 ${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}
 ${credential?.limits?`<details><summary>附件限制与保留</summary><p>单文件 ${esc(credential.limits.asset_bytes/1048576)} MiB，账号总量 ${esc(credential.limits.account_bytes/1048576)} MiB；未引用原件保留 ${esc(credential.limits.unreferenced_ttl_seconds%3600===0?credential.limits.unreferenced_ttl_seconds/3600+' 小时':credential.limits.unreferenced_ttl_seconds+' 秒')}。已关联导入记录的原件保留至删除来源或撤销文件。</p></details>`:''}
 <div class="button-row"><button class="btn" data-media-action="refresh" ${loading?'disabled':''}>${loading?'读取中…':'刷新处理状态'}</button>${credential?.configured?`<button class="btn" data-media-action="revoke" ${busy?'disabled':''}>撤销本机导入凭证</button>`:`<button class="btn" data-media-action="issue" ${busy||!session.user?'disabled':''}>生成本机导入凭证</button>`}</div>
 ${session.user&&(credential?.configured||jobs.length)?`<button class="btn" data-media-action="prepare-remove" ${busy?'disabled':''}>删除微信来源数据</button>`:''}
 ${removeConfirmation?`<div class="wechat-media-removal"><p>将删除微信原件、导入记录和候选；已加入日历的任务保留。删除的原件无法从本服务恢复。</p><div class="button-row"><button class="btn" data-media-action="remove" ${busy?'disabled':''}>确认删除</button><button class="btn" data-media-action="cancel-remove" ${busy?'disabled':''}>取消</button></div></div>`:''}
 ${token?`<div class="wechat-media-token"><p>凭证只显示这一次，请复制到本机采集器；不要发送给他人或写入仓库。</p><code>${esc(token)}</code><button class="btn" data-media-action="hide">关闭凭证显示</button></div>`:''}
 <div class="wechat-media-jobs">${jobs.map(job=>`<article class="panel"><h3>微信导入 · ${esc(labels[job.status]||job.status)}</h3><p>${job.progress.finished_records}/${job.progress.total_records} 条已处理${job.progress.failed_records?' · '+job.progress.failed_records+' 条异常':''}</p>${result(job)}</article>`).join('')||'<p>暂无微信附件导入记录。</p>'}</div></section>`;
}
export async function wechatMediaClick(button){
 const action=button.dataset.mediaAction;if(!action)return false;
 const epoch=accountVersion();
 if(action==='hide'){token='';refresh();return true;}
 if(action==='refresh'){await load();return true;}
 if(busy)return true;
 if(action==='prepare-remove'||action==='cancel-remove'){removeConfirmation=action==='prepare-remove';refresh();return true;}
 busy=true;error='';notice='';refresh();
 try{
  if(action==='remove'){
   if(!removeConfirmation)return true;
   const response=await api('/api/sources/wechat/remove',{confirmed:true});
   if(epoch===accountVersion()){
    sourceRevision++;jobs=[];credential={configured:false};token='';removeConfirmation=false;
    notice=response.cleanup_pending?'来源已删除，原件被占用时将在后续清理。':'来源及原件已删除，日历中的任务保留。';
    window.dispatchEvent(new Event('niuma:recognition-changed'));
   }
  }else if(action==='review'){
   await host.reviewCandidate?.(button.dataset.mediaCandidate);
  }else if(action==='ignore'){
   const item=jobs.flatMap(job=>job.items||[]).find(item=>item.id===button.dataset.mediaCandidate);
   if(item){await api('/api/recognition/candidates/'+encodeURIComponent(item.id)+'/resolve',{action:'ignore',version:item.version});if(epoch===accountVersion())await load();}
  }else if(action==='issue'){
   const response=await api('/api/sources/wechat/credential',{});
   if(epoch===accountVersion()){credential={configured:true};token=response.token;notice='凭证已生成，旧凭证已失效。';}
  }else if(action==='revoke'){
   await api('/api/sources/wechat/credential/revoke',{});
   if(epoch===accountVersion()){credential={configured:false};token='';notice='凭证已撤销。';}
  }else if(action==='retry'){
   await api('/api/sources/wechat/imports/'+encodeURIComponent(button.dataset.mediaJob)+'/retry',{retry_id:crypto.randomUUID()});
   if(epoch===accountVersion()){notice='已提交失败部分重试。';await load();}
  }
 }catch(e){if(epoch===accountVersion())error=e.message;}
 finally{if(epoch===accountVersion()){busy=false;refresh();schedule();}}
 return true;
}

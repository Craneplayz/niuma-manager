import {api,session,accountVersion} from './store.js';
import {newId} from './id.js';

const root='/api/sources/sync-runs',version='source-sync/0.1';
const activeStates=new Set(['queued','crawling','recognizing']);
const labels={queued:'等待同步',crawling:'正在获取教学网内容',recognizing:'正在并发识别',succeeded:'同步完成',partial_success:'部分完成',failed:'同步未完成',stopped:'已停止',interrupted:'服务重启后已中断'};
const itemLabels={queued:'等待识别',running:'正在识别',succeeded:'已识别',failed:'识别失败',incomplete:'来源待补全',cancelled:'尚未处理',interrupted:'处理中断'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const visible=()=>['#agent','#sources','#settings'].includes(location.hash);
const retryable=record=>['cancelled','interrupted'].includes(record.state)||record.state==='failed'&&record.error?.retryable;
let host={},runs=[],loaded=false,loading=false,working=false,error='',timer=null,generation=0,operation=null,observed='',showHistory=false;
const expanded=new Set();

export function configureSourceSync(callbacks){host=callbacks;}
export function resetSourceSync(){clearTimeout(timer);timer=null;generation++;runs=[];loaded=false;loading=false;working=false;error='';operation=null;observed='';showHistory=false;expanded.clear();}
window.addEventListener('niuma:account-reset',resetSourceSync);
window.addEventListener('hashchange',()=>{clearTimeout(timer);if(visible()&&session.user)void refreshSourceSync();});
function render(){if(visible())host.render?.();}
function poll(){clearTimeout(timer);if(visible()&&session.user){timer=setTimeout(()=>refreshSourceSync(),runs.some(r=>activeStates.has(r.state))?2000:30000);timer.unref?.();}}
export function syncIsActive(){return working||runs.some(r=>activeStates.has(r.state));}
export function syncOwnsRequest(id){return runs.some(run=>run.records.some(record=>record.request_id===id));}

function latestAttempts(){
 const latest=new Map();
 // API order is newest first; timestamps may tie and are not used as identity.
 for(const run of runs)for(const record of run.records)if(!latest.has(record.request_id))latest.set(record.request_id,{run,record});
 return latest;
}
function guidance(record){
 const code=record.error?.code;
 if(record.state==='incomplete'||code==='source_incomplete')return '原文或附件未取得；重试模型不能补齐。请重新获取来源；仍缺失时查看原文或粘贴完整要求。';
 if(code==='source_changed')return '来源、凭据或提交状态已变化，请核对来源设置并重新同步。';
 if(code==='model_configuration'||code==='agent_unavailable'&&!record.error?.retryable)return '请联系管理员检查当前环境的模型配置、密钥或额度，修复后重新同步。';
 if(code==='agent_timeout')return '模型响应超时；可稍后重试本条，不需要重新抓取教学网。';
 if(code==='invalid_agent_output')return '模型输出未通过校验；可重试本条。持续失败请联系管理员并提供运行编号。';
 if(['interrupted','cancelled'].includes(record.state))return '已保存内容仍在；可继续本条，成功结果不会重跑。';
 if(retryable(record))return '识别服务暂时失败；可稍后重试本条。若多次失败，请联系管理员检查服务。';
 return '此错误不能靠重复识别恢复；请联系管理员并提供运行编号。';
}

async function accept(next,current){
 if(!current())return;
 runs=next;loaded=true;
 const fingerprint=JSON.stringify(runs.map(r=>[r.run_id,r.counts?.succeeded,r.counts?.failed,r.counts?.incomplete,r.state]));
 if(fingerprint!==observed){
  observed=fingerprint;
  await host.changed?.(runs);
 }
}
export async function refreshSourceSync(){
 if(!session.user||session.changing||loading||working)return;
 const epoch=accountVersion(),gen=generation,current=()=>epoch===accountVersion()&&gen===generation;
 const previous=JSON.stringify(runs),wasLoaded=loaded,hadError=!!error;
 loading=true;error='';
 try{
  const response=await api(root);
  if(response.contract_version!==version||!Array.isArray(response.runs))throw new Error('同步服务版本不匹配，请刷新或联系管理员');
  await accept(response.runs,current);
 }catch(e){if(current())error='暂时无法读取进度：'+e.message+'。后台可能仍在运行，请先刷新进度；这里不代表识别失败。';}
 finally{if(current()){loading=false;if(error||hadError||!wasLoaded||previous!==JSON.stringify(runs))render();poll();}}
}
export async function startSourceSync(sources,consent){
 if(!consent)throw new Error('请先同意发送所选来源内容');
 if(sources.length!==1||sources[0]!=='teaching_web')throw new Error('请先选择教学网来源');
 return act('start',null,{sources,consent:true});
}
async function act(action,id,extra={}){
 if(!session.user||session.changing||working)return;
 const epoch=accountVersion(),gen=++generation,current=()=>epoch===accountVersion()&&gen===generation;
 clearTimeout(timer);loading=false;working=true;error='';render();
 const signature=JSON.stringify([action,id,extra]);
 if(operation?.signature!==signature)operation={signature,id:newId()};
 try{
  const body={contract_version:version,...extra,...(action==='stop'?{}:{operation_id:operation.id})};
  const run=await api(action==='start'?root:root+'/'+encodeURIComponent(id)+'/'+action,body);
  if(!current())return;
  if(run.contract_version!==version||!run.run_id)throw new Error('同步服务版本不匹配');
  operation=null;
  await accept([run,...runs.filter(r=>r.run_id!==run.run_id)].slice(0,20),current);
 }catch(e){if(current())error=e.message;}
 finally{if(current()){working=false;render();poll();}}
}
export async function sourceSyncClick(button){
 const action=button.dataset.sourceSyncAction;
 if(!action)return false;
 if(action==='refresh')await refreshSourceSync();
 else if(action==='stop')await act('stop',button.dataset.run);
 else if(action==='retry')await act('retry',button.dataset.run,{consent:true,...(button.dataset.request?{request_ids:[button.dataset.request]}:{})});
 else if(action==='collect')await startSourceSync(['teaching_web'],true);
 else if(action==='history'){showHistory=!showHistory;render();}
 else if(action==='details'){const id=button.dataset.run;expanded.has(id)?expanded.delete(id):expanded.add(id);render();}
 return true;
}

export function sourceSyncPanel(){
 if(session.user&&!loaded&&!loading&&!working&&!error){loading=true;queueMicrotask(()=>{loading=false;void refreshSourceSync();});}
 if(!session.user)return '';
 const latest=latestAttempts(),blocked=syncIsActive();
 return `<section class="agent-source-sync"><h2>后台同步进度</h2><p>刷新或离开页面后仍会继续；明确任务自动加入，已完成结果会保留。</p><button class="btn" data-source-sync-action="refresh" ${loading||working?'disabled':''}>刷新进度</button>${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${!runs.length?`<p>${loading?'正在读取进度…':'尚无同步记录。'}</p>`:''}${(showHistory?runs:runs.slice(0,3)).map(run=>{
 const c=run.counts||{},done=(c.total||0)-(c.queued||0)-(c.running||0),active=activeStates.has(run.state);
 const current=run.records.filter(r=>latest.get(r.request_id)?.run===run),retry=current.filter(retryable).length;
 const problems=current.filter(r=>['failed','incomplete','cancelled','interrupted'].includes(r.state));
 const continued=run.records.some(r=>latest.get(r.request_id)?.run!==run);
 const recovered=run.records.filter(r=>r.state!=='succeeded'&&latest.get(r.request_id)?.record.state==='succeeded').length;
 const recollect=current.some(r=>r.state==='incomplete'||r.error?.code==='source_changed')||run.error||!run.records.length&&['stopped','interrupted'].includes(run.state);
 const item=r=>{
  const newer=latest.get(r.request_id),superseded=newer?.run!==run;
  return `<li><strong>${esc(r.label)}</strong>${r.course?' · '+esc(r.course):''} · ${esc(itemLabels[r.state])}${r.seconds!=null?' · '+r.seconds+' 秒':''}${superseded?`<p>后续处理：${esc(itemLabels[newer.record.state])} · 运行 ${esc(newer.run.run_id)}</p>`:`${r.error?`<p>${esc(r.error.message)}</p>`:''}${problems.includes(r)?`<p>${esc(guidance(r))}</p>${!active&&retryable(r)?`<button class="btn" data-source-sync-action="retry" data-run="${esc(run.run_id)}" data-request="${esc(r.request_id)}" ${blocked?'disabled':''}>${['cancelled','interrupted'].includes(r.state)?'继续本条':'重试本条'}</button>`:''}`:''}`}</li>`;
 };
 return `<article class="draft-card"><h3>${esc(labels[run.state])}</h3><p class="sync-run-id">运行 ${esc(run.run_id)}${run.parent_run_id?' · 重试自 '+esc(run.parent_run_id):''}</p><p role="status">本轮已处理 ${done}/${c.total||0} 条 · 新增 ${c.added||0} 项 · 失败 ${c.failed||0} 条 · 来源待补全 ${c.incomplete||0} 条 · 跳过 ${c.skipped||0} 条未变化来源</p>${run.state==='crawling'?'<p>正在登录并获取来源；识别完成后会逐条保存。</p>':`<progress max="${Math.max(1,c.total||0)}" value="${done}"></progress>`}${run.error?`<p class="error">${esc(run.error.message)}</p><p>若采集阶段失败，请检查来源账号、校园网连接和采集服务；识别重试不会重新登录或补抓内容。</p><a href="#settings">检查来源设置</a>`:''}
 ${continued?`<p>已发起后续重试或同步；原始计数保留，其中 ${recovered} 条已恢复，其余按最新记录继续处理。</p>`:''}
 ${active?`<button class="btn" data-source-sync-action="stop" data-run="${esc(run.run_id)}" ${working?'disabled':''}>停止本轮同步</button>`:`${retry?`<button class="btn" data-source-sync-action="retry" data-run="${esc(run.run_id)}" ${blocked?'disabled':''}>继续未完成 / 重试失败 ${retry} 条</button><p>重试仅发送尚未恢复的已保存内容；点击批量或单条重试即同意再次发送给识别服务。</p>`:''}${recollect?`<button class="btn" data-source-sync-action="collect" ${blocked?'disabled':''}>重新获取来源并识别</button><p>点击即同意重新登录教学网获取来源并发送待识别内容；已成功结果继续复用。</p>`:''}`}
 <a href="#tasks">查看任务</a> · <a href="#agent">查看待核对内容</a>
 ${problems.length?`<section class="sync-recovery"><h4>需要处理 ${problems.length} 条</h4><ul>${problems.map(item).join('')}</ul></section>`:''}
 ${run.records.length?`<button class="btn" data-source-sync-action="details" data-run="${esc(run.run_id)}" aria-expanded="${expanded.has(run.run_id)}">${expanded.has(run.run_id)?'收起':'展开'}全部逐条结果</button>${expanded.has(run.run_id)?`<ul>${run.records.map(item).join('')}</ul>`:''}`:''}</article>`;
 }).join('')}${runs.length>3?`<button class="btn" data-source-sync-action="history">${showHistory?'收起历史运行':'查看最近 '+runs.length+' 轮（含历史失败）'}</button>`:''}</section>`;
}

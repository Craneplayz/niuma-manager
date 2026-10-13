import {api,session,accountVersion} from './store.js';
import {newId} from './id.js';

const root='/api/sources/sync-runs',version='source-sync/0.1';
const activeStates=new Set(['queued','crawling','recognizing']);
const labels={queued:'等待同步',crawling:'正在获取教学网内容',recognizing:'正在并发识别',succeeded:'同步完成',partial_success:'部分完成',failed:'同步未完成',stopped:'已停止',interrupted:'服务重启后已中断'};
const itemLabels={queued:'等待识别',running:'正在识别',succeeded:'已识别',failed:'识别失败',incomplete:'来源待补全',cancelled:'尚未处理',interrupted:'处理中断'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const visible=()=>['#agent','#sources','#settings'].includes(location.hash);
const retryable=record=>['cancelled','interrupted'].includes(record.state)||record.state==='failed'&&record.error?.retryable;
let host={},runs=[],loaded=false,loading=false,working=false,error='',timer=null,generation=0,operation=null,observed='';

export function configureSourceSync(callbacks){host=callbacks;}
export function resetSourceSync(){clearTimeout(timer);timer=null;generation++;runs=[];loaded=false;loading=false;working=false;error='';operation=null;observed='';}
window.addEventListener('niuma:account-reset',resetSourceSync);
window.addEventListener('hashchange',()=>{clearTimeout(timer);if(visible()&&session.user)void refreshSourceSync();});
function render(){if(visible())host.render?.();}
function poll(){clearTimeout(timer);if(visible()&&runs.some(r=>activeStates.has(r.state))){timer=setTimeout(()=>refreshSourceSync(),2000);timer.unref?.();}}
export function syncIsActive(){return working||runs.some(r=>activeStates.has(r.state));}

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
 loading=true;error='';
 try{
  const response=await api(root);
  if(response.contract_version!==version||!Array.isArray(response.runs))throw new Error('同步服务版本不匹配，请刷新或联系管理员');
  await accept(response.runs,current);
 }catch(e){if(current())error=e.message;}
 finally{if(current()){loading=false;render();poll();}}
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
 else if(action==='retry')await act('retry',button.dataset.run,{consent:true});
 else if(action==='collect')await startSourceSync(['teaching_web'],true);
 return true;
}

export function sourceSyncPanel(){
 if(session.user&&!loaded&&!loading&&!working&&!error){loading=true;queueMicrotask(()=>{loading=false;void refreshSourceSync();});}
 if(!session.user)return '';
 return `<section class="agent-source-sync"><h2>后台同步进度</h2><p>刷新或离开页面后仍会继续；明确任务自动加入，已完成结果会保留。</p><button class="btn" data-source-sync-action="refresh" ${loading||working?'disabled':''}>刷新进度</button>${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${!runs.length?`<p>${loading?'正在读取进度…':'尚无同步记录。'}</p>`:''}${runs.slice(0,3).map(run=>{
 const c=run.counts||{},done=(c.total||0)-(c.queued||0)-(c.running||0),active=activeStates.has(run.state),retry=run.records.filter(retryable).length;
 return `<article class="draft-card"><h3>${esc(labels[run.state])}</h3><p role="status">已处理 ${done}/${c.total||0} 条 · 新增 ${c.added||0} 项 · 失败 ${c.failed||0} 条 · 来源待补全 ${c.incomplete||0} 条 · 跳过 ${c.skipped||0} 条未变化来源</p>${run.state==='crawling'?'<p>正在登录并获取来源；识别完成后会逐条保存。</p>':`<progress max="${Math.max(1,c.total||0)}" value="${done}"></progress>`}${run.error?`<p class="error">${esc(run.error.message)}</p>`:''}
 ${active?`<button class="btn" data-source-sync-action="stop" data-run="${esc(run.run_id)}" ${working?'disabled':''}>停止本轮同步</button>`:`${retry?`<button class="btn" data-source-sync-action="retry" data-run="${esc(run.run_id)}" ${working?'disabled':''}>继续未完成 / 重试失败 ${retry} 条</button><p>只重试已保存内容；点击即同意再次发送这些内容给识别服务。</p>`:''}${c.incomplete||run.error?`<button class="btn" data-source-sync-action="collect" ${working?'disabled':''}>重新获取来源并识别</button><p>重新登录教学网补抓缺失内容，并发送有变化的内容；已成功结果继续复用。</p>`:''}`}
 <a href="#tasks">查看任务</a> · <a href="#agent">查看待核对内容</a>
 ${run.records.length?`<details><summary>逐条处理结果</summary><ul>${run.records.map(r=>`<li><strong>${esc(r.label)}</strong>${r.course?' · '+esc(r.course):''} · ${esc(itemLabels[r.state])}${r.seconds!=null?' · '+r.seconds+' 秒':''}${r.error?`<p>${esc(r.error.message)}</p>`:''}</li>`).join('')}</ul></details>`:''}</article>`;
 }).join('')}</section>`;
}

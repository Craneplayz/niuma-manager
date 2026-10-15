import {api,session,accountVersion,flushStore,getTaskVersion} from './team-store.js';
import {newId} from './id.js';
const root='/api/sources/public',version='public-import/0.1';
export function createPublicSourcesUI(host){
 const esc=host.escapeHtml;let catalog=[],runs=[],source='cs',loading=false,busy=false,error='',notice='',generation=0,timer=null,operation=null;
 const selected=new Map();let mutationRevision=0;
 const stamp=()=>[generation,accountVersion()],valid=s=>s[0]===generation&&s[1]===accountVersion()&&session.user&&!host.isDemo();
 function reset(){generation++;clearTimeout(timer);catalog=[];runs=[];source='cs';loading=busy=false;error=notice='';operation=null;selected.clear();}
 function poll(){clearTimeout(timer);if(host.isVisible()&&runs.some(r=>r.state==='running')){timer=setTimeout(load,2000);timer.unref?.();}}
 async function load(){
  if(loading||busy||!session.user||host.isDemo())return;const s=stamp(),revision=mutationRevision;loading=true;
  try{const [c,r]=await Promise.all([api(root+'/catalog'),api(root+'/runs')]);if(valid(s)){catalog=c.sources;if(revision===mutationRevision){runs=r.runs;error='';}}}
  catch(e){if(valid(s)&&revision===mutationRevision)error=e.message;}
  finally{if(valid(s)){loading=false;host.render();poll();}}
 }
 function input(e){
  const d=e.target.dataset||{};
  if(d.publicField==='source'){if(!busy){source=e.target.value;operation=null;}return true;}
  if(d.publicEvent){if(!busy){if(!selected.has(d.run))selected.set(d.run,new Set());const set=selected.get(d.run);if(e.target.checked)set.add(d.publicEvent);else set.delete(d.publicEvent);}return true;}
  return false;
 }
 async function click(button){
  const action=button.dataset.publicAction;if(!action)return false;
  if(busy||!session.user||host.isDemo())return true;if(action==='refresh'){await load();return true;}
  const s=stamp();busy=true;error=notice='';host.render();
  try{
   if(action==='start'){
    mutationRevision++;
    operation??={contract_version:version,source_id:source,operation_id:newId()};
    const run=await api(root+'/runs',operation);if(!valid(s))return true;operation=null;runs=[run,...runs.filter(r=>r.run_id!==run.run_id)];
   }else if(action==='import'){
    const id=button.dataset.run,ids=[...(selected.get(id)||[])];if(!ids.length)throw new Error('请选择要关注的活动');
    await flushStore();if(!valid(s))return true;
    const out=await api(root+'/runs/'+encodeURIComponent(id)+'/import',{contract_version:version,event_ids:ids,tasks_version:getTaskVersion()});
    if(!valid(s))return true;await host.refresh();if(!valid(s))return true;
    selected.delete(id);notice=`已加入 ${out.added.length} 条通知，${out.already_imported} 条此前已加入；删除的通知可在回收站恢复。`;
   }
  }catch(e){if(valid(s)){
   error=e.message;
   if(action==='import'&&e.status===409){
    try{await host.refresh();if(valid(s))error='任务已变化，已读取最新版本；请重新确认所选活动后加入。';}
    catch{if(valid(s))error='任务已变化且刷新失败，请刷新任务列表后重试。';}
   }
  }}
  finally{if(valid(s)){busy=false;host.render();poll();}}return true;
 }
 function render(){
  if(!catalog.length&&!loading&&!error&&session.user&&!host.isDemo())queueMicrotask(load);
  return `<section class="public-sources"><h2>公开讲座与活动</h2><p class="status-note">获取已有适配器支持的近期活动，选择感兴趣的活动加入通知；不会自动加入全部讲座，也不调用模型。活动时间保留在说明中，不作为截止时间。</p><label class="field">网站<select data-public-field="source" ${busy?'disabled':''}>${catalog.map(c=>`<option value="${esc(c.id)}" ${source===c.id?'selected':''}>${esc(c.label)}</option>`).join('')}</select></label><button class="btn primary" data-public-action="start" ${busy||!catalog.length||runs.some(r=>r.state==='running')?'disabled':''}>获取近期活动</button><button class="btn quiet" data-public-action="refresh" ${loading?'disabled':''}>刷新采集进度</button>${error?`<p class="error" role="alert">${esc(error)}</p>`:''}${notice?`<p role="status">${esc(notice)}</p>`:''}${runs.map(r=>`<article class="draft-card"><h3>${esc(catalog.find(c=>c.id===r.source_id)?.label||r.source_id)} · ${r.state==='running'?'后台采集中':r.state==='failed'?'采集失败':'采集完成'}</h3>${r.error?`<p class="error">${esc(r.error)}</p>`:''}${r.result?`${r.result.pages_failed?`<p>有 ${r.result.pages_failed} 个页面未取得，以下是已取得的活动。</p>`:''}${r.result.events.length?`${r.result.events.map(e=>`<label class="field check-field"><input type="checkbox" data-public-event="${esc(e.id)}" data-run="${esc(r.run_id)}" ${selected.get(r.run_id)?.has(e.id)?'checked':''} ${busy?'disabled':''}><span><strong>${esc(e.title)}</strong><small>${esc(e.start_time||'时间待确认')} ${esc(e.location||'')}</small></span></label><details><summary>查看活动说明</summary><p>${esc(e.description)}</p><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">打开原网页</a></details>`).join('')}<button class="btn" data-public-action="import" data-run="${esc(r.run_id)}" ${busy?'disabled':''}>将所选活动加入通知</button>`:'<p>本次未找到适配范围内的未来活动。</p>'}`:''}</article>`).join('')}</section>`;
 }
 return{render,input,click,load,reset};
}

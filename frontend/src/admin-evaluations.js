const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=v=>typeof v==='number'&&Number.isFinite(v);
const fmt=v=>number(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—';
const pct=v=>number(v)?(v*100).toFixed(1)+'%':'—';
const ratio=(a,b)=>b?a/b:null;
const modules={recognition:'文字/教学网识别',vision:'图像识别',decomposition:'任务拆解',estimation:'任务工时'};
const origins={synthetic:'合成',real_original:'真实原版',real_modified:'真实改写'};
export function summarizeRows(rows){
 const done=rows.filter(r=>r.executed),passed=done.filter(r=>r.status==='ok');
 const latency=done.map(r=>r.seconds).filter(number).sort((a,b)=>a-b);
 const quantile=p=>{if(!latency.length)return null;const x=(latency.length-1)*p,a=Math.floor(x),b=Math.ceil(x);return latency[a]+(latency[b]-latency[a])*(x-a)};
 const tp=done.reduce((s,r)=>s+(r.tp||0),0),fp=done.reduce((s,r)=>s+(r.fp||0),0),fn=done.reduce((s,r)=>s+(r.fn||0),0);
 const costs=done.map(r=>r.estimated_yuan),known=costs.every(number);
 return {detectionCases:done.filter(r=>r.detection_scored).length,n:done.length,planned:rows.filter(r=>r.applicable).length,complete:done.length===rows.filter(r=>r.applicable).length,contract:ratio(passed.length,done.length),precision:ratio(tp,tp+fp),recall:ratio(tp,tp+fn),f1:ratio(2*tp,2*tp+fp+fn),p50:quantile(.5),p90:quantile(.9),p95:quantile(.95),maximum:latency.at(-1)??null,cost:known?costs.reduce((a,b)=>a+b,0):null,costKnown:costs.filter(number).reduce((a,b)=>a+b,0),costUnknown:costs.filter(v=>!number(v)).length,tokens:done.reduce((s,r)=>s+(r.total_tokens||0),0),tokenUnknown:done.filter(r=>!number(r.total_tokens)).length};
}
export function mountEvaluations(root,request){
 const tab=root.querySelector('[data-admin-view="evaluations"]'),panel=root.querySelector('#evaluations-panel');
 let report=null,runs=[],generation=0;
 const select=(name,values,labels={})=>`<label>${esc(({module:"模块",origin:"样本来源",scenario:"场景",family:"模型系列",repeat_index:"重复轮次"})[name]||name)}<select data-eval-filter="${esc(name)}"><option value="">全部</option>${values.map(v=>`<option value="${esc(v)}">${esc(labels[v]||v)}</option>`).join('')}</select></label>`;
 const table=(headers,body)=>`<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
 const cells=values=>'<tr>'+values.map(v=>`<td>${v}</td>`).join('')+'</tr>';
 function filtered(){return(report?.rows||[]).filter(r=>[...panel.querySelectorAll('[data-eval-filter]')].every(s=>!s.value||String(r[s.dataset.evalFilter])===s.value));}
 function draw(){
  const rows=filtered(),total=summarizeRows(rows),models=[...new Set(rows.map(r=>r.model))];
  const groups=models.map(model=>({model,...summarizeRows(rows.filter(r=>r.model===model))}));
  const metric=panel.querySelector('#eval-sort').value;
  groups.sort((a,b)=>Number(b.complete)-Number(a.complete)||(number(a[metric])&&number(b[metric])?(metric==='contract'||metric==='f1'?b[metric]-a[metric]:a[metric]-b[metric]):Number(number(b[metric]))-Number(number(a[metric]))));
  panel.querySelector('#eval-results').innerHTML=`<section class="cards">${[['独立来源组',new Set(rows.map(r=>r.family_id)).size],['已执行 / 适用',`${total.n} / ${total.planned}`],['契约成功率',pct(total.contract)],['实际Token',`${fmt(total.tokens)}${total.tokenUnknown?'（部分未知）':''}`],['已知标准价估算',`¥${fmt(total.costKnown)}${total.costUnknown?'（部分未知）':''}`]].map(([k,v])=>`<article><span>${k}</span><strong>${v}</strong></article>`).join('')}</section><article><h2>模型对比</h2>${table(['模型','覆盖','契约率','Precision','Recall','F1（评分样例）','P50秒','P90秒','P95秒','最大秒','Token','估算元'],groups.map(g=>cells([esc(g.model),`${g.n}/${g.planned}${g.complete?'':' · 未完整'}`,pct(g.contract),pct(g.precision),pct(g.recall),pct(g.f1)+(g.detectionCases?' · '+g.detectionCases+'例':''),fmt(g.p50),fmt(g.p90),fmt(g.p95),fmt(g.maximum),fmt(g.tokens),fmt(g.cost)])).join(''))}</article><section class="charts"><article><h2>契约可用率与成本</h2>${scatter(groups,'cost','每组标准价估算（元）')}</article><article><h2>契约可用率与延迟</h2>${scatter(groups,'p95','P95整例耗时（秒）')}</article></section><article><h2>分项指标与区间</h2>${table(["模型","指标","指标值","分子/分母","独立来源组","来源组全通过率95%区间"],models.flatMap(model=>metricRows(rows.filter(r=>r.model===model)).map(m=>cells([esc(model),esc(({contract:"契约有效",count:"任务数量正确",date:"日期值与精度",action_coverage:"明确动作词覆盖（自动）",explicit_constraint:"明确工时约束",usable_plan:"有步骤的计划",state:"结构化状态",reading_retained:"阅读行动保留",event_time:"活动时间用途",negative_correct:"无任务判定",positive_complete:"估时字段可用",no_invented_schedule:"无无据时段",no_fabricated_deadline:"无虚构日期"})[m.name]||m.name),pct(m.value),`${m.correct}/${m.n}`,m.independent,`${pct(m.interval[0])}–${pct(m.interval[1])}`]))).join(""))}</article><article><h2>场景覆盖热力图</h2>${heatmap(rows,models)}</article><section class="charts"><article><h2>输入 / 输出Token</h2>${tokenBars(rows,models)}</article><article><h2>整例耗时分布</h2>${latencyPlot(rows,models)}</article></section><article><h2>逐例结果与对照</h2><p>真实原文留在服务器私有目录；以下仅展示可共享材料。待复核内容不自动计为语义正确。</p>${table(['样例 / 场景','来源','模型','结果 / 判定','耗时秒','Token','估算元','对照'],rows.map(r=>cells([`${esc(r.id)}<br>${esc(r.purpose||r.scenario)}`,esc(origins[r.origin]||r.origin),esc(r.family),esc(r.executed?(r.error||r.status):r.status==='not_selected_budget'?'预算未纳入完整评测':'待执行'),fmt(r.seconds),fmt(r.total_tokens),fmt(r.estimated_yuan),`<details data-eval-case="${esc(r.id)}" data-eval-model="${esc(r.model)}"><summary>查看字段与输出</summary><pre>展开后读取详细结果</pre></details>`])).join(''))}</article>`;
  if(rows.some(row=>row.scenario==='course_context')){
   const outcomes=['correct','wrong','missed','safe','fallback','unverified','failed'];
   const totals=models.map(model=>{
    const cases=rows.filter(row=>row.model===model&&row.course_context);
    return cells([esc(model),...outcomes.map(key=>cases.length?cases.reduce((total,row)=>total+(row.course_context[key]??0),0):'未测')]);
   });
   panel.querySelector('#eval-results').insertAdjacentHTML('afterbegin','<article><h2>同课程旁证专项</h2><p>按用例计数；冲突或无关旁证正确保留未知计入安全不补，识别失败单列。</p>'+table(['模型版本','补对','误补','漏补或漏任务','安全不补','核对失败回退','输出符合但核对未证实','技术失败'],totals.join(''))+'</article>');
  }
  for(const detail of panel.querySelectorAll('[data-eval-case]'))detail.ontoggle=async()=>{
   if(!detail.open||detail.dataset.loaded||detail.dataset.loading)return;
   detail.dataset.loading='true';const runId=report.id;
   try{const query=new URLSearchParams({case:detail.dataset.evalCase,model:detail.dataset.evalModel});const result=await request('/api/admin/evaluations/'+runId+'?'+query);
    if(!detail.isConnected||report.id!==runId)return;detail.querySelector('pre').textContent=JSON.stringify(result.row,null,2);detail.dataset.loaded='true';
   }catch(e){if(detail.isConnected)detail.querySelector('pre').textContent='详细结果暂不可用，可收起后重试。';}
   finally{delete detail.dataset.loading;}
  }

 }
 function scatter(groups,x,label){
  const points=groups.filter(g=>g.complete&&number(g[x])&&number(g.contract));
  if(!points.length)return'<p>暂无完整可比模型，部分结果不进入散点排名。</p>';
  const max=Math.max(.001,...points.map(g=>g[x]));
  return `<svg viewBox="0 0 600 300" role="img" aria-label="契约成功率与${esc(label)}"><path d="M60 20V250H580" fill="none" stroke="currentColor"/>${[0,.5,1].map(p=>`<text x="5" y="${250-p*220}">${pct(p)}</text>`).join('')}${points.map((g,i)=>`<g tabindex="0"><title>${esc(g.model)}：${pct(g.contract)}，${fmt(g[x])}，${g.n}例</title><circle cx="${60+g[x]/max*500}" cy="${250-g.contract*220}" r="7" fill="${['#525cc5','#14816b','#bd672f','#8b51a5'][i%4]}"/><text x="${65+g[x]/max*480}" y="${240-g.contract*220}" font-size="10">${esc(g.model.split('/').at(-1))}</text></g>`).join('')}<text x="180" y="285">${esc(label)}</text></svg>`;
 }
 function tokenBars(rows,models){
  const data=models.map(model=>{const group=rows.filter(r=>r.model===model&&r.executed);return {model,input:group.reduce((s,r)=>s+(number(r.input_tokens)?r.input_tokens:0),0),output:group.reduce((s,r)=>s+(number(r.output_tokens)?r.output_tokens:0),0),unknown:group.filter(r=>!number(r.input_tokens)||!number(r.output_tokens)).length}});
  const max=Math.max(1,...data.map(r=>r.input+r.output));
  return data.map(r=>`<div class="eval-token-row"><span>${esc(r.model)}</span><div class="eval-token-track" role="img" aria-label="输入${r.input}输出${r.output}Token，${r.unknown}例未知"><i style="width:${r.input/max*100}%;background:#525cc5"></i><i style="width:${r.output/max*100}%;background:#bc7338"></i></div><span>输入 ${fmt(r.input)} · 输出 ${fmt(r.output)}${r.unknown?' · 部分未知':''}</span></div>`).join('');
 }
 function latencyPlot(rows,models){
  const data=models.map(model=>({model,values:rows.filter(r=>r.model===model&&r.executed&&number(r.seconds)).map(r=>r.seconds).sort((a,b)=>a-b)})).filter(r=>r.values.length);
  if(!data.length)return '<p>暂无耗时数据</p>';
  const max=Math.max(1,...data.flatMap(r=>r.values)),x=value=>140+value/max*400;
  return `<svg viewBox="0 0 600 ${data.length*45+45}" role="img" aria-label="整例耗时四分位分布，包含失败"><text x="140" y="16">0秒</text><text x="500" y="16">${fmt(max)}秒</text>${data.map((r,i)=>{const q=p=>{const index=(r.values.length-1)*p,a=Math.floor(index),b=Math.ceil(index);return r.values[a]+(r.values[b]-r.values[a])*(index-a)},y=i*45+40;return `<g><title>${esc(r.model)}：${r.values.length}例，中位数${fmt(q(.5))}秒</title><text x="2" y="${y+4}" font-size="10">${esc(r.model.split('/').at(-1))}</text><path d="M${x(q(0))} ${y}H${x(q(1))}" stroke="#888"/><rect x="${x(q(.25))}" y="${y-10}" width="${Math.max(1,x(q(.75))-x(q(.25)))}" height="20" fill="#cbd0ed"/><path d="M${x(q(.5))} ${y-12}V${y+12}" stroke="#253d6d" stroke-width="2"/></g>`}).join('')}</svg>`;
 }
 function heatmap(rows,models){
  const scenarios=[...new Set(rows.map(r=>r.scenario))];
  return table(['场景',...models],scenarios.map(s=>cells([esc(s),...models.map(m=>{const r=rows.filter(x=>x.model===m&&x.scenario===s&&x.executed),n=r.filter(x=>x.status==='ok').length;return r.length?`<span class="eval-cell" style="--eval-alpha:${.08+.5*n/r.length}">${n}/${r.length} · ${pct(n/r.length)}</span>`:'—'})])).join(''));
 }
 async function loadRun(id){
  const current=++generation;
  try{const value=await request('/api/admin/evaluations/'+id);if(current!==generation)return;report=value;
   const values=key=>[...new Set(report.rows.map(r=>r[key]))];
   panel.querySelector('#eval-filters').innerHTML=select('module',values('module'),modules)+select('origin',values('origin'),origins)+select('scenario',values('scenario'))+select('family',values('family'))+select('repeat_index',values('repeat_index'));
   const budget=report.budget;
   panel.querySelector('#eval-version').textContent=`源码 ${report.sourceVersion} · 数据 ${report.datasetHash.slice(0,12)}${report.scoringHash?' · 评分 '+report.scoringHash.slice(0,12):''}${budget?` · 全轮预算 ¥${fmt(budget.limitYuan)}，已知估算 ¥${fmt(budget.knownYuan)}，含未结算预留 ¥${fmt(budget.committedYuan)}`:''}。费用为标准价估算。`;
   for(const filter of panel.querySelectorAll('[data-eval-filter]'))filter.onchange=draw;draw();
  }catch(e){panel.querySelector('[role="status"]').textContent=e.message;}
 }
 tab.onclick=async()=>{
  for(const button of root.querySelectorAll('[data-admin-view]'))button.setAttribute('aria-pressed',String(button===tab));
  root.querySelector('#usage-panel').hidden=true;root.querySelector('#operations-panel').hidden=true;panel.hidden=false;root.querySelector('#page-title').textContent='模型评测';
  if(panel.dataset.loaded)return;
  panel.innerHTML='<p role="status">正在读取评测…</p>';
  try{({runs}=await request('/api/admin/evaluations'));panel.dataset.loaded='true';
   panel.innerHTML=`<p role="status"></p><div class="eval-controls"><label>评测运行<select id="eval-run">${runs.map(r=>`<option value="${esc(r.id)}">${esc(({probe:'接入探测',formal:'完整集评测',diagnostic:'诊断'})[r.phase]||'评测')} · ${esc(r.sourceVersion)} · ${r.id.slice(0,8)}</option>`).join('')}</select></label><label>排序<select id="eval-sort"><option value="contract">契约成功率</option><option value="f1">任务F1</option><option value="p95">P95耗时</option><option value="cost">费用</option></select></label><button id="eval-refresh">刷新运行</button><button id="eval-export">导出当前筛选JSON</button><button id="eval-csv">导出CSV</button></div><p id="eval-version"></p><div id="eval-filters" class="eval-controls"></div><div id="eval-results"></div>`;
   panel.querySelector('#eval-run').onchange=e=>loadRun(e.target.value);panel.querySelector('#eval-sort').onchange=draw;
   panel.querySelector('#eval-export').onclick=async()=>{const current=report;if(!current)return;const identities=new Set(filtered().map(r=>r.id+'\0'+r.model));let full;try{full=await request('/api/admin/evaluations/'+current.id+'?full=1')}catch(e){panel.querySelector('[role="status"]').textContent='导出失败，请重试。';return;}if(report!==current)return;const {rows,summary,...metadata}=full;const selected=rows.filter(r=>identities.has(r.id+'\0'+r.model));const url=URL.createObjectURL(new Blob([JSON.stringify({...metadata,rows:selected},null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='model-evaluation.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),0)};
   panel.querySelector('#eval-refresh').onclick=()=>{delete panel.dataset.loaded;void tab.onclick()};
   panel.querySelector('#eval-csv').onclick=()=>{const url=URL.createObjectURL(new Blob(['\uFEFF'+csvRows(filtered())],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='model-evaluation.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),0)};
   if(runs.length){panel.querySelector('#eval-run').value=runs.at(-1).id;await loadRun(runs.at(-1).id);}else panel.querySelector('[role="status"]').textContent='尚未发布真实模型评测结果。';
  }catch(e){panel.querySelector('[role="status"]').textContent=e.message;}
 };
 for(const button of root.querySelectorAll('[data-admin-view]'))if(button!==tab)button.addEventListener('click',()=>{panel.hidden=true});
}

export function metricRows(rows){
 const groups={};
 for(const row of rows.filter(r=>r.executed))for(const[key,value]of Object.entries(row.checks||{})){
  if(typeof value!=='boolean')continue;
  const metric=key.startsWith('date_')?'date':key.startsWith('action_')?'action_coverage':key.startsWith('explicit_')?'explicit_constraint':key;
  const group=groups[metric]??={name:metric,n:0,correct:0,families:new Map()};group.n++;group.correct+=Number(value);
  group.families.set(row.family_id,(group.families.get(row.family_id)??true)&&value);
 }
 return Object.values(groups).map(g=>{const n=g.families.size,k=[...g.families.values()].filter(Boolean).length,p=k/n,z=1.959963984540054,scale=1+z*z/n,center=(p+z*z/(2*n))/scale,radius=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/scale;return {...g,value:g.correct/g.n,independent:n,interval:[Math.max(0,center-radius),Math.min(1,center+radius)]}});
}
export function csvRows(rows){
 const columns=['id','family','module','origin','scenario','status','seconds','input_tokens','output_tokens','total_tokens','estimated_yuan'];
 const cell=value=>{let s=String(value??'');if(/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"'};
 return [columns,...rows.map(r=>columns.map(k=>r[k]))].map(values=>values.map(cell).join(',')).join('\r\n');
}

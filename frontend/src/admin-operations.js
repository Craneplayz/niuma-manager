const escape=value=>String(value??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rows=value=>Array.isArray(value)?value.filter(row=>row&&typeof row==='object'):[];
const number=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
const count=value=>number(value)===null?'—':String(value);
const ms=value=>number(value)===null?'—':`${value.toLocaleString('zh-CN')} ms`;
const percent=value=>number(value)===null?'—':`${value.toFixed(1)}%`;
const bytes=value=>number(value)===null?'—':`${(value/1024**3).toFixed(2)} GB`;
const pair=(used,total)=>`${bytes(used)} / ${bytes(total)}`;
const runLabels={succeeded:'成功',partial_success:'部分成功',failed:'失败',stopped:'已停止',interrupted:'已中断',no_tasks:'无任务',filtered:'已过滤'};
const stageLabels={http:'接口请求',model_queue:'模型排队',sync_queue:'同步排队',collection:'采集总耗时含下载',attachment_download:'附件下载',attachment_read:'附件读取',model_call:'模型调用',recognition:'识别总耗时',validation:'结果校验',save:'保存',database_lock:'数据库写锁等待',commit:'提交',outcome:'业务结果',run:'运行'};
const operationLabels={sync:'来源同步',recognition:'文本识别',planning:'生成规划',decomposition:'任务拆解',materials:'材料处理',workspace:'工作区',sources:'来源管理',background:'后台任务',other:'其他操作',auth:'账号', 'auth.login':'登录','auth.register':'注册','auth.logout':'退出登录','auth.me':'读取会话','auth.email_code':'发送邮箱验证码','auth.verify_email':'验证邮箱','auth.reset_password':'重置密码','auth.password':'修改密码','auth.profile':'修改资料','auth.other':'其他账号操作'};
const codeLabels={...runLabels,ok:'成功',http_400:'请求参数错误',unauthorized:'未登录或会话失效',forbidden:'无访问权限',not_found:'未找到',conflict:'数据冲突',too_large:'内容过大',rate_limited:'请求限流',queue_unavailable:'队列不可用',model_configuration:'模型配置异常',agent_timeout:'模型响应超时',agent_unavailable:'模型服务不可用',invalid_agent_output:'模型结果格式异常',collection_failed:'采集失败',source_changed:'来源已变更',source_incomplete:'来源内容不完整',save_failed:'保存失败',database_busy:'数据库繁忙',cache_hit:'缓存命中',cache_miss:'缓存未命中',retry:'重试',upstream_error:'上游服务异常',internal_error:'内部错误',cancelled:'已取消',output_truncated:'输出被截断'};
const stateLabels={...runLabels,ok:'成功',cancelled:'已取消'};
Object.assign(codeLabels,{queue_timeout:'同步排队超时',queue_full:'同步排队已满',download_failed:'附件下载失败'});
const alertLabels={pending:'未触发',firing:'待通知',active:'告警中',recovering:'待恢复通知',resolved:'已恢复'};
const label=(labels,value)=>Object.hasOwn(labels,value)?labels[value]:value;
function operationLabel(value){
 if(Object.hasOwn(operationLabels,value))return operationLabels[value];
 const [name,method,...rest]=String(value??'').split('.'),verb={get:'读取',post:'提交',put:'更新',patch:'修改',delete:'删除'};
 return !rest.length&&Object.hasOwn(operationLabels,name)&&Object.hasOwn(verb,method)?`${operationLabels[name]} · ${verb[method]}`:value;
}
const beijingTime=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
export function formatBeijingTime(value){
 const timestamp=typeof value==='number'&&number(value)!==null?value*1000:typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)?Date.parse(value):NaN;
 if(!Number.isFinite(timestamp)||!Number.isFinite(new Date(timestamp).getTime()))return '—';
 const parts=Object.fromEntries(beijingTime.formatToParts(timestamp).map(part=>[part.type,part.value]));
 return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
const resourceExpired=(resource,now)=>number(resource?.observedAt)!==null&&now-resource.observedAt*1000>180000;
const table=(headers,body)=>`<div class="table-wrap" tabindex="0" role="region" aria-label="${escape(headers[0])}明细"><table><thead><tr>${headers.map(h=>`<th scope="col">${escape(h)}</th>`).join('')}</tr></thead><tbody>${body.length?body.map(row=>`<tr>${row.map(cell=>`<td>${escape(cell)}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}" class="empty">此时间范围暂无记录</td></tr>`}</tbody></table></div>`;
const cards=items=>`<section class="cards">${items.map(([label,value])=>`<article><span>${escape(label)}</span><strong>${escape(value)}</strong></article>`).join('')}</section>`;
function timingChart(data,key){
 const max=Math.max(1,...data.map(row=>number(row.p95Ms)??0));
 return `<div class="timing-chart"><p class="chart-key">浅蓝 P50 · 蓝色 P95（毫秒）</p>${data.map(row=>`<div class="timing-row"><span>${escape(label(stageLabels,row[key]))}</span><div class="timing-bars"><i class="p50" style="width:${Math.min(100,(number(row.p50Ms)??0)/max*100)}%"></i><i style="width:${Math.min(100,(number(row.p95Ms)??0)/max*100)}%"></i></div><small>${escape(ms(row.p50Ms))} / ${escape(ms(row.p95Ms))}</small></div>`).join('')||'<p class="empty">此时间范围暂无记录</p>'}</div>`;
}
function qualityChart(data){
 return `<div class="quality-chart"><p class="chart-key">蓝色 成功 · 红色 失败 · 灰色 其他 / 未知</p>${data.map(row=>{
  const total=number(row.count),width=value=>total>0?Math.min(100,(number(value)??0)/total*100):0;
  return `<div class="timing-row"><span>${escape(operationLabel(row.operation))}</span><div class="quality-bar"><i style="width:${width(row.success)}%"></i><i class="failed" style="width:${Math.min(100-width(row.success),width(row.failures))}%"></i></div><small>成功 ${escape(count(row.success))} / ${escape(count(row.count))} · 失败 ${escape(count(row.failures))}</small></div>`;
 }).join('')||'<p class="empty">此时间范围暂无记录</p>'}</div>`;
}
export function operationsUrl(start='',end=''){
 return '/api/admin/operations'+(start&&end?`?${new URLSearchParams({start,end})}`:'');
}
export function renderOperations(input={},now=Date.now()){
 const data=input??{},requests=rows(data.requests),stages=rows(data.stages),queue=data.queue??{};
 const total=key=>Array.isArray(data.requests)&&requests.length===data.requests.length&&requests.every(row=>number(row[key])!==null)?requests.reduce((sum,row)=>sum+row[key],0):null;
 const all=total('count'),success=total('success');
 const expired=resourceExpired(data.resources,now),resource=data.resources?.available===true&&!expired?data.resources:null;
 return `<p class="operations-meta">环境：${escape(label({production:'正式环境',test:'测试环境',development:'开发环境'},data.environment))} · 版本：${escape(data.revision)}<br>时间均为北京时间 · 更新时间：${escape(formatBeijingTime(data.generatedAt))} · 范围：${escape(formatBeijingTime(data.start))} → ${escape(formatBeijingTime(data.end))} · 保留 ${escape(count(data.retentionDays))} 天</p>
 ${cards([['请求总数',count(all)],['请求成功率',all>0&&success!==null?percent(success/all*100):'—'],['失败请求',count(total('failures'))],['丢弃事件',count(data.droppedEvents)]])}
 <section class="charts"><article><h2>请求质量与耗时</h2>${qualityChart(requests)}${table(['操作','请求','成功','失败','P50','P95','错误码 / 次数'],requests.map(row=>[operationLabel(row.operation),count(row.count),count(row.success),count(row.failures),ms(row.p50Ms),ms(row.p95Ms),rows(row.errors).map(e=>`${label(codeLabels,e.code)??'—'} / ${count(e.count)}`).join('；')||'—']))}</article><article><h2>阶段耗时</h2>${timingChart(stages,'stage')}${table(['阶段','次数','成功','失败','P50','P95'],stages.map(row=>[label(stageLabels,row.stage),count(row.count),count(row.success),count(row.failures),ms(row.p50Ms),ms(row.p95Ms)]))}</article></section>
 <article><h2>当前队列</h2>${table(['队列','排队','运行中','最早等待（秒）'],[['作业',count(queue.queued),count(queue.running),count(queue.oldestWaitSeconds)],['模型',count(queue.modelQueued),count(queue.modelRunning),count(queue.modelOldestWaitSeconds)]])}</article>
 <article><h2>模型实际用量</h2><p>已收到调用观测 ${escape(count(data.providerUsage?.observedCalls))} 次；下列仅累加供应商已返回的用量，缺失值不算零，不估算费用。</p>${table(['用量','已知 Token 合计','已知调用','用量未知调用'],[['input_tokens','输入'],['output_tokens','输出'],['total_tokens','总计']].map(([key,name])=>[name,count(data.providerUsage?.[key]?.value),count(data.providerUsage?.[key]?.known),count(data.providerUsage?.[key]?.unknown)]))}</article>
 <article><h2>资源快照</h2><p class="empty">采样时间：${escape(formatBeijingTime(data.resources?.observedAt))}</p>${expired?'<p role="status">采样已过期</p>':resource?'':'<p class="empty">暂无可用采样</p>'}${cards([['CPU',percent(resource?.cpuPercent)],['内存（已用 / 总量）',pair(resource?.memoryUsedBytes,resource?.memoryTotalBytes)],['磁盘（已用 / 总量）',pair(resource?.diskUsedBytes,resource?.diskTotalBytes)],['重启次数',count(resource?.restarts)]])}</article>
 <section class="charts"><article><h2>运行结果</h2>${table(['结果','次数'],Object.entries(runLabels).map(([key,label])=>[label,count(data.runs?.[key])]))}</article><article><h2>业务结果码</h2>${table(['结果码','次数'],rows(data.outcomes).map(row=>[label(codeLabels,row.code),count(row.count)]))}</article></section>
 <article><h2>告警记录</h2>${table(['告警码','状态','首次出现','最近出现','最近通知','通知投递'],rows(data.alerts).map(row=>[label(codeLabels,row.code),label(alertLabels,row.state),formatBeijingTime(row.firstSeen),formatBeijingTime(row.lastSeen),formatBeijingTime(row.lastNotified),row.deliveryError===true?'通知失败，待重试':'—']))}</article>
 <article><h2>只读追踪</h2>${table(['时间','请求 ID','运行 ID','阶段','操作','结果','结果码','耗时'],rows(data.trace).map(row=>[formatBeijingTime(row.at),row.requestId,row.runId,label(stageLabels,row.stage),operationLabel(row.operation),label(stateLabels,row.result),label(codeLabels,row.code),ms(row.durationMs)]))}</article><footer>${escape(data.methodology??'')} 队列、资源为当前快照。</footer>`;
}

export function mountOperations(root,request){
 const usage=root.querySelector('#usage-panel'),panel=root.querySelector('#operations-panel'),title=root.querySelector('#page-title');
 const tabs=[...root.querySelectorAll('[data-admin-view]')];
 const usageRange=usage.querySelector('#range');
 panel.innerHTML=`<form id="operations-range"><label>开始日期<input name="start" type="date" required></label><label>结束日期<input name="end" type="date" required></label><button>查看</button><button type="button" id="operations-recent">最近 24 小时</button></form><p id="operations-status" role="status" aria-live="polite"></p><button type="button" id="operations-login" hidden>重新验证管理员会话</button><div id="operations-content"></div>`;
 const form=panel.querySelector('form'),status=panel.querySelector('#operations-status'),content=panel.querySelector('#operations-content');
 const login=panel.querySelector('#operations-login');
 login.onclick=()=>location.reload();
 let loaded=false,pending=false,generation=0,resourceTimer;
 const syncDates=()=>{for(const name of ['start','end'])form.elements[name].value=usageRange.elements[name].value;};
 async function load(start='',end=''){
  const current=++generation;
  clearTimeout(resourceTimer);pending=true;login.hidden=true;
  status.setAttribute('role','status');status.textContent='正在加载运行监控…';content.setAttribute('aria-busy','true');
  try{
   const data=await request(operationsUrl(start,end));
   if(current!==generation||!content.isConnected)return;
   loaded=true;
   content.innerHTML=renderOperations(data);status.textContent=start?'已按日期范围更新':'最近 24 小时';
   const remaining=number(data?.resources?.observedAt)===null?-1:data.resources.observedAt*1000+180000-Date.now();
   if(remaining>=0&&remaining<=180000)resourceTimer=setTimeout(()=>{if(current===generation&&content.isConnected)content.innerHTML=renderOperations(data);},remaining+1);
  }catch(error){
   if(current!==generation||!content.isConnected)return;
   loaded=false;login.hidden=error.status!==401&&error.status!==403;
   content.innerHTML='';status.setAttribute('role','alert');status.textContent=!login.hidden?'管理员会话已失效或无权读取，请重新验证管理员会话。':'运行监控加载失败，请重试。';
  }finally{if(current===generation){pending=false;content.setAttribute('aria-busy','false');}}
 }
 for(const tab of tabs)tab.onclick=()=>{
  const operations=tab.dataset.adminView==='operations';
  usage.hidden=operations;panel.hidden=!operations;title.textContent=operations?'运行监控':'使用数据';
  for(const item of tabs)item.setAttribute('aria-pressed',String(item===tab));
  if(operations){syncDates();if(!loaded&&!pending)void load();}
 };
 form.onsubmit=event=>{event.preventDefault();const {start,end}=form.elements;if(start.value>end.value){status.setAttribute('role','alert');status.textContent='开始日期不能晚于结束日期';return;}for(const name of ['start','end'])usageRange.elements[name].value=form.elements[name].value;void load(start.value,end.value);};
 panel.querySelector('#operations-recent').onclick=()=>void load();
}

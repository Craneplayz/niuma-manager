const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function sourceLink(url,label='打开教学网原页面'){
 try{const u=new URL(url);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)return '';return `<a class="story-link" href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;}catch{return '';}
}
export function evidenceMarkup(entries){
 if(!entries.length)return '<p class="evidence-empty">这项任务没有保存识别证据，可能是手动创建或旧记录。</p>';
 return entries.map(entry=>{
 const original=entry.original||'';
 const quotes=[...new Set((entry.quotes||[]).map(evidence=>evidence.quote).filter(quote=>quote&&(!original.trim()||!original.includes(quote))))];
 const sources=[...(entry.sources||[])];if(entry.sourceUrl&&!sources.some(s=>s.url===entry.sourceUrl))sources.unshift({url:entry.sourceUrl,label:entry.sourceLabel,platform:entry.platform});
 return `<article class="task-evidence-entry"><div class="task-evidence-links">${sources.map(s=>`${esc(s.label||entry.sourceLabel||'通知来源')} ${sourceLink(s.url,(s.platform||entry.platform)==='teaching_web'?'打开教学网原页面':'打开来源页面')}`).join('<br>')}</div>${original?`<details><summary>查看完整通知原文</summary><pre>${esc(original)}</pre></details>`:''}${quotes.length?`<section class="task-evidence-quotes"><h4>来源证据摘录</h4>${quotes.map(quote=>`<blockquote>${esc(quote)}</blockquote>`).join('')}</section>`:''}</article>`;
 }).join('');
}
export async function loadTaskEvidence(taskId,container,request){
 try{const result=await request('/api/recognition/task-evidence',{task_id:taskId});if(container.isConnected)container.innerHTML=evidenceMarkup(result.evidence||[]);}
 catch(error){if(container.isConnected)container.innerHTML=`<p class="error">${esc(error.message)}</p><button type="button" class="btn" data-task-evidence="${esc(taskId)}">重试查看证据</button>`;}
}

import { userErrorMarkup } from './user-errors.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function sourceLink(url,label='打开教学网原页面'){
 try{const u=new URL(url);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)return '';return `<a class="story-link" href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;}catch{return '';}
}
function evidenceQuote(evidence,entry){
 const source=(entry.sources||[]).find(source=>source.input_id===evidence.input_id);
 const labels=[source?.label||entry.sourceLabel];
 if(evidence.asset_id)labels.push('附件 '+evidence.asset_id);
 const locator=evidence.locator;
 if(locator?.page!=null)labels.push('第 '+locator.page+' 页');
 if(locator?.paragraph!=null)labels.push('第 '+locator.paragraph+' 段');
 if(locator?.bbox)labels.push('区域 ['+locator.bbox.join(', ')+']');
 if(evidence.verification==='model_reported')labels.push('模型报告（model_reported），未经逐字核验');
 else if(evidence.verification)labels.push(evidence.verification);
 return `<blockquote>${labels.filter(Boolean).length?`<small>${labels.filter(Boolean).map(esc).join(' · ')}</small>`:''}${esc(evidence.quote)}</blockquote>`;
}
export function evidenceMarkup(entries){
 if(!entries.length)return '<p class="evidence-empty">这项任务没有保存识别证据。</p>';
 return entries.map(entry=>{
 const original=entry.original||'';
 const seen=new Set();
 const quotes=(entry.quotes||[]).filter(evidence=>{
  if(!evidence.quote)return false;
  const independent=evidence.asset_id||evidence.locator||evidence.verification;
  if(!independent&&original.includes(evidence.quote))return false;
  const key=JSON.stringify([evidence.input_id,evidence.field,evidence.asset_id,evidence.quote,evidence.locator,evidence.verification]);
  if(seen.has(key))return false;seen.add(key);return true;
 });
 const sources=[...(entry.sources||[])];if(entry.sourceUrl&&!sources.some(s=>s.url===entry.sourceUrl))sources.unshift({url:entry.sourceUrl,label:entry.sourceLabel,platform:entry.platform});
 return `<article class="task-evidence-entry"><div class="task-evidence-links">${sources.map(s=>`${esc(s.label||entry.sourceLabel||'通知来源')} ${sourceLink(s.url,(s.platform||entry.platform)==='teaching_web'?'打开教学网原页面':'打开来源页面')}`).join('<br>')}</div>${original?`<details><summary>查看完整通知原文</summary><pre>${esc(original)}</pre></details>`:''}${quotes.length?`<section class="task-evidence-quotes"><h4>来源证据摘录</h4>${quotes.map(evidence=>evidenceQuote(evidence,entry)).join('')}</section>`:''}</article>`;
 }).join('');
}
export async function loadTaskEvidence(taskId,container,request){
 try{const result=await request('/api/recognition/task-evidence',{task_id:taskId});if(container.isConnected)container.innerHTML=evidenceMarkup(result.evidence||[]);}
 catch(error){if(container.isConnected)container.innerHTML=`${userErrorMarkup(error,'task_evidence')}<button type="button" class="btn" data-task-evidence="${esc(taskId)}">重试查看原文</button>`;}
}

const MAX_FILE_BYTES=10_000_000;

export function parseSourceFile(raw){
 if(new TextEncoder().encode(raw).length>MAX_FILE_BYTES)throw new Error('文件超过 10 MB，请缩小导出范围');
 let entries;
 try{entries=[JSON.parse(raw)];}
 catch{try{entries=raw.split(/\r?\n/).filter(Boolean).map(line=>JSON.parse(line));}catch{throw new Error('文件不是有效的 JSON 或 JSONL');}}
 if(entries.length===1&&entries[0]?.schema_version===1&&Array.isArray(entries[0].messages)){
  const chat=entries[0];
  const messages=chat.messages.filter(m=>m&&m.type_code===1&&typeof m.content==='string'&&m.content.trim()).slice(-100);
  if(!messages.length)throw new Error('导出文件中没有可选择的文本消息');
  return {kind:'wechat',chatName:String(chat.chat_name||'微信会话'),messages};
 }
 const requests=entries.length===1&&Array.isArray(entries[0])?entries[0]:entries;
 const invalid=requests.some(r=>
  r?.contract_version!=='0.2'||typeof r.request_id!=='string'||!r.request_id||
  !Array.isArray(r.records)||!r.records.length||r.records.length>100||
  r.records.some(record=>!record||typeof record!=='object'||typeof record.text!=='string')||
  new TextEncoder().encode(JSON.stringify(r)).length>1_900_000);
 if(!requests.length||requests.length>20||invalid||new Set(requests.map(r=>r.request_id)).size!==requests.length)
  throw new Error('请选择 v0.2 请求文件（每批 1–100 条，最多 20 批）');
 return {kind:'requests',requests,sent:new Set()};
}

export function wechatRequest(messages,selected){
 const chosen=[...selected].sort((a,b)=>a-b).map(index=>messages[index]);
 if(!chosen.length||chosen.length>20||chosen.some(m=>!m))throw new Error('请选择 1–20 条文本消息');
 return {contract_version:'0.2',request_id:'wechat:manual:'+crypto.randomUUID(),records:chosen.map(message=>{
  const time=typeof message.time==='string'&&/([+-]\d\d:\d\d|Z)$/.test(message.time)&&Number.isFinite(Date.parse(message.time))?message.time:null;
  return {input_id:'wechat:manual:'+crypto.randomUUID(),text:message.content,text_quality:'complete',platform:'wechat',
   published_at:{value:time,precision:time?'datetime':'unknown',basis:time?'upstream_unverified':'unknown'},
   collected_at:null,timezone:'Asia/Shanghai',timezone_basis:'caller_default',source_url:null,
   source_label:'用户选择的微信消息',source_item_id:null,course_or_project:null,context:[],upstream_hints:null};
 })};
}

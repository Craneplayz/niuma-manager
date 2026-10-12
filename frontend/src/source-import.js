import {newId} from './id.js';
const MAX_FILE_BYTES=10_000_000;
export const wechatEnabled=import.meta.env?.VITE_ENABLE_WECHAT!=='0';

export async function readManualTextFile(file){
 if(!/\.(txt|json|jsonl)$/i.test(file.name)||!file.size||file.size>16000)throw new Error('请选择不超过 4000 字的 UTF-8 文字或 JSON 文件；较大文件可在导出记录入口分批选择');
 let text;
 try{text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}
 catch{throw new Error('无法读取文字文件，请使用 UTF-8 编码');}
 if(!text.trim()||text.length>4000)throw new Error('文字内容须为 1–4000 字，请分段整理');
 if(/^(-----BEGIN PGP MESSAGE-----|age-encryption\.org\/v1|Salted__)/.test(text.trimStart()))throw new Error('不接受加密数据，请自行导出未加密的记录');
 try{const value=JSON.parse(text);if(value?.encrypted===true||value?.is_encrypted===true)throw new Error('不接受加密数据，请自行导出未加密的记录');}catch(error){if(!(error instanceof SyntaxError))throw error;}
 return text;
}

export function parseSourceFile(raw){
 if(new TextEncoder().encode(raw).length>MAX_FILE_BYTES)throw new Error('文件超过 10 MB，请缩小导出范围');
 let entries;
 try{entries=[JSON.parse(raw)];}
 catch{try{entries=raw.split(/\r?\n/).filter(Boolean).map(line=>JSON.parse(line));}catch{throw new Error('文件不是有效的 JSON 或 JSONL');}}
 if(entries.some(entry=>entry?.encrypted===true||entry?.is_encrypted===true))throw new Error('不接受加密数据，请自行导出未加密的记录');
 if(entries.length===1&&entries[0]?.schema_version===1&&Array.isArray(entries[0].messages)){
  const chat=entries[0];
  const messages=chat.messages.filter(m=>m&&m.type_code===1&&typeof m.content==='string'&&m.content.trim()).slice(-100);
  if(!messages.length)return {kind:'text',text:raw};
  return {kind:'wechat',chatName:String(chat.chat_name||'微信会话'),messages};
 }
 const requests=entries.length===1&&Array.isArray(entries[0])?entries[0]:entries;
 const invalid=requests.some(r=>
  r?.contract_version!=='0.2'||typeof r.request_id!=='string'||!r.request_id||
  !Array.isArray(r.records)||!r.records.length||r.records.length>100||
  r.records.some(record=>!record||typeof record!=='object'||typeof record.text!=='string')||
  new TextEncoder().encode(JSON.stringify(r)).length>1_900_000);
 if(!requests.length||invalid)return {kind:'text',text:raw};
 if(requests.length>20||new Set(requests.map(r=>r.request_id)).size!==requests.length)
  throw new Error('每次最多 20 批，请分批导入且保持请求编号唯一');
 return {kind:'requests',requests,sent:new Set()};
}

export function wechatRequest(messages,selected){
 const chosen=[...selected].sort((a,b)=>a-b).map(index=>messages[index]);
 if(!chosen.length||chosen.length>20||chosen.some(m=>!m))throw new Error('请选择 1–20 条文本消息');
 return {contract_version:'0.2',request_id:'wechat:manual:'+newId(),records:chosen.map(message=>{
  const time=typeof message.time==='string'&&/([+-]\d\d:\d\d|Z)$/.test(message.time)&&Number.isFinite(Date.parse(message.time))?message.time:null;
  return {input_id:'wechat:manual:'+newId(),text:message.content,text_quality:'complete',platform:wechatEnabled?'wechat':'manual',
   published_at:{value:time,precision:time?'datetime':'unknown',basis:time?'upstream_unverified':'unknown'},
   collected_at:null,timezone:'Asia/Shanghai',timezone_basis:'caller_default',source_url:null,
   source_label:'用户选择的微信消息',source_item_id:null,course_or_project:null,context:[],upstream_hints:null};
 })};
}

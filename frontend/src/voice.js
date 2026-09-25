import {session,api} from './store.js';
export function voiceSupport(){return {browser:Boolean(window.SpeechRecognition||window.webkitSpeechRecognition),cloud:Boolean(window.MediaRecorder&&navigator.mediaDevices?.getUserMedia&&session.aiConfigured&&session.user)};}
let cancelActive=null;
export function cancelVoice(){cancelActive?.();cancelActive=null;}
export function startVoice({onInterim,onResult,onError,onState}){
 cancelVoice();const support=voiceSupport();
 if(support.browser){
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;const recognition=new Recognition();
  let cancelled=false,ended=false,finalText='',interim='',timer;
  recognition.lang='zh-CN';recognition.continuous=false;recognition.interimResults=true;
  const finish=()=>{clearTimeout(timer);onState('idle');cancelActive=null;};
  recognition.onresult=e=>{finalText='';interim='';for(let i=0;i<e.results.length;i++){if(e.results[i].isFinal)finalText+=e.results[i][0].transcript;else interim+=e.results[i][0].transcript;}onInterim(finalText+interim);};
  recognition.onerror=e=>{ended=true;finish();if(cancelled)return;const messages={'not-allowed':'麦克风权限未开启，请在浏览器允许访问后重试。','audio-capture':'没有找到可用麦克风。','network':'浏览器语音服务无法连接，可以改用文字输入。','no-speech':'没有听清，请再试一次。','service-not-allowed':'当前浏览器未启用语音服务，请换用支持语音的浏览器。'};onError(messages[e.error]||'语音识别中断，请重试或输入文字。');};
  recognition.onend=()=>{finish();if(cancelled||ended)return;const text=(finalText||interim).trim();if(text)onResult(text);else onError('没有识别到内容，请再试一次。');};
  cancelActive=()=>{cancelled=true;recognition.abort();finish();};
  try{recognition.start();onState('listening');timer=setTimeout(()=>recognition.stop(),30000);}catch{finish();onError('语音服务无法启动，请使用文字输入。');}
  return ()=>recognition.stop();
 }
 if(support.cloud){let recorder,stream,cancelled=false,timer;const chunks=[];
  cancelActive=()=>{cancelled=true;clearTimeout(timer);if(recorder?.state==='recording')recorder.stop();stream?.getTracks().forEach(t=>t.stop());onState('idle');};
  onState('starting');
  navigator.mediaDevices.getUserMedia({audio:true}).then(s=>{
   stream=s;if(cancelled){stream.getTracks().forEach(t=>t.stop());return;}
   const mime=['audio/webm','audio/mp4','audio/ogg'].find(m=>MediaRecorder.isTypeSupported(m));if(!mime)throw new Error('当前浏览器没有支持的录音格式');
   recorder=new MediaRecorder(stream,{mimeType:mime});
   recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
   recorder.onerror=()=>{cancelled=true;clearTimeout(timer);stream.getTracks().forEach(t=>t.stop());onState('idle');onError('录音中断，请重试。');};
   recorder.onstop=async()=>{clearTimeout(timer);stream.getTracks().forEach(t=>t.stop());if(cancelled)return;onState('transcribing');try{const blob=new Blob(chunks,{type:mime});if(blob.size<100)throw new Error('录音为空，请重新录制');if(blob.size>15*1024*1024)throw new Error('录音过大，请缩短到 30 秒内');const data=await api('/api/voice/transcribe',{},'POST',{headers:{'Content-Type':mime,'X-Niuma-Client':'1'},body:blob});if(!cancelled){if(data.text.trim())onResult(data.text);else onError('没有识别到内容，请重试。');}}catch(e){if(!cancelled)onError(e.message);}finally{if(!cancelled){onState('idle');cancelActive=null;}}};
   recorder.start();onState('listening');timer=setTimeout(()=>recorder.stop(),30000);
  }).catch(e=>{stream?.getTracks().forEach(t=>t.stop());if(!cancelled){onState('idle');onError(e.name==='NotAllowedError'?'麦克风权限未开启，请允许访问或改用文字。':e.message);}cancelActive=null;});
  return ()=>{if(recorder?.state==='recording')recorder.stop();};
 }
 onError('当前浏览器不支持语音识别。可用文字输入，或配置云端语音服务并登录。');return ()=>{};
}

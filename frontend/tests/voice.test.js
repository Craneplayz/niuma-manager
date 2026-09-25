import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window={addEventListener(){},dispatchEvent(){}};
class Recognition {
 constructor(){Recognition.current=this;}
 start(){}
 stop(){this.onend?.();}
 abort(){this.onend?.();}
}
window.SpeechRecognition=Recognition;
const {startVoice,cancelVoice}=await import('../src/voice.js');
test('语音结束后回传最终文本，停止监听',()=>{
 const result=[],states=[];
 startVoice({onInterim(){},onResult:t=>result.push(t),onError:()=>assert.fail(),onState:s=>states.push(s)});
 const r=Recognition.current;
 const phrase=[{transcript:'明天晚上八点交作业'}];phrase.isFinal=true;
 r.onresult({results:[phrase]});r.onend();
 assert.deepEqual(result,['明天晚上八点交作业']);assert.equal(states.at(-1),'idle');
});
test('取消录音不提交识别结果',()=>{
 let submitted=false;
 startVoice({onInterim(){},onResult(){submitted=true;},onError:()=>assert.fail(),onState(){}});
 cancelVoice();assert.equal(submitted,false);
});
test('拒绝麦克风权限给出可恢复错误',()=>{
 const errors=[];
 startVoice({onInterim(){},onResult:()=>assert.fail(),onError:e=>errors.push(e),onState(){}});
 Recognition.current.onerror({error:'not-allowed'});Recognition.current.onend();
 assert.equal(errors.length,1);assert.match(errors[0],/权限/);
});

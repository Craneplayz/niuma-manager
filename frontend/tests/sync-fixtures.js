import {readFileSync} from 'node:fs';
const examples=JSON.parse(readFileSync(new URL('../../contracts/examples/source-sync-v01.json',import.meta.url),'utf8'));
export function syncRun({state='succeeded',states=['succeeded'],skipped=0,added=0,id='run:one'}={}){
 const records=states.map((state,i)=>({request_id:'sync:'+i,label:'合成通知 '+i,course:'合成课程',state,added:i===0?added:0,seconds:1,error:state==='failed'?{code:'agent_timeout',message:'模型暂时失败',retryable:true}:state==='incomplete'?{code:'source_incomplete',message:'来源尚未取得',retryable:false}:null}));
 const counts=Object.fromEntries(['queued','running','succeeded','failed','incomplete','cancelled','interrupted'].map(s=>[s,states.filter(x=>x===s).length]));
 return {...structuredClone(examples.run),run_id:id,state,revision:1,skipped,records,counts:{...counts,total:records.length,skipped,added},error:null};
}

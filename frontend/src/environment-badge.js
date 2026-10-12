// Runtime environment comes from the API; the same web image can move from test to production.
const names={development:'开发环境',test:'测试环境',production:'生产环境'};
export function describeEnvironment(version,frontendRevision='unknown'){
 const name=names[version?.environment];
 if(!name)return {label:'环境未确认',detail:'未取得有效服务环境标识',mismatch:false};
 const backend=/^[a-f0-9]{7,40}$/.test(version.revision)?version.revision:'unknown';
 const frontend=/^[a-f0-9]{7,40}$/.test(frontendRevision)?frontendRevision:'unknown';
 const mismatch=backend!=='unknown'&&frontend!=='unknown'&&backend!==frontend;
 return {label:mismatch?'前后端版本不一致':name+(version.managed?'':'（旧配置）'),
  detail:`前端 ${frontend.slice(0,12)} · 后端 ${backend.slice(0,12)}`,mismatch};
}
export async function showEnvironmentBadge(){
 const badge=document.createElement('div');
 badge.id='environment-badge';badge.setAttribute('role','status');
 Object.assign(badge.style,{position:'fixed',bottom:'8px',left:'8px',zIndex:'30',padding:'4px 8px',
  borderRadius:'5px',font:'12px sans-serif',background:'#edf1e7',color:'#344638',maxWidth:'calc(100vw - 32px)'});
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),4000);
 let value=describeEnvironment(null);
 try{
  const response=await fetch('/api/version',{cache:'no-store',signal:controller.signal});
  if(response.ok)value=describeEnvironment(await response.json(),import.meta.env?.VITE_BUILD_REVISION);
 }catch{}finally{clearTimeout(timer);}
 badge.textContent=value.label;badge.title=value.detail;
 if(value.mismatch)badge.style.background='#fff0d3';
 document.body.append(badge);
}

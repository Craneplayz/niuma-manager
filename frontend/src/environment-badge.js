// Runtime environment comes from the API; the same web image can move from test to production.
const names={development:'开发环境',test:'测试环境',production:'生产环境'};
export function describeEnvironment(version,frontendRevision='unknown',frontendDirty='unknown'){
 const name=names[version?.environment];
 if(!name)return {label:'环境未确认',detail:'未取得有效服务环境标识',mismatch:false};
 const backend=/^[a-f0-9]{7,40}$/.test(version.revision)?version.revision:'unknown';
 const frontend=/^[a-f0-9]{7,40}$/.test(frontendRevision)?frontendRevision:'unknown';
 const mismatch=backend!=='unknown'&&frontend!=='unknown'&&backend!==frontend;
 const dirty=frontendDirty==='1'||version.source_dirty===true;
 const state=(value)=>value===true?'（有未提交修改）':value===false?'（干净）':'（修改状态未知）';
 return {label:mismatch?'前后端版本不一致':name+(version.managed?'':'（旧配置）')+(dirty?'（有未提交修改）':''),
  detail:`前端 ${frontend.slice(0,12)}${state(frontendDirty==='1'?true:frontendDirty==='0'?false:null)} · 后端 ${backend.slice(0,12)}${state(version.source_dirty)}；本机版本为启动时快照，修改源码后请重启核对`,mismatch};
}

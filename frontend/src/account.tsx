import { useRef, useState, type ReactNode } from 'react'
export type User = { id: string; name: string; email: string }
type Entry = { value: unknown; version: number }
let workspace: Record<string, Entry> = {}
let activeUserId = ''
export const userDataVersion = (name:string) => workspace[name]?.version??0
export async function api(path: string, method = 'GET', body?: unknown, signal?: AbortSignal) {
  const response = await fetch('/api'+path,{method,credentials:'include',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal})
  let result
  try { result = await response.json() } catch { throw new Error('后端暂时不可用，请确认 API 服务已启动') }
  if (!response.ok) throw new Error(result.error || '请求失败，请重试')
  return result
}
export function AccountGate({children}:{children:(user:User,logout:()=>void)=>ReactNode}) {
  const [user,setUser]=useState<User|null>(null)
  const [mode,setMode]=useState<'login'|'register'>('login')
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const logout=async()=>{try{await api('/auth/logout','POST');workspace={};activeUserId='';setUser(null);setError('');setMode('login')}catch(e){setError((e as Error).message)}}
  if(user)return <>{error&&<p role="alert" className="form-error">{error}</p>}{children(user,logout)}</>
  return <main className="auth-page"><section className="auth-card"><div className="auth-logo">牛</div><h1>{mode==='login'?'登录牛马管理器':'创建牛马管理器账号'}</h1><p className="subtle">先记下任务，再把时间留给生活。</p>
    <form key={mode} onSubmit={async e=>{e.preventDefault();if(busy)return;const data=new FormData(e.currentTarget);setBusy(true);setError('');try{
      if(mode==='register'&&data.get('password')!==data.get('confirm'))throw new Error('两次输入的密码不一致')
      const result=await api('/auth/'+mode,'POST',{name:data.get('name'),email:data.get('email'),password:data.get('password')})
      workspace=await api('/workspace');activeUserId=result.user.id;setUser(result.user)
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}}}>
      {mode==='register'&&<label>昵称<input name="name" required maxLength={40} autoComplete="nickname"/></label>}
      <label>邮箱<input name="email" type="email" required autoComplete="username"/></label>
      <label>密码<input name="password" type="password" required minLength={8} maxLength={128} autoComplete={mode==='login'?'current-password':'new-password'}/></label>
      {mode==='register'&&<label>确认密码<input name="confirm" type="password" required autoComplete="new-password"/></label>}
      {error&&<p role="alert" className="form-error">{error}</p>}<button className="primary-button" disabled={busy}>{busy?'处理中…':mode==='login'?'登录':'注册并登录'}</button>
    </form><button className="text-button" disabled={busy} onClick={()=>{setMode(mode==='login'?'register':'login');setError('')}}>{mode==='login'?'还没有账号？注册':'已有账号？登录'}</button><p className="local-note">账号与任务保存在服务端数据库。浏览器不持久化业务数据，仅使用会话 Cookie。</p>
  </section></main>
}
export function useUserData<T>(userId:string,name:string,fallback:T) {
  const [value,setValue]=useState<T>(()=>(workspace[name]?.value as T)??fallback)
  const [error,setError]=useState('')
  const current=useRef(value),busy=useRef(false)
  const save=async(update:T|((value:T)=>T)):Promise<boolean>=>{
    if(activeUserId!==userId)return false
    if(busy.current){setError('正在保存，请稍候');return false}
    busy.current=true
    try{
      const next=typeof update==='function'?(update as (value:T)=>T)(current.current):update
      const result=await api('/data/'+name,'PUT',{value:next,version:workspace[name]?.version??0})
      if(activeUserId!==userId)return false
      workspace[name]={value:next,version:result.version};current.current=next;setValue(next);setError('');return true
    }catch(e){setError((e as Error).message);return false}finally{busy.current=false}
  }
  const refresh=async()=>{if(activeUserId!==userId)return;const nextWorkspace=await api('/workspace');if(activeUserId!==userId)return;if(nextWorkspace[name])workspace[name]=nextWorkspace[name];else delete workspace[name];const next=(workspace[name]?.value as T)??fallback;current.current=next;setValue(next)}
  return [value,save,error,refresh] as const
}

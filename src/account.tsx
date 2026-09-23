import { useEffect, useRef, useState, type ReactNode } from 'react'

export type User = { id: string; name: string; email: string }
type Account = User & { salt: string; hash: string }
const ACCOUNTS = 'shixu.accounts.v1'
const SESSION = 'shixu.session.v1'
const hex = (bytes: ArrayBuffer) => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('')
const accounts = (): Account[] => JSON.parse(localStorage.getItem(ACCOUNTS) || '[]')
async function passwordHash(password: string, salt: string) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits'])
  return hex(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: new TextEncoder().encode(salt), iterations: 210000 }, key, 256))
}
function sessionUser(): User | null {
  try {
    const session = JSON.parse(sessionStorage.getItem(SESSION) || 'null')
    if (!session || session.expiresAt < Date.now()) return null
    const account = accounts().find(a => a.id === session.userId)
    return account ? { id: account.id, name: account.name, email: account.email } : null
  } catch { return null }
}

export function AccountGate({ children }: { children: (user: User, logout: () => void) => ReactNode }) {
  const [user, setUser] = useState<User | null>(sessionUser)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const logout = () => { sessionStorage.removeItem(SESSION); setUser(null); setError(''); setMode('login') }
  useEffect(() => {
    const timer = window.setInterval(() => { if (user && !sessionUser()) logout() }, 30000)
    return () => clearInterval(timer)
  }, [user])
  if (user) return <>{children(user, logout)}</>
  return <main className="auth-page"><section className="auth-card">
    <div className="auth-logo">时</div><h1>{mode === 'login' ? '欢迎回到时序' : '创建你的时序账号'}</h1>
    <p className="subtle">先记录截止日期，再安排执行时间。</p>
    <form key={mode} onSubmit={async e => {
      e.preventDefault(); if (busy) return
      const form = e.currentTarget
      const data = new FormData(form)
      const email = String(data.get('email')).trim().toLowerCase()
      const password = String(data.get('password'))
      setBusy(true); setError('')
      try {
        let account = accounts().find(a => a.email === email)
        if (mode === 'register') {
          const name = String(data.get('name')).trim()
          if (!name) throw new Error('请填写昵称')
          if (account) throw new Error('该邮箱已注册，请登录')
          if (password.length < 8) throw new Error('密码至少需要 8 个字符')
          if (password !== data.get('confirm')) throw new Error('两次输入的密码不一致')
          const salt = hex(crypto.getRandomValues(new Uint8Array(16)).buffer)
          account = { id: crypto.randomUUID(), name, email, salt, hash: await passwordHash(password, salt) }
          // Re-read after hashing, so another registration cannot silently replace the list.
          const latest = accounts()
          if (latest.some(a => a.email === email)) throw new Error('该邮箱已注册，请登录')
          localStorage.setItem(ACCOUNTS, JSON.stringify([...latest, account]))
        } else if (!account || await passwordHash(password, account.salt) !== account.hash) {
          throw new Error('邮箱或密码不正确')
        }
        sessionStorage.setItem(SESSION, JSON.stringify({ userId: account.id, expiresAt: Date.now() + 86400000 }))
        form.reset()
        setUser({ id: account.id, name: account.name, email: account.email })
      } catch (err) { setError(err instanceof Error ? err.message : '操作失败，请检查浏览器存储权限后重试') }
      finally { setBusy(false) }
    }}>
      {mode === 'register' && <label>昵称<input name="name" required maxLength={40} autoComplete="nickname"/></label>}
      <label>邮箱<input name="email" type="email" required autoComplete="username"/></label>
      <label>密码<input name="password" type="password" required minLength={mode === 'register' ? 8 : undefined} maxLength={128} autoComplete={mode === 'register' ? 'new-password' : 'current-password'}/></label>
      {mode === 'register' && <label>确认密码<input name="confirm" type="password" required autoComplete="new-password"/></label>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary-button" disabled={busy}>{busy ? '处理中…' : mode === 'login' ? '登录' : '注册并登录'}</button>
    </form>
    <button className="text-button" disabled={busy} onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>{mode === 'login' ? '还没有账号？注册' : '已有账号？登录'}</button>
    <p className="local-note">本地体验版：账号与数据保存在当前浏览器，清除网站数据会丢失记录；暂不支持跨设备登录或找回密码。</p>
  </section></main>
}

/** Persist before updating UI: failure must never be shown as a saved change. */
export function useUserData<T>(userId: string, name: string, fallback: T) {
  const key = `shixu.user.${userId}.${name}.v1`
  const [initial] = useState(() => {
    try { const raw=localStorage.getItem(key); return {raw,value:(JSON.parse(raw || 'null') ?? fallback) as T,error:''} }
    catch { return {raw:null,value:fallback,error:'无法读取已保存的数据，请检查浏览器存储；为避免覆盖，当前禁止保存。'} }
  })
  const lastStored = useRef(initial.raw)
  const [error, setError] = useState(initial.error)
  const [value, setValue] = useState<T>(initial.value)
  const save = (update: T | ((current: T) => T)): boolean => {
    if(initial.error) return false
    try {
      if (localStorage.getItem(key) !== lastStored.current) {
        setError('数据已在其他窗口修改。请保留当前输入，刷新获取最新数据后重试。'); return false
      }
      const next = typeof update === 'function' ? (update as (current: T) => T)(value) : update
      const serialized = JSON.stringify(next)
      localStorage.setItem(key, serialized); lastStored.current = serialized; setValue(next); setError(''); return true
    } catch { setError('未能保存：浏览器存储不可用或空间不足，请保留当前输入并重试。'); return false }
  }
  return [value, save, error] as const
}

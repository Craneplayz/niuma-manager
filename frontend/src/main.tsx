import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { AccountGate } from './account'
import './workflow.css'
import './styles.css'
import './responsive.css'
import './productivity.css'
import './reference-theme.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode><AccountGate>{(user,logout)=><App key={user.id} user={user} onLogout={logout}/>}</AccountGate></StrictMode>,
)

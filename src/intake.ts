import type { Task } from './App'

/** Only extract explicit dates locally; ambiguous language is left for review. */
export function extractDue(text: string): Task['due'] {
  const matches = [...text.matchAll(/(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?/g)]
  if (matches.length !== 1) return null
  const [,y,m,d,h,min] = matches[0]
  const date = `${y}-${m.padStart(2,'0')}-${d.padStart(2,'0')}`
  const parsed = new Date(date+'T00:00:00Z')
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0,10)!==date || (h && (Number(h)>23 || Number(min)>59))) return null
  return h ? {date,time:`${h.padStart(2,'0')}:${min}`,precision:'datetime'} : {date,precision:'date'}
}

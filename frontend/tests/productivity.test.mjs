import test from 'node:test'
import assert from 'node:assert/strict'
import {selectTasks,remainingSeconds,formatTimer,initialFocus} from '../.cache/tests/productivity.js'
import {exportCalendar,foldLine} from '../.cache/tests/exports.js'

const task=(id,extra={})=>({id,title:id,description:'',course:'数学',priority:'medium',status:'not_started',due:null,plans:[],subtasks:[],...extra})
test('filters course, today and overdue without losing unknown deadlines or mutating input',()=>{
  const tasks=[task('unknown'),task('today',{due:{date:'2026-09-24',precision:'date'}}),task('overdue',{due:{date:'2026-09-23',precision:'date'}}),task('cancelled',{status:'cancelled'}),task('done',{status:'completed',due:{date:'2026-09-23',precision:'date'}})]
  assert.equal(selectTasks(tasks,{query:' 数学 '}).length,4)
  assert.deepEqual(selectTasks(tasks,{window:'today',today:'2026-09-24'}).map(t=>t.id),['today'])
  assert.deepEqual(selectTasks(tasks,{window:'overdue',today:'2026-09-24'}).map(t=>t.id),['overdue'])
  assert.equal(tasks[0].id,'unknown')
  assert.equal(selectTasks([task('low',{priority:'low'}),task('high',{priority:'high'})],{sort:'priority'})[0].id,'high')
})
test('focus derives time from deadline and supports pause and sleep',()=>{
  assert.equal(remainingSeconds(initialFocus,5000),1500)
  const running={...initialFocus,running:true,endsAt:6000}
  assert.equal(remainingSeconds(running,4001),2)
  assert.equal(remainingSeconds(running,99999),0)
  assert.equal(formatTimer(3000),'50:00')
})
test('calendar keeps all-day deadlines, converts precise time and includes plans but not cancelled tasks',()=>{
  const out=exportCalendar([task('day',{due:{date:'2026-09-24',precision:'date'}}),task('timed',{title:'报告;\n检查',due:{date:'2026-09-24',precision:'datetime',time:'20:00'},plans:[{id:'p',title:'准备',startAt:'2026-09-24T10:00:00+08:00',endAt:'2026-09-24T11:00:00+08:00'}]}),task('cancelled',{status:'cancelled',due:{date:'2026-09-24',precision:'date'}})],'2026-09-24T00:00:00Z')
  assert.match(out,/DTSTART;VALUE=DATE:20260924\r\n/)
  assert.match(out,/DTSTART:20260924T120000Z/)
  assert.match(out,/DTEND:20260924T030000Z/)
  assert.match(out,/报告\\;\\n检查/)
  assert.equal((out.match(/BEGIN:VEVENT/g)||[]).length,3)
  assert.ok(!out.includes('cancelled'))
})
test('calendar folding preserves Chinese text and bounds UTF-8 octets',()=>{
  const input='SUMMARY:'+'课程笔记'.repeat(30),folded=foldLine(input)
  assert.equal(folded.replace(/\r\n /g,''),input)
  assert.ok(folded.split('\r\n').every(line=>Buffer.byteLength(line)<=75))
})

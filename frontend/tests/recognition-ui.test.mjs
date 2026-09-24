import test from 'node:test'
import assert from 'node:assert/strict'
import { deadlineLabel,suggestedDue,safeSourceUrl } from '../.cache/tests/recognition.js'
const deadline={value:'2026-09-30',precision:'date',timezone:'Asia/Shanghai',kind:'deadline',status:'explicit',raw:'9月30日',basis:'原文',alternatives:[]}
test('only explicit deadlines prefill; no deadline, conflict and reference remain unset',()=>{
  assert.deepEqual(suggestedDue(deadline),{date:'2026-09-30',precision:'date'})
  for(const status of ['inferred','unknown','conflict','not_provided'])assert.equal(suggestedDue({...deadline,status}),null)
  assert.equal(suggestedDue({...deadline,kind:'reference'}),null)
  assert.match(deadlineLabel({...deadline,status:'not_provided'}),/可保留无 DDL/)
  assert.match(deadlineLabel({...deadline,kind:'reference',status:'inferred'}),/不是真实 DDL/)
})
test('datetime is converted by instant; date-only is never shifted',()=>{
  assert.deepEqual(suggestedDue({...deadline,precision:'datetime',value:'2026-09-30T23:00:00-04:00',timezone:'America/New_York'}),{date:'2026-10-01',time:'11:00',precision:'datetime'})
  assert.equal(suggestedDue({...deadline,precision:'datetime',value:'2026-09-30T18:00:30+08:00'}),null)
})
test('source links reject script URLs and embedded credentials',()=>{
  for(const url of ['javascript:alert(1)','data:text/html,test','https://user:password@example.com',null])assert.equal(safeSourceUrl(url),null)
  assert.equal(safeSourceUrl('https://example.com/notice'),'https://example.com/notice')
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { extractDue } from '../.cache/tests/intake.js'
test('intake keeps date-only and unknown deadlines separate',()=>{
 assert.deepEqual(extractDue('2026年10月3日提交'),{date:'2026-10-03',precision:'date'})
 assert.deepEqual(extractDue('2026-10-03 18:00 提交'),{date:'2026-10-03',time:'18:00',precision:'datetime'})
 for(const input of ['下周五报告','2026-02-30','2026-10-01 或 2026-10-02','2026-10-03 25:00']) assert.equal(extractDue(input),null)
})

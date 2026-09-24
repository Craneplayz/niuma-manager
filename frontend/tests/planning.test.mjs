import test from 'node:test'
import assert from 'node:assert/strict'
import { extractDateHint as extractDue, localTextPreview } from '../.cache/tests/intake.js'
test('intake keeps date-only and unknown deadlines separate',()=>{
 assert.deepEqual(extractDue('2026年10月3日提交'),{date:'2026-10-03',precision:'date'})
 assert.deepEqual(extractDue('2026-10-03 18:00 提交'),{date:'2026-10-03',time:'18:00',precision:'datetime'})
 for(const input of ['下周五报告','2026-02-30','2026-10-01 或 2026-10-02','2026-10-03 25:00']) assert.equal(extractDue(input),null)
})
test('date clues never automatically become real deadlines',()=>{
 for(const text of ['2026-10-03 18:00 开会，请提前加群','发布日期：2026-10-03','2026-10-03 前提交报告','报告延期至 2026-10-03']){
  const preview=localTextPreview(text)
  assert.equal(preview.due,null)
  assert.ok(preview.dateHint)
  assert.equal(preview.description,text)
 }
})
test('uncertain and absent dates preserve the original input',()=>{
 for(const text of ['阅读论文','明天提交报告','9 月 30 日提交','2026-10-03 或 2026-10-04']){
  assert.deepEqual(localTextPreview(text),{description:text,due:null,dateHint:null})
 }
})

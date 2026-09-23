import test from 'node:test'
import assert from 'node:assert/strict'
import { localPlan, validateResult, validateAvailability, eligible } from '../.cache/tests/planning.js'
import { extractDue } from '../.cache/tests/intake.js'

const makeRequest = () => ({requestId:'test',schemaVersion:'1.0',requestedAt:'2026-09-21T00:00:00+08:00',timezone:'Asia/Shanghai',availability:{startDate:'2026-09-21',endDate:'2026-09-22',startTime:'19:00',endTime:'22:00',weekdays:[1,2],focusMinutes:60,dailyMinutes:120},tasks:[{id:'task',title:'报告',description:'',due:{date:'2026-09-22',precision:'date'},priority:'high',estimatedMinutes:180}],existingPlans:[{id:'existing',taskId:'other',title:'已有安排',startAt:'2026-09-21T19:00:00+08:00',endAt:'2026-09-21T20:00:00+08:00',isLocked:true,origin:'manual'}]})
test('planning avoids existing locked blocks and does not mutate tasks or deadlines',()=>{
 const request=makeRequest(),before=JSON.stringify(request)
 const result=validateResult(request,localPlan(request))
 assert.equal(result.blocks.length,3)
 assert.equal(result.blocks[0].startAt,'2026-09-21T20:00:00+08:00')
 assert.equal(JSON.stringify(request),before)
})
test('impossible work produces a warning and never moves DDL',()=>{
 const request=makeRequest();request.tasks[0].estimatedMinutes=1000
 const result=validateResult(request,localPlan(request))
 assert.ok(result.warnings.length)
 assert.equal(result.blocks.length,4)
})
test('model response conflicts and fabricated task IDs are rejected',()=>{
 const request=makeRequest(),result=localPlan(request)
 result.blocks[0].startAt='2026-09-21T19:00:00+08:00';result.blocks[0].endAt='2026-09-21T20:00:00+08:00'
 assert.throws(()=>validateResult(request,result),/冲突/)
 result.blocks[0].taskId='unknown';assert.throws(()=>validateResult(request,result),/未知任务/)
})
test('model cannot schedule after precise DDL',()=>{
 const request=makeRequest(),result=localPlan(request)
 request.tasks[0].due={date:'2026-09-21',time:'20:30',precision:'datetime'}
 assert.throws(()=>validateResult(request,result),/DDL/)
})
test('invalid availability is rejected',()=>{
 const a=makeRequest().availability
 assert.throws(()=>validateAvailability({...a,endTime:'18:00'}))
 assert.throws(()=>validateAvailability({...a,weekdays:[]}))
})
test('intake keeps date-only and unknown deadlines separate',()=>{
 assert.deepEqual(extractDue('2026年10月3日提交'),{date:'2026-10-03',precision:'date'})
 assert.deepEqual(extractDue('2026-10-03 18:00 提交'),{date:'2026-10-03',time:'18:00',precision:'datetime'})
 for(const input of ['下周五报告','2026-02-30','2026-10-01 或 2026-10-02','2026-10-03 25:00']) assert.equal(extractDue(input),null)
})
test('only active tasks without plans are eligible',()=>{
 assert.deepEqual(eligible([{id:'a',status:'not_started',plans:[]},{id:'b',status:'completed',plans:[]},{id:'c',status:'in_progress',plans:[{}]}]).map(t=>t.id),['a'])
})

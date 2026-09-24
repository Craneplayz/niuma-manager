import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Fixture checks supplement the server runtime validator.
test('contract example has consistent IDs, evidence and partial status',()=>{
  const {request,expected}=JSON.parse(readFileSync(new URL('../docs/examples/text-contract-v0.2.json',import.meta.url),'utf8'))
  assert.equal(request.contract_version,expected.contract_version)
  assert.equal(request.request_id,expected.request_id)
  const records=new Map(request.records.map(r=>[r.input_id,r]))
  assert.equal(records.size,request.records.length)
  assert.equal(new Set(expected.tasks.map(t=>t.candidate_id)).size,expected.tasks.length)
  for(const result of expected.record_results){
    assert.ok(records.has(result.input_id))
    assert.deepEqual(result.task_ids,expected.tasks.filter(t=>t.input_id===result.input_id).map(t=>t.candidate_id))
    if(result.status==='failed')assert.ok(expected.errors.some(e=>e.input_id===result.input_id))
  }
  for(const task of expected.tasks){
    assert.ok(task.sources.length)
    assert.ok(task.evidence.some(e=>e.supports.includes('title')))
    for(const evidence of task.evidence){
      assert.equal(evidence.field,'text')
      assert.ok(evidence.quote.length)
      assert.ok(records.get(evidence.input_id).text.includes(evidence.quote))
    }
    if(task.deadline.status==='explicit')assert.ok(task.evidence.some(e=>e.supports.includes('deadline')))
    if(task.deadline.status==='not_provided'){
      assert.equal(task.deadline.value,null)
      assert.equal(task.deadline.precision,'unknown')
      assert.equal(task.needs_review,false)
    }
  }
  assert.equal(expected.status,'partial_success')
  assert.ok(expected.record_results.some(r=>r.status==='failed'))
  assert.ok(expected.record_results.some(r=>r.status==='success'))
})

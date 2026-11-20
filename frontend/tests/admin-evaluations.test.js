import test from 'node:test';
import assert from 'node:assert/strict';
import {summarizeRows} from '../src/admin-evaluations.js';
test('object-level precision recall and F1 retain proper denominators',()=>{
 const r=summarizeRows([{executed:true,applicable:true,status:'ok',tp:3,fp:1,fn:2,seconds:1,total_tokens:10,estimated_yuan:.1}]);
 assert.equal(r.precision,.75);assert.equal(r.recall,.6);assert.equal(r.f1,2/3);assert.equal(r.cost,.1);
});
test('unknown token and cost measurements stay visible',()=>{
 const r=summarizeRows([{executed:true,applicable:true,status:'error',seconds:20}]);
 assert.equal(r.cost,null);assert.equal(r.costUnknown,1);assert.equal(r.tokenUnknown,1);assert.equal(r.p95,20);
});
test('incomplete models do not claim complete coverage',()=>{
 const r=summarizeRows([{executed:true,applicable:true,status:'ok'},{executed:false,applicable:true,status:'pending'}]);
 assert.equal(r.complete,false);assert.equal(r.planned,2);assert.equal(r.n,1);
});
test('empty metrics are unknown',()=>{const r=summarizeRows([]);assert.equal(r.contract,null);assert.equal(r.f1,null);assert.equal(r.p95,null)});
import {metricRows,csvRows} from '../src/admin-evaluations.js';
test('metric intervals group correlated repeats under source family',()=>{
 const values=metricRows([{executed:true,family_id:'same',checks:{date_0:true}},{executed:true,family_id:'same',checks:{date_0:false}}]);
 assert.equal(values[0].value,.5);assert.equal(values[0].independent,1);assert.equal(values[0].n,2);
});
test('CSV export escapes spreadsheet formulas and quotes',()=>{
 const csv=csvRows([{id:'=HYPERLINK("x")',family:'test'}]);
 assert.ok(csv.includes("'=HYPERLINK"));assert.ok(csv.includes('""x""'));
});

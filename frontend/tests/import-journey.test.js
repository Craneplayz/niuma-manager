import test from 'node:test';
import assert from 'node:assert/strict';
import {ImportJourney,groupImportBatches,boundarySnapTarget} from '../src/import-journey.js';
test('current import does not wait on historical pending candidates',()=>{
 const journey=new ImportJourney();journey.begin(['current'],[{id:'existing'}],'通知');
 const batches=[{request:{request_id:'old'},items:[{state:'pending'}]},{request:{request_id:'current'},items:[{state:'applied'},{state:'ignored'}]}];
 assert.deepEqual(journey.snapshot(batches),{pending:0,ignored:1,added:[]});assert.equal(journey.seen,2);
});
test('highlight only newly created tasks associated with this import',()=>{
 const journey=new ImportJourney();journey.begin(['current'],[{id:'existing'}]);
 const tasks=[{id:'existing',recognitionHistory:[{requestId:'current'}]},{id:'new',recognitionHistory:[{requestId:'current'}]},{id:'other',recognitionHistory:[{requestId:'old'}]}];
 assert.deepEqual(journey.snapshot([],tasks).added,['new']);
});
test('partial failure stays in failed review instead of reporting completion',()=>{
 const journey=new ImportJourney();journey.begin(['current']);journey.finish(true);assert.equal(journey.phase,'failed');assert.equal(journey.active,true);journey.begin(['current']);assert.equal(journey.phase,'importing');journey.finish(false);assert.equal(journey.phase,'review');
});
test('account reset forgets the active import and new task association',()=>{
 const journey=new ImportJourney();journey.begin(['current']);journey.snapshot([{request:{request_id:'current'},items:[{state:'pending'}]}]);journey.reset();assert.equal(journey.phase,'idle');assert.equal(journey.seen,0);assert.equal(journey.requestIds.size,0);
});

test('one teaching sync groups its independent requests and preserves source and time',()=>{
 const input=[{importId:'sync-1',importedAt:'2026-10-09T13:00:00Z',importSource:'教学网',request:{request_id:'one',records:[]},items:[]},{importId:'sync-1',importedAt:'2026-10-09T13:00:00Z',importSource:'教学网',request:{request_id:'two',records:[]},items:[]},{importId:'manual-1',importedAt:'2026-10-08T13:00:00Z',importSource:'手动通知',request:{request_id:'three',records:[]},items:[]}];
 const groups=groupImportBatches(input);assert.equal(groups.length,2);assert.equal(groups[0].batches.length,2);assert.equal(groups[0].source,'教学网');assert.equal(groups[0].createdAt,'2026-10-09T13:00:00Z');
});

test('review contents and footer scroll freely even near the page boundary',()=>{
 for(const y of [720,721,800,1200,2500])assert.equal(boundarySnapTarget(y,y-30,720,800),null);
});
test('only the import and review junction settles in the user scroll direction',()=>{
 assert.equal(boundarySnapTarget(640,600,720,800),'#import-review');
 assert.equal(boundarySnapTarget(640,760,720,800),'.story-header');
 assert.equal(boundarySnapTarget(300,250,720,800),'#import-review');
 assert.equal(boundarySnapTarget(300,350,720,800),'.story-header');
 assert.equal(boundarySnapTarget(300,300,720,800),'.story-header');
 assert.equal(boundarySnapTarget(500,500,720,800),'#import-review');
});

test('a tall import form remains readable in a short viewport',()=>{
 assert.equal(boundarySnapTarget(200,150,910,650),null);
 assert.equal(boundarySnapTarget(400,350,910,650),'#import-review');
});

test('new candidates append in first-seen order even when the API returns newest batches first',()=>{
 const journey=new ImportJourney();journey.begin(['one','two']);
 const a={id:'a',state:'pending'},b={id:'b',state:'pending'};
 const one={request:{request_id:'one'},items:[a]},two={request:{request_id:'two'},items:[b]};
 assert.deepEqual(journey.items([one]).map(item=>item.id),['a']);
 assert.deepEqual(journey.items([two,one]).map(item=>item.id),['a','b']);
 a.state='applied';assert.deepEqual(journey.items([two,one]).filter(item=>item.state==='pending').map(item=>item.id),['b']);
 a.state='pending';assert.deepEqual(journey.items([two,one]).map(item=>item.id),['a','b']);
 journey.reset();journey.begin(['one','two']);assert.deepEqual(journey.items([two,one]).map(item=>item.id),['b','a']);
});

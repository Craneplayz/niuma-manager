import test from 'node:test';
import assert from 'node:assert/strict';
import {describeEnvironment} from '../src/environment-badge.js';

test('the same frontend build identifies its current API environment',()=>{
 for(const environment of ['test','production']){
  const value=describeEnvironment({environment,managed:true,revision:'a'.repeat(40)},'a'.repeat(40));
  assert.equal(value.mismatch,false);
  assert.equal(value.label,environment==='test'?'测试环境':'生产环境');
 }
});
test('a stale frontend is distinguished from unavailable version metadata',()=>{
 assert.equal(describeEnvironment({environment:'production',managed:true,revision:'b'.repeat(40)},'a'.repeat(40)).mismatch,true);
 assert.equal(describeEnvironment({environment:'test',managed:true,revision:'unknown'},'a'.repeat(40)).mismatch,false);
 assert.equal(describeEnvironment(null).label,'环境未确认');
 assert.match(describeEnvironment({environment:'development',managed:false,revision:'a'.repeat(40)}).label,/旧配置/);
});

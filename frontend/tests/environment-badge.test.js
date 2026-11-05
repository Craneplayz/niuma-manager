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

test('local startup identities preserve revisions and mark uncommitted source',()=>{
 const version={environment:'development',managed:true,revision:'a'.repeat(40),source_dirty:true};
 const value=describeEnvironment(version,'a'.repeat(40),'1');
 assert.equal(value.mismatch,false);
 assert.match(value.label,/有未提交修改/);
 assert.match(value.detail,/前端 aaaaaaaaaaaa（有未提交修改）/);
 assert.match(value.detail,/后端 aaaaaaaaaaaa（有未提交修改）/);
 const clean=describeEnvironment({...version,source_dirty:false},'a'.repeat(40),'0');
 assert.equal(clean.label,'开发环境');
 assert.match(clean.detail,/（干净）/);
 assert.match(describeEnvironment({...version,source_dirty:null}).detail,/修改状态未知/);
});

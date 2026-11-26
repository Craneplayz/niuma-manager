import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window=new EventTarget();
const {buildMaterialRequest}=await import('../src/quick-import-data.js');
const receipt={contract_version:'manual-materials/0.1',file_name:'合成通知.png',file_ref:'asset:one',mime_type:'image/png',size_bytes:3,sha256:'a'.repeat(64)};
test('unsupported file metadata remains an explicit unsupported record',()=>{
 const r=buildMaterialRequest([{...receipt,mime_type:'application/zip'}],'request:one','2026-10-14T00:00:00Z');
 assert.equal(r.records[0].attachments[0].status,'unsupported');assert.equal(r.records[0].attachments[0].file_ref,null);
});

test('pasted notices carry the unique workspace course across the request boundary', async () => {
  const { buildTextImport } = await import('../src/quick-import-data.js');
  const dsa = '数据结构与算法 (A)(26-27学年第1学期)';
  const algebra = '线性代数(26-27学年第1学期)';
  const courses = [dsa, algebra, '高等数学基础(26-27学年第1学期)', '计算机系统导论'];
  const course = (text, available = courses) =>
    buildTextImport(text, '', 'quick-add:test', '2026-11-23T16:00:00Z', available).records[0].course_or_project;
  assert.equal(course('数算mooc平台第10-11章作业已发布。'), dsa);
  assert.equal(course('数据结构与算法的作业已发布。'), dsa);
  assert.equal(course('线代周五交报告。'), algebra);
  assert.equal(course('高数第七次作业。'), courses[2]);
  assert.equal(course('复习 basics'), null);
  assert.equal(course('线代和数算分别交作业。'), null);
  const sections = [dsa, '数据结构与算法 (B)(26-27学年第1学期)'];
  assert.equal(course('数算作业', sections), null);
  assert.equal(course('数据结构与算法（A）作业', sections), dsa);
  assert.equal(course('写数算作业', [algebra]), null);
});

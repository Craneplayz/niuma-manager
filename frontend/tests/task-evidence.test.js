import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceLink,evidenceMarkup} from '../src/task-evidence.js';
test('source links accept only safe web URLs',()=>{
 for(const url of ['javascript:alert(1)','data:text/html,test','https://secret:password@example.com'])assert.equal(sourceLink(url),'');
 assert.match(sourceLink('https://course.pku.edu.cn/notice/1'),/noopener noreferrer/);
});
test('evidence shows original text and teaching source without duplicate excerpts',()=>{
 const result=evidenceMarkup([{quotes:[{quote:'<img>'}],original:'原文 <img>',basis:'周五',sourceUrl:'https://course.pku.edu.cn/notice/1',platform:'teaching_web'}]);
 assert.match(result,/打开教学网原页面/);assert.doesNotMatch(result,/<blockquote>|截止日期依据/);assert.match(result,/原文 &lt;img&gt;/);assert.doesNotMatch(result,/<script>/);
});
test('tasks without evidence explain the empty state',()=>assert.match(evidenceMarkup([]),/没有保存识别证据/));


test('attachment-only WeChat tasks retain every saved evidence quote',async()=>{
 const {readFile}=await import('node:fs/promises');
 const request=JSON.parse(await readFile(new URL('../../contracts/source-to-text/text-attachments-v031-request.json',import.meta.url)));
 const result=JSON.parse(await readFile(new URL('../../contracts/text-to-app/text-attachments-v031-output.json',import.meta.url)));
 const record=request.records[0],candidate=result.tasks[0];
 assert.equal(record.text,'');assert.equal(candidate.evidence.length,2);
 const html=evidenceMarkup([{original:record.text,quotes:candidate.evidence,sources:candidate.sources,sourceLabel:record.source_label,platform:record.platform}]);
 for(const evidence of candidate.evidence)assert.ok(html.includes(evidence.quote));
 assert.match(html,/附件 asset-001/);assert.match(html,/第 1 页/);assert.match(html,/模型报告（model_reported），未经逐字核验/);assert.doesNotMatch(html,/查看完整通知原文/);
});

test('attachment evidence supplements message text, deduplicates and escapes quotes',()=>{
 const html=evidenceMarkup([{original:'群消息：请看附件',quotes:[{quote:'请看附件'},{quote:'附件 <img> 内容'},{quote:'附件 <img> 内容'}]}]);
 assert.match(html,/查看完整通知原文/);assert.match(html,/附件 &lt;img&gt; 内容/);
 assert.equal((html.match(/<blockquote>/g)||[]).length,1);assert.doesNotMatch(html,/<img>/);
});


test('identical attachment quotes retain distinct locations even when present in message text',()=>{
 const quote='提交报告',evidence={quote,field:'attachment_file',asset_id:'<asset>',verification:'model_reported'};
 const first={...evidence,locator:{page:1,paragraph:2,bbox:[0,0,1,1]}};
 const second={...evidence,locator:{page:2,paragraph:null,bbox:null}};
 const html=evidenceMarkup([{original:quote,quotes:[first,structuredClone(first),second]}]);
 assert.equal((html.match(/<blockquote>/g)||[]).length,2);
 assert.match(html,/第 1 页 · 第 2 段 · 区域 \[0, 0, 1, 1\]/);assert.match(html,/第 2 页/);
 assert.match(html,/附件 &lt;asset&gt;/);assert.doesNotMatch(html,/<asset>/);
});

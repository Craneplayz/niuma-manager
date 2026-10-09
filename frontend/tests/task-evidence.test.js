import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceLink,evidenceMarkup} from '../src/task-evidence.js';
test('source links accept only safe web URLs',()=>{
 for(const url of ['javascript:alert(1)','data:text/html,test','https://secret:password@example.com'])assert.equal(sourceLink(url),'');
 assert.match(sourceLink('https://course.pku.edu.cn/notice/1'),/noopener noreferrer/);
});
test('evidence shows original text and teaching source without duplicate excerpts',()=>{
 const result=evidenceMarkup([{quotes:[{quote:'<script>quote</script>'}],original:'原文 <img>',basis:'周五',sourceUrl:'https://course.pku.edu.cn/notice/1',platform:'teaching_web'}]);
 assert.match(result,/打开教学网原页面/);assert.doesNotMatch(result,/<blockquote>|截止日期依据/);assert.match(result,/原文 &lt;img&gt;/);assert.doesNotMatch(result,/<script>/);
});
test('manual and older tasks explain missing evidence honestly',()=>assert.match(evidenceMarkup([]),/没有保存识别证据/));

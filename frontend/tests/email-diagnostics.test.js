import test from 'node:test';
import assert from 'node:assert/strict';
import {renderEmailDiagnostics} from '../src/email-diagnostics-view.js';
test('email diagnostics render safe reason aggregates and explain timing scope',()=>{
 const html=renderEmailDiagnostics([{stage:'validation',reason:'evidence',count:6,avg_duration_ms:0.456}]);
 assert.match(html,/引用未能在原文找到/);assert.match(html,/>6<\/td>/);assert.match(html,/0.5 ms/);assert.match(html,/不包含模型生成/);
});
test('diagnostics reject untrusted category strings and nonnumeric aggregate values',()=>{
 const html=renderEmailDiagnostics([{stage:'validation',reason:'<img src=x>',count:1,avg_duration_ms:1},{stage:'validation',reason:'schema',count:'<script>',avg_duration_ms:Infinity},{stage:'private',reason:'evidence',count:1}]);
 assert.doesNotMatch(html,/<img|<script>|Infinity/);assert.match(html,/任务结构不符合约定/);assert.doesNotMatch(html,/引用未能在原文找到/);
 assert.match(renderEmailDiagnostics(),/暂无/);
});

test('token monitoring separates coverage from zero and explains reasoning overlap',async()=>{
 const {renderEmailTokens}=await import('../src/email-diagnostics-view.js');
 const html=renderEmailTokens({attempts:3,failed:1,truncated:1,tokens:{prompt_tokens:{total:1200,reportedAttempts:2},completion_tokens:{total:0,reportedAttempts:1},reasoning_tokens:{total:null,reportedAttempts:0}}});
 assert.match(html,/1,200/);assert.match(html,/600/);assert.match(html,/>0<\/td>/);assert.match(html,/>—<\/td>/);assert.match(html,/0 \/ 3/);assert.match(html,/推理属于输出/);
 assert.match(renderEmailTokens(),/暂无 Token/);
 const unsafe=renderEmailTokens({attempts:'<img>',tokens:{total_tokens:{total:'<script>',reportedAttempts:1}}});
 assert.doesNotMatch(unsafe,/<img|<script>/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {storyShell} from '../src/story-layout.js';
import {readFileSync} from 'node:fs';

test('deployment explicitly separates platform restrictions from user-submitted text',async()=>{
 const source=readFileSync(new URL('../src/story-layout.js',import.meta.url),'utf8')
  .replace("import {icon} from './icons.js';","const icon=()=>'';")
  .replace("import {wechatEnabled} from './source-import.js';","const wechatEnabled=false;");
 const deployed=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
 for(const page of ['tasks','agent','settings']){
  const html=deployed.storyShell({page,user:null,saveLabel:'未登录',saveAlert:'',content:''});
  assert.match(html,/腾讯\/微信适用协议/);assert.match(html,/不提供任何获取微信内容/);
  assert.match(html,/不连接微信客户端/);assert.match(html,/自行.*导出/);
  assert.match(html,/点击识别/);assert.match(html,/服务端和模型处理/);
  assert.match(html,/JSON 或附件/);assert.match(html,/不提供解密/);
 }
});

const shell=page=>storyShell({page,user:null,saveLabel:'未登录',saveAlert:'',content:'',focus:''});

test('primary business routes stay directly reachable in their chapter or workspace tools',()=>{
 const pages=['agent','tasks','decompose','book','plan','calendar','settings'];
 const navigation=['agent','tasks','calendar'].map(shell).join('');
 for(const page of pages)assert.ok(navigation.includes(`href="#${page}"`),`${page} has a direct entry`);
 assert.match(shell('decompose'),/href="#decompose" class="selected" aria-current="page"/);
 assert.match(shell('calendar'),/href="#calendar" class="selected" aria-current="page"/);
 assert.match(shell('settings'),/data-experience="search"/);
 assert.match(shell('settings'),/data-experience="focus"/);
 assert.match(shell('settings'),/data-action="calendar-export"/);
 assert.match(shell('settings'),/data-action="export"/);
});

test('workspace chrome escapes user content in labels and attributes',()=>{
 const malicious='"><img src=x onerror=alert(1)>';
 const chrome=storyShell({page:'tasks',user:{display_name:malicious},saveLabel:malicious,saveAlert:'',content:'',focus:''});
 assert.doesNotMatch(chrome,/<img/);
 assert.match(chrome,/&lt;img/);
});

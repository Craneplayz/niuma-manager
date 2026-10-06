import test from 'node:test';
import assert from 'node:assert/strict';
import {storyShell} from '../src/story-layout.js';

const shell=page=>storyShell({page,user:null,saveLabel:'未登录',saveAlert:'',content:'',focus:''});

test('each business route stays directly reachable in its chapter or workspace tools',()=>{
 const pages=['agent','tasks','decompose','book','trash','plan','calendar','settings'];
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

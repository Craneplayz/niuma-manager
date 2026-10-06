import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('account management forms expose explicit submit buttons to their save handlers',async()=>{
 const source=await readFile(new URL('../src/app.js',import.meta.url),'utf8');
 const profile=source.match(/<form id="account-profile-form">[\s\S]*?<\/form>/)?.[0]||'';
 const password=source.match(/<form id="account-password-form">[\s\S]*?<\/form>/)?.[0]||'';
 assert.match(profile,/<button type="submit" class="btn primary">保存昵称<\/button>/);
 assert.match(password,/<button type="submit" class="btn primary">更新密码<\/button>/);
});

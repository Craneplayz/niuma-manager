import test from 'node:test';
import assert from 'node:assert/strict';
import {renderTraffic} from '../src/traffic-view.js';
test('traffic cards and daily counts render without exposing identifiers',()=>{
 const html=renderTraffic({guests:5,registrations:2,returnVisits:3,registeredAccounts:8,
  daily:[{day:'2026-10-19',guests:5,registrations:2,returningVisitors:3}]});
 for(const text of ['游客数','新增注册','回访次数','2026-10-19','账号 8 个'])assert.ok(html.includes(text));
 assert.doesNotMatch(html,/cookie|token|email/);
});
test('old servers and invalid display values do not fabricate counts or inject markup',()=>{
 assert.equal(renderTraffic(null),'');
 const html=renderTraffic({guests:'<script>',registrations:-1,returningVisitors:null,
  daily:[{day:'<img onerror=alert(1)>',guests:99}]});
 assert.doesNotMatch(html,/<script|<img|onerror|>99</);assert.match(html,/>0</);
});

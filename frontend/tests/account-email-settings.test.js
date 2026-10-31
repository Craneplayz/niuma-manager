import test from 'node:test';
import assert from 'node:assert/strict';
import { setMaxListeners } from 'node:events';

globalThis.window = new EventTarget();
setMaxListeners(0, globalThis.window);
globalThis.CustomEvent ||= class extends Event { constructor(type, options) { super(type); this.detail = options?.detail; } };

const store = await import('../src/team-store.js');
const { createAccountUI } = await import('../src/account-ui.js');

function fixture() {
  let account = 'A', dialogOpen = false, dialog = '', renders = 0, loads = 0;
  const focuses = [], opens = [], calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ path, method, body, account });
    if (path === '/api/auth/login') { account = body.email.split('@')[0]; return { ok: true, json: async () => ({}) }; }
    if (path === '/api/auth/me') return { ok: true, json: async () => ({ user: { id: account, name: account, email: `${account}@test.local` } }) };
    if (path === '/api/workspace') return { ok: true, json: async () => ({ tasks: { version: 0, value: [] } }) };
    throw new Error(`unexpected request ${path}`);
  };
  const host = {
    escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])),
    icon: () => '',
    openDialog(html) { dialog = html; dialogOpen = true; opens.push(html); },
    closeDialog() { dialogOpen = false; dialog = ''; },
    isDialogOpen(selector) { return dialogOpen && selector === '[data-account-settings-panel]' && dialog.includes('data-account-settings-panel'); },
    focusAccountSection(section) { focuses.push(section); },
    renderEmailSettings() { return '<section class="account-connection" data-account-section="pku-email" tabindex="-1"><div><h3>北大邮箱</h3><p class="status-note">管理用于获取邮件通知的账号</p></div><button data-import-action="email-settings">管理连接</button></section>'; },
    loadEmailSettings() { loads++; return Promise.resolve(fixture.load?.()); },
    feedbackBox: () => '',
    render() { renders++; },
    setState() {},
    toast() {},
  };
  const accountUI = createAccountUI(host);
  return {
    host, accountUI, focuses, opens, calls,
    get loads() { return loads; }, get renders() { return renders; },
    get dialog() { return dialog; }, get dialogOpen() { return dialogOpen; },
    async login(who = 'A') { await store.authenticate('login', { email: `${who}@test.local`, password: 'test-password' }); },
  };
}

test('normal account settings embed and load the reusable PKU email settings section', async t => {
  const f = fixture(); t.after(() => f.accountUI.click({ dataset: { accountAction: 'close' } }));
  await f.login();
  await f.accountUI.click({ dataset: { accountAction: 'open' } });
  assert.match(f.dialog, /data-account-settings-panel/);
  assert.match(f.dialog, /北大邮箱/);
  assert.equal(f.loads, 1);
  assert.deepEqual(f.focuses, []);
});

test('targeted account open shows loading immediately and focuses the PKU email section after load', async t => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  fixture.load = () => pending;
  const f = fixture(); t.after(() => { fixture.load = null; void f.accountUI.click({ dataset: { accountAction: 'close' } }); });
  await f.login();
  const opening = f.accountUI.open('pku-email');
  assert.equal(f.loads, 1, 'loading starts before the dialog is rendered');
  assert.match(f.dialog, /data-account-settings-panel/);
  assert.deepEqual(f.focuses, ['pku-email']);
  release(); await opening;
  assert.equal(f.opens.length, 2);
  assert.deepEqual(f.focuses, ['pku-email', 'pku-email']);
});

test('closing the panel while email settings load prevents a late reopen', async t => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  fixture.load = () => pending;
  const f = fixture(); t.after(() => { fixture.load = null; });
  await f.login();
  const opening = f.accountUI.open('pku-email');
  f.host.closeDialog();
  release(); await opening;
  assert.equal(f.dialogOpen, false);
  assert.equal(f.opens.length, 1);
});

test('choosing another account mode during loading keeps that mode open', async t => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  fixture.load = () => pending;
  const f = fixture(); t.after(() => { fixture.load = null; });
  await f.login();
  const opening = f.accountUI.open('pku-email');
  await f.accountUI.click({ dataset: { accountAction: 'profile' } });
  assert.match(f.dialog, /data-account-form="profile"/);
  release(); await opening;
  assert.match(f.dialog, /data-account-form="profile"/);
  assert.equal(f.opens.length, 2);
});

test('account changes invalidate a pending targeted open', async t => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  fixture.load = () => pending;
  const f = fixture(); t.after(() => { fixture.load = null; });
  await f.login('A');
  const opening = f.accountUI.open('pku-email');
  await f.login('B');
  release(); await opening;
  assert.equal(f.opens.length, 1);
  assert.equal(f.dialogOpen, true);
});

test('refresh rerenders and focuses only while the account summary owns the open dialog', async t => {
  const f = fixture(); t.after(() => f.accountUI.click({ dataset: { accountAction: 'close' } }));
  await f.login(); await f.accountUI.open();
  const before = f.opens.length;
  assert.equal(f.accountUI.refresh('pku-email'), true);
  assert.equal(f.opens.length, before + 1);
  assert.deepEqual(f.focuses, ['pku-email']);
  f.host.closeDialog();
  assert.equal(f.accountUI.refresh('pku-email'), false);
  assert.equal(f.opens.length, before + 1);
});

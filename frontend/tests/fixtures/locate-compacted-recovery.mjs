// Invoked by server/test_source_jobs.py against its synthetic HTTP/SQLite server.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSourceSyncUI } from '../../src/source-sync-ui.js';

const config = JSON.parse(readFileSync(0, 'utf8'));
for (const cached of [true, false]) {
  const revealed = [], messages = [], reads = [];
  const ui = createSourceSyncUI({
    accountVersion: () => 1, isAuthenticated: () => true, isDemo: () => false,
    isImportBusy: () => false, getState: () => ({ tasks: [], batches: [] }),
    render() {}, async onChanged() {},
    toast: message => messages.push(message), revealImportResult: id => revealed.push(id),
    async api(path, body, method = 'GET') {
      assert.equal(method, 'GET', 'locating recovery must never start another import');
      reads.push(path);
      const response = await fetch(new URL(path, config.base), { headers: { Cookie: config.cookie } });
      assert.equal(response.status, 200);
      const value = await response.json();
      if (!cached && path === '/api/sources/sync-runs')
        value.runs = value.runs.filter(run => run.run_id !== config.target_run_id);
      return value;
    },
  });
  await ui.load();
  // The history projection may hide older duplicate failures; use the original
  // HTTP receipt to find the recovered source's index in its visible batch.
  const response = await fetch(new URL('/api/sources/sync-runs', config.base), { headers: { Cookie: config.cookie } });
  const { runs } = await response.json();
  const old = runs.find(run => run.run_id === config.old_run_id);
  assert.ok(old, 'the recovered source remains available after compaction and restart');
  const index = old.records.filter(record => record.recovered || ['failed', 'incomplete', 'cancelled', 'interrupted'].includes(record.state))
    .findIndex(record => record.request_id === config.request_id);
  assert.ok(index >= 0);
  await ui.click({ dataset: { sourceSyncAction: 'locate-recovery', sourceSyncRun: old.run_id, sourceSyncItem: String(index) } });
  assert.deepEqual(messages, []);
  assert.deepEqual(revealed, [`sync-run-${config.target_run_id}`]);
  const html = ui.renderHistory();
  const row = html.match(new RegExp(`<article id="sync-run-${config.target_run_id}"[\\s\\S]*?</article>`))?.[0];
  assert.ok(row);
  assert.match(row, /sync-run-heading" data-source-sync-highlight/);
  assert.match(row, /这条来源已处理完成，没有新增任务或待核对内容。/);
  assert.match(row, /部分完成/);
  assert.match(row, /错误 1 条/);
  if (cached) assert.match(row, /处理未完成 · 1/);
  if (!cached) assert.ok(reads.includes(`/api/sources/sync-runs/${config.target_run_id}`));
  ui.reset();
}

import { describeUserError, userErrorMarkup } from './user-errors.js';

export const TEXT_JOB_LABELS = {
  queued: '等待识别', recognizing: '正在识别', success: '识别完成', no_tasks: '未识别出任务',
  partial_success: '部分完成', failed: '识别失败', stopped: '已停止', interrupted: '识别已中断',
};
const ACTIVE = new Set(['queued', 'recognizing']);
const RETRY = new Set(['failed', 'partial_success', 'stopped', 'interrupted']);
const BASE = '/api/recognition/text-jobs';

export function checkTextJobs(value) {
  if (value?.contract_version !== 'text-import-jobs/0.1' || !Array.isArray(value.jobs) || value.jobs.length > 100 ||
      new Set(value.jobs.map(job => job?.request_id)).size !== value.jobs.length || value.jobs.some(job =>
        !job || typeof job.request_id !== 'string' || !job.request_id || job.request_id.length > 500 ||
        !Object.hasOwn(TEXT_JOB_LABELS, job.state) || !Number.isInteger(job.attempt) || job.attempt < 1 ||
        !Number.isFinite(Date.parse(job.created_at)) || !Number.isFinite(Date.parse(job.updated_at)) ||
        job.error !== null && (typeof job.error?.code !== 'string' || typeof job.error?.message !== 'string' || typeof job.error?.retryable !== 'boolean'))) {
    throw new Error('导入服务版本不匹配，请刷新或更新服务。');
  }
  return value.jobs;
}

/** Only job identities/status live here; private input stays in the server account. */
export function createTextImportJobs(host) {
  let jobs = [], loaded = false, loading = false, writing = false, visible = false;
  let generation = 0, timer = null, controller = null, error = null, pendingResults = false;
  const setTimer = host.setTimer || globalThis.setTimeout;
  const clearTimer = host.clearTimer || globalThis.clearTimeout;
  const esc = host.escapeHtml;
  const current = () => ({ generation, account: host.accountVersion() });
  const valid = stamp => stamp.generation === generation && stamp.account === host.accountVersion();
  const allowed = () => host.isAuthenticated() && !host.isDemo();
  const active = () => jobs.some(job => ACTIVE.has(job.state));
  function poll() {
    clearTimer(timer); timer = null;
    if (!visible || !allowed()) return;
    timer = setTimer(() => { timer = null; void load(); }, active() || error || pendingResults ? 2000 : 30000);
    timer?.unref?.();
  }
  function reset() {
    generation++; controller?.abort(); controller = null; clearTimer(timer); timer = null;
    jobs = []; loaded = loading = writing = false; error = null; pendingResults = false;
  }
  function accept(value) {
    const next = checkTextJobs(value), changed = JSON.stringify(next) !== JSON.stringify(jobs);
    jobs = next; loaded = true;
    return changed;
  }
  async function refreshResults(stamp) {
    if (!pendingResults) return;
    const refreshed = await host.onChanged();
    if (!valid(stamp)) return;
    if (refreshed === true) pendingResults = false;
    else if (refreshed === false) throw new Error('导入结果暂时无法读取，请重试读取结果。');
  }
  async function load() {
    if (!allowed() || loading || writing) return;
    const stamp = current(); loading = true;
    const requestController = new AbortController(); controller = requestController;
    try {
      const next = await host.api(BASE, undefined, 'GET', { signal: requestController.signal });
      if (!valid(stamp)) return;
      const changed = accept(next), hadError = Boolean(error), wasPending = pendingResults; error = null;
      if (changed) pendingResults = true;
      await refreshResults(stamp);
      if (valid(stamp) && (changed || hadError || wasPending !== pendingResults)) host.render();
    } catch (reason) {
      if (valid(stamp)) { error = describeUserError(reason, 'text_import'); host.render(); }
    } finally {
      if (valid(stamp)) { loading = false; controller = null; poll(); }
    }
  }
  async function mutate(path, body) {
    if (!allowed() || writing) return false;
    const stamp = current(); writing = true; error = null;
    controller?.abort();
    // A stale read must not overwrite a just-accepted stop/retry response.
    generation++; stamp.generation = generation; loading = false;
    const requestController = new AbortController(); controller = requestController;
    host.render();
    try {
      const next = await host.api(path, body, 'POST', { signal: requestController.signal });
      if (!valid(stamp)) return false;
      accept(next);
      // A write can return an already-finished job. Its results still need an
      // acknowledged refresh even when every following GET has the same state.
      pendingResults = true;
      return true;
    } catch (reason) {
      if (valid(stamp)) error = describeUserError(reason, 'text_import');
      throw reason;
    } finally {
      if (valid(stamp)) { writing = false; controller = null; host.render(); poll(); }
    }
  }
  function setVisible(next) {
    const entering = next && !visible; visible = next;
    if (!visible) { clearTimer(timer); timer = null; return; }
    if ((entering || !loaded) && !loading && !writing) void load();
    else if (!loading && !writing) poll();
  }
  function controls(job) {
    const action = ACTIVE.has(job.state) ? 'stop' : RETRY.has(job.state) ? 'retry' : null;
    return action ? `<button type="button" class="btn quiet" data-text-job-action="${action}" data-text-job-id="${esc(job.request_id)}" data-text-job-attempt="${job.attempt}" ${writing ? 'disabled' : ''}>${action === 'stop' ? '停止导入' : '重试识别'}</button>` : '';
  }
  function renderProgress() {
    const job = jobs.find(job => ACTIVE.has(job.state));
    return `${job ? `<div class="import-progress" role="status"><span class="status-note">${TEXT_JOB_LABELS[job.state]}通知… 刷新或离开页面后仍会继续。</span>${controls(job)}</div>` : pendingResults && !error ? '<p class="status-note" role="status">正在更新导入结果…</p>' : ''}${error ? `${userErrorMarkup(error, 'text_import')}<button type="button" class="btn quiet" data-text-job-action="refresh">重新读取导入状态</button>` : ''}`;
  }
  async function click(button) {
    const { textJobAction: action, textJobId: id, textJobAttempt: attempt } = button.dataset;
    if (action === 'refresh') return load();
    if (!['stop', 'retry'].includes(action) || !jobs.some(job => job.request_id === id)) return;
    let stamp;
    try {
      if (!await mutate(`${BASE}/${encodeURIComponent(id)}/${action}`, { attempt: Number(attempt) })) return;
      stamp = current();
      await refreshResults(stamp);
    } catch (reason) {
      if (stamp && valid(stamp)) error = describeUserError(reason, 'text_import');
    } finally {
      if (stamp && valid(stamp)) { host.render(); poll(); }
    }
  }
  return { load, reset, setVisible, renderProgress, controls, click,
    list: () => jobs, get: id => jobs.find(job => job.request_id === id),
    isActive: active, isWriting: () => writing,
    start: body => mutate(BASE, body),
    retry: job => mutate(`${BASE}/${encodeURIComponent(job.request_id)}/retry`, { attempt: job.attempt }),
  };
}

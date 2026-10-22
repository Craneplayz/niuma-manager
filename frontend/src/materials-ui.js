import { describeUserError } from "./user-errors.js";
import { api, session, accountVersion, flushStore } from "./team-store.js";
import { newId } from "./id.js";

const root = "/api/materials/";
const active = job => ["queued", "running"].includes(job.status);
const labels = { queued: "排队中", running: "读取并识别中", succeeded: "已完成", partial_failed: "部分失败", failed: "识别失败" };
const known = new Set(["image/png", "image/jpeg", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);
const unknownDate = () => ({ value: null, precision: "unknown", basis: "unknown" });
export function buildMaterialRequest(receipts, id = "material:" + newId(), now = new Date().toISOString()) {
  return { contract_version: "0.3.1", request_id: id, records: receipts.map((r, index) => {
    const supported = known.has(r.mime_type);
    return { input_id: id + ":" + index, text: "", text_quality: "missing", platform: "wechat",
      published_at: unknownDate(), collected_at: now, timezone: "Asia/Shanghai", timezone_basis: "caller_default",
      source_url: null, source_label: "用户主动上传的文件", source_item_id: null, course_or_project: null,
      context: [], upstream_hints: null, attachments: [{ asset_id: "file:" + index,
        ...Object.fromEntries(["file_name", "mime_type", "size_bytes", "sha256"].map(k => [k, r[k]])),
        file_ref: supported ? r.file_ref : null, origin: "unknown", original_published_at: unknownDate(),
        status: supported ? "available" : "unsupported", content_mode: "file", errors: supported ? [] : [
          { stage: "upload", code: "unsupported_type", message: "当前读取器不支持此格式", retryable: false }] }] };
  }) };
}

export function createMaterialsUI(host) {
  const esc = host.escapeHtml;
  let files = [], consent = false, limits = null, jobs = [], loading = false, busy = false, error = "", notice = "";
  let generation = 0, timer = null, controller = null, draft = null, retryIds = new Map(), observed = "", loadingPromise = null, historyOpen = false;
  const stamp = () => [generation, accountVersion()];
  const valid = value => value[0] === generation && value[1] === accountVersion() && session.user && !host.isDemo();
  const visible = () => host.isVisible?.() !== false;
  const fingerprint = () => JSON.stringify(jobs.map(j => [j.job_id, j.status, j.items]));
  function reset() {
    generation++; controller?.abort(); clearTimeout(timer); timer = controller = null;
    files = []; consent = false; limits = null; jobs = []; loading = busy = false;
    error = notice = observed = ""; draft = null; retryIds.clear(); loadingPromise = null; historyOpen = false;
  }
  function poll() {
    clearTimeout(timer); timer = null;
    if (!busy && visible() && (jobs.some(active) || jobs.length && fingerprint() !== observed)) { timer = setTimeout(load, 2000); timer.unref?.(); }
  }
  function load() {
    // Source switches must not start a list read before the write is accepted.
    if (busy) return loadingPromise || Promise.resolve();
    return readJobs();
  }
  function readJobs() {
    if (loadingPromise) return loadingPromise;
    const pending = loadOnce(); loadingPromise = pending;
    pending.finally(() => { if (loadingPromise === pending) loadingPromise = null; });
    return pending;
  }
  async function loadOnce() {
    if (!session.user || host.isDemo() || loading) return;
    const value = stamp(); loading = true;
    try {
      const [settings, list] = await Promise.all([api(root + "limits"), api(root + "imports")]);
      if (!valid(value)) return;
      if (!Array.isArray(list.jobs)) throw new Error("文件处理状态暂时无法读取");
      limits = settings.limits; jobs = list.jobs; error = "";
      const snapshot = fingerprint();
      if (snapshot !== observed) {
        const refreshed = await host.changed();
        if (!valid(value)) return;
        if (refreshed === false) throw new Error("文件结果已保存，但列表刷新失败，请刷新文件状态重试。");
        if (refreshed !== null) observed = snapshot;
      }
    } catch (cause) { if (valid(value)) error = cause.status === 403 ? "文件上传暂未开放，请稍后再试。" : cause.message; }
    finally { if (valid(value)) { loading = false; host.render(); poll(); } }
  }
  function input(event) {
    if (event.target.id === "material-files") {
      if (!busy) { files = [...event.target.files]; consent = false; draft = null; host.render(); }
      return true;
    }
    if (event.target.id === "material-consent") { consent = event.target.checked; return true; }
    return false;
  }
  async function click(button) {
    const action = button.dataset.materialAction;
    if (!action) return false;
    if (busy) return true;
    if (action === "remove") {
      files = files.filter((_, index) => index !== Number(button.dataset.index));
      consent = false; draft = null; host.render(); return true;
    }
    if (action === "refresh") { await load(); return true; }
    if (busy || !session.user || host.isDemo()) return true;
    const value = stamp(); busy = true; error = notice = ""; controller = new AbortController(); host.render();
    clearTimeout(timer); timer = null;
    try {
      if (loadingPromise) await loadingPromise;
      await flushStore();
      if (!valid(value)) return true;
      if (action === "upload") {
        if (!consent || !files.length) throw new Error("请选择文件，并同意上传和识别");
        if (!limits) throw new Error("文件上传设置尚未就绪，请稍后重试");
        if (files.length > 20 || files.some(f => !f.size || f.size > limits.asset_bytes)) throw new Error("最多选择 20 个非空文件，请检查单文件大小限制");
        draft ??= { id: "material:" + newId(), receipts: [], request: null };
        for (let i = draft.receipts.length; i < files.length; i++) {
          const form = new FormData(); form.append("file", files[i]);
          const receipt = await api(root + "assets", form, "POST", { formData: true, signal: controller.signal, timeoutMs: 45000 });
          if (!valid(value)) return true;
          if (receipt.contract_version !== "manual-materials/0.1") throw new Error("文件服务版本不匹配");
          draft.receipts.push(receipt);
        }
        draft.request ??= buildMaterialRequest(draft.receipts, draft.id);
        await api(root + "imports", draft.request, "POST", { signal: controller.signal });
        if (!valid(value)) return true;
        draft = null; files = []; consent = false; notice = "文件已提交，识别结果会显示在下方。";
      } else if (action === "retry") {
        const id = button.dataset.job;
        if (!jobs.some(j => j.job_id === id && !active(j))) return true;
        if (!retryIds.has(id)) retryIds.set(id, "retry-" + newId());
        await api(root + "imports/" + encodeURIComponent(id) + "/retry", { retry_id: retryIds.get(id) });
        if (!valid(value)) return true;
        retryIds.delete(id); notice = "已重试失败部分，成功结果保留。";
      }
      await readJobs();
    } catch (cause) { if (valid(value)) error = cause.message; }
    finally { if (valid(value)) { busy = false; controller = null; host.render(); poll(); } }
    return true;
  }
  const errorLabel = item => item.failure ? (() => {
    const report = describeUserError(item);
    return report.message + report.details;
  })() : ({
    agent_unavailable: "文件识别暂不可用，请稍后重试。",
    agent_timeout: "文件识别超时，请稍后重试。",
    invalid_agent_output: "暂时无法读取识别结果，请重试。",
    file_ref_unavailable: "文件已失效，请重新上传。",
    reader_unsupported: "暂不支持此文件格式，请使用 PNG、JPG、PDF 或 DOCX。",
    low_content_quality: "文件内容不够清晰，请换一份清晰的文件。",
    limit_exceeded: "文件超过处理限制，请缩小或拆分后再上传。",
  }[item.code] || item.message);
  const sizeLabel = size => size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;
  const attachmentLabels = { complete: "已读取", succeeded: "已读取", read: "已读取", available: "可读取", partial: "部分读取", partial_read: "部分读取", failed: "读取失败", unsupported: "暂不支持", skipped: "未读取", missing: "文件缺失", unread: "未读取" };
  function jobMarkup(job) {
    const errors = [...(job.result?.errors || []), ...(job.last_attempt_errors || [])];
    const exhausted = job.retry_remaining === 0 && !active(job) && errors.some(item => item.retryable);
    const retry = !active(job) && job.retry_remaining !== 0 && errors.some(item => item.retryable);
    const names = Object.values(job.sources || {}).flatMap(source => Object.values(source.assets || {}));
    const assets = (job.result?.record_results || []).flatMap(record => (record.attachments || []).map(asset => `<li>${esc(job.sources?.[record.input_id]?.assets?.[asset.asset_id] || "附件")} · ${esc(attachmentLabels[asset.status] || "请查看识别结果")}${asset.coverage?.unread_locations?.length ? `<span class="status-note">${esc(asset.coverage.unread_locations.join("；"))}</span>` : ""}</li>`));
    return `<article class="material-job"><div class="material-job-heading"><h3>${esc(names.join("、") || "文件导入")}</h3><span class="status-note" role="status">${esc(labels[job.status] || "处理中")}</span></div>
      ${errors.map(item => `<p class="error">${esc(errorLabel(item))}</p>`).join("")}
      ${assets.length ? `<details class="source-disclosure"><summary>文件读取情况</summary><ul>${assets.join("")}</ul></details>` : ""}
      ${job.result?.status === "no_tasks" ? '<p class="status-note">未发现需要处理的任务。</p>' : ""}
      ${exhausted ? '<p class="status-note">本次导入的重试次数已用完，请重新上传失败的文件；成功结果已保留。</p>' : ""}
      ${retry ? `<button class="btn quiet" data-material-action="retry" data-job="${esc(job.job_id)}" ${busy ? "disabled" : ""}>重试失败部分</button>` : ""}
    </article>`;
  }
  function render() {
    if (session.user && !host.isDemo() && !limits && !loading && !error) queueMicrotask(load);
    const pending = jobs.filter(job => active(job) || ["failed", "partial_failed"].includes(job.status));
    const finished = jobs.filter(job => !pending.includes(job));
    return `<section class="materials-panel"><h2 class="source-title">图片与文件</h2><p class="source-intro">从图片或文档中识别任务。</p>
      <label class="material-picker" for="material-files"><input id="material-files" type="file" multiple accept=".png,.jpg,.jpeg,.pdf,.docx" ${busy ? "disabled" : ""}><strong>${files.length ? "重新选择文件" : "选择文件"}</strong><span>PNG、JPG、PDF、DOCX${limits?.asset_bytes ? ` · 每份最多 ${limits.asset_bytes / 1048576} MB` : ""}</span></label>
      ${files.length ? `<ul class="material-selection">${files.map((file, index) => `<li><div><strong>${esc(file.name)}</strong><span class="status-note">${sizeLabel(file.size)}</span></div><button class="icon-button" type="button" data-material-action="remove" data-index="${index}" aria-label="移除 ${esc(file.name)}" ${busy ? "disabled" : ""}>${host.icon?.("close") || "×"}</button></li>`).join("")}</ul>
      <label class="source-consent"><input id="material-consent" type="checkbox" ${consent ? "checked" : ""} ${busy ? "disabled" : ""}>同意上传所选文件，并用于任务识别。</label>` : ""}
      <div class="import-form-actions"><button class="btn primary" data-material-action="upload" ${busy || !files.length || !limits || !session.user || host.isDemo() ? "disabled" : ""}>${busy ? "正在提交…" : "识别任务"}</button>${jobs.length || error ? `<button class="btn quiet" data-material-action="refresh" ${loading || busy ? "disabled" : ""}>刷新状态</button>` : ""}</div>
      ${error ? `<p class="error" role="alert">${esc(error)}</p>` : ""}${notice ? `<p class="status-note" role="status">${esc(notice)}</p>` : ""}
      ${pending.length ? `<div class="material-jobs">${pending.map(jobMarkup).join("")}</div>` : ""}
      ${finished.length ? `<details class="import-history material-history" data-material-history ${historyOpen ? "open" : ""}><summary>最近导入 · ${finished.length}</summary>${finished.map(jobMarkup).join("")}</details>` : ""}
    </section>`;
  }
  function toggle(event) {
    if (event.target?.dataset?.materialHistory === undefined || event.target.isConnected === false) return false;
    historyOpen = event.target.open; return true;
  }
  return { render, input, click, load, reset, toggle, owns: id => jobs.some(job => job.request_id === id) };
}

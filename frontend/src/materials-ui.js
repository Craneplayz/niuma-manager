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
  let generation = 0, timer = null, controller = null, draft = null, retryIds = new Map(), observed = "", loadingPromise = null;
  const stamp = () => [generation, accountVersion()];
  const valid = value => value[0] === generation && value[1] === accountVersion() && session.user && !host.isDemo();
  const visible = () => host.isVisible?.() !== false;
  const fingerprint = () => JSON.stringify(jobs.map(j => [j.job_id, j.status, j.items]));
  function reset() {
    generation++; controller?.abort(); clearTimeout(timer); timer = controller = null;
    files = []; consent = false; limits = null; jobs = []; loading = busy = false;
    error = notice = observed = ""; draft = null; retryIds.clear(); loadingPromise = null;
  }
  function poll() {
    clearTimeout(timer); timer = null;
    if (!busy && visible() && (jobs.some(active) || jobs.length && fingerprint() !== observed)) { timer = setTimeout(load, 2000); timer.unref?.(); }
  }
  function load() {
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
    } catch (cause) { if (valid(value)) error = cause.status === 403 ? "当前环境未开放文件上传，请联系管理员。" : cause.message; }
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
    if (action === "refresh") { await load(); return true; }
    if (busy || !session.user || host.isDemo()) return true;
    const value = stamp(); busy = true; error = notice = ""; controller = new AbortController(); host.render();
    clearTimeout(timer); timer = null;
    try {
      if (loadingPromise) await loadingPromise;
      await flushStore();
      if (!valid(value)) return true;
      if (action === "upload") {
        if (!consent || !files.length) throw new Error("请选择文件并同意发送给识别服务");
        if (!limits) throw new Error("请先读取当前环境的上传设置");
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
        draft = null; files = []; consent = false; notice = "已提交，关闭页面后仍会处理。结果在下方待核对列表。";
      } else if (action === "retry") {
        const id = button.dataset.job;
        if (!jobs.some(j => j.job_id === id && !active(j))) return true;
        if (!retryIds.has(id)) retryIds.set(id, "retry-" + newId());
        await api(root + "imports/" + encodeURIComponent(id) + "/retry", { retry_id: retryIds.get(id) });
        if (!valid(value)) return true;
        retryIds.delete(id); notice = "已重试失败部分，成功结果保留。";
      }
      await load();
    } catch (cause) { if (valid(value)) error = cause.message; }
    finally { if (valid(value)) { busy = false; controller = null; host.render(); poll(); } }
    return true;
  }
  function render() {
    if (session.user && !host.isDemo() && !limits && !loading && !error) queueMicrotask(load);
    return `<section class="materials-panel"><h2>图片与文件</h2><p class="status-note">支持 PNG、JPEG、PDF、DOCX。所选文件会上传并发送给识别服务；只选择你有权提供的材料。当前入口不连接微信。</p><label class="field">选择文件<input id="material-files" type="file" multiple ${busy ? "disabled" : ""}></label>${files.length ? `<p>${files.map(f => esc(f.name)).join("、")}</p>` : ""}<label class="sync-consent"><input id="material-consent" type="checkbox" ${consent ? "checked" : ""} ${busy ? "disabled" : ""}>我同意上传并识别所选文件。</label><div class="import-form-actions"><button class="btn primary" data-material-action="upload" ${busy || !limits || !session.user || host.isDemo() ? "disabled" : ""}>${busy ? "正在提交…" : "上传并识别"}</button><button class="btn quiet" data-material-action="refresh" ${loading || busy ? "disabled" : ""}>刷新文件状态</button></div>${limits ? `<p class="status-note">单文件最多 ${limits.asset_bytes / 1048576} MiB，账号最多 ${limits.account_bytes / 1048576} MiB。</p>` : ""}${error ? `<p class="error" role="alert">${esc(error)}</p>` : ""}${notice ? `<p role="status">${esc(notice)}</p>` : ""}${jobs.map(j => {
      const errors = [...(j.result?.errors || []), ...(j.last_attempt_errors || [])];
      const exhausted = j.retry_remaining === 0 && !active(j) && errors.some(e => e.retryable);
      const retry = !active(j) && j.retry_remaining !== 0 && errors.some(e => e.retryable);
      const assets = (j.result?.record_results || []).flatMap(r => (r.attachments || []).map(a => `<li>${esc(j.sources?.[r.input_id]?.assets?.[a.asset_id] || "附件")}：${esc(a.status)}${a.coverage?.unread_locations?.length ? " · " + esc(a.coverage.unread_locations.join("；")) : ""}</li>`));
      return `<article class="candidate-row"><div><h3>${esc(Object.values(j.sources || {}).flatMap(s => Object.values(s.assets || {})).join("、") || "文件导入")} · ${esc(labels[j.status] || j.status)}</h3>${errors.map(e => `<p class="error">${esc(e.message)}</p>`).join("")}${assets.length ? `<details><summary>附件读取情况</summary><ul>${assets.join("")}</ul></details>` : ""}${j.result?.status === "no_tasks" ? '<p>本次未发现任务，请结合读取情况确认是否有缺失内容。</p>' : ""}${exhausted ? '<p class="status-note">本次导入的重试次数已用完；请检查失败原因，仅重新上传失败的文件，成功结果已保留。</p>' : ""}${retry ? `<button class="btn" data-material-action="retry" data-job="${esc(j.job_id)}" ${busy ? "disabled" : ""}>重试失败部分</button>` : ""}</div></article>`;
    }).join("")}</section>`;
  }
  return { render, input, click, load, reset, owns: id => jobs.some(j => j.request_id === id) };
}

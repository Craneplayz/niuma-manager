// The server commits the resulting tasks even if the page closes. Keep only job
// identifiers here so this tab can resume displaying progress after a refresh.
import { api, session } from "./team-store.js";
import { newId } from "./id.js";

export function createQuickAddJobs({ render, refreshTasks, escapeHtml, toast }) {
  let owner, jobs = [], timer, checking = false;
  function persist() {
    try { sessionStorage.setItem("quick-add:" + owner, JSON.stringify(jobs)); } catch {}
  }
  function schedule(delay = 1200) {
    clearTimeout(timer);
    if (!checking && jobs.some(j => !j.message)) timer = setTimeout(check, delay);
  }
  function syncAccount() {
    const next = session.user?.id;
    if (next === owner) return;
    clearTimeout(timer);
    owner = next; jobs = []; checking = false;
    if (!owner) return;
    try { jobs = JSON.parse(sessionStorage.getItem("quick-add:" + owner) || "[]"); } catch {}
    schedule(0);
  }
  function track(job) {
    syncAccount();
    if (!jobs.some(j => j.id === job.id)) jobs.push(job);
    persist(); schedule(0);
  }
  async function check() {
    const account = owner;
    checking = true;
    let textJobs;
    for (const job of [...jobs]) {
      if (account !== session.user?.id || !jobs.includes(job) || job.message) continue;
      try {
        const isFile = job.kind === "files";
        const response = isFile ? await api("/api/materials/imports/" + encodeURIComponent(job.id))
          : await (textJobs ??= api("/api/recognition/text-jobs"));
        if (account !== session.user?.id || !jobs.includes(job)) return;
        const receipt = isFile ? response : response.jobs.find(j => j.request_id === job.id);
        if (!receipt) throw new Error("暂时找不到这次识别记录");
        const status = isFile ? receipt.status : receipt.state;
        if (["running","queued","recognizing"].includes(status)) continue;
        const count = await refreshTasks(job.requestId);
        if (account !== session.user?.id || !jobs.includes(job)) return;
        if (["success","succeeded","no_tasks"].includes(status)) {
          jobs = jobs.filter(j => j !== job);
          toast(count ? "已加入 " + count + " 条" : "未识别出可加入的任务");
        } else {
          job.attempt = receipt.attempt;
          job.failed = true;
          job.message = (count ? "已加入 " + count + " 条，其余内容" : "") +
            (receipt.error?.message || receipt.result?.errors?.[0]?.message || "识别未完成");
        }
      } catch (error) {
        if (account !== session.user?.id || !jobs.includes(job)) return;
        job.message = error.message || "暂时无法读取加入结果";
      }
    }
    if (account === owner) { checking = false; persist(); render(); schedule(); }
  }
  async function action(button) {
    const job = jobs.find(j => j.id === button.dataset.quickJob);
    if (!job || !job.message) return;
    if (button.dataset.jobAction === "dismiss") jobs = jobs.filter(j => j !== job);
    else {
      const account = owner;
      const failed = job.failed;
      delete job.message;
      render();
      try {
        if (failed) {
          if (job.kind === "files") {
            job.retryId ||= newId();
            persist();
            await api("/api/materials/imports/" + encodeURIComponent(job.id) + "/retry", { retry_id: job.retryId });
            delete job.retryId;
          } else await api("/api/recognition/text-jobs/" + encodeURIComponent(job.id) + "/retry", { attempt: job.attempt });
        }
        delete job.failed;
      } catch (e) { job.message = e.message || "请稍后重试"; }
      if (account !== owner) return;
    }
    persist(); render(); schedule(0);
  }
  function markup() {
    syncAccount();
    return jobs.map(job => {
      const id = escapeHtml(job.id);
      return '<p class="status-note" role="status">' + (job.message ? escapeHtml(job.message) +
        ' <button class="btn quiet" data-quick-job="' + id + '" data-job-action="retry">重试</button>' +
        ' <button class="btn quiet" data-quick-job="' + id + '" data-job-action="dismiss">关闭</button>'
        : "正在识别，完成后将自动加入清单…") + '</p>';
    }).join("");
  }
  return { track, markup, action, syncAccount };
}

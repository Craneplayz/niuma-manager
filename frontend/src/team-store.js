// Maps the task UI to the account workspace API.
import { todayKey as dayKey, shiftDay, assertCurrentTaskEdit } from "./task-model.js";
import { appendOperation, readOperations, removeOperations, clearJournal } from './task-journal.js';
export const session = {
  user: null,
  sync: "saved",
  error: "",
  failure: null,
  conflict: false,
  changing: false,
};
const empty = () => ({ tasks: [], trash: [] });
let canonicalTasks = [],
  taskVersion = 0,
  queue = Promise.resolve(),
  latest = null,
  failed = false,
  pendingChanges = [];
const clone = (value) => structuredClone(value);
let accountEpoch = 0, editRevision = 0, retrying = null;
let staging = Promise.resolve(), journalLoaded = null, order = 0, taskGeneration = 0;
const requests = new Set();
export const accountVersion = () => accountEpoch;
function resetAccount() {
  accountEpoch++;
  editRevision++;
  for (const controller of requests) controller.abort();
  requests.clear();
  session.user = null;
  canonicalTasks = [];
  taskVersion = 0;
  taskGeneration = 0;
  latest = null;
  failed = false;
  pendingChanges = [];
  journalLoaded = null;
  retrying = null;
  session.conflict = false;
  window.dispatchEvent(new CustomEvent("niuma:account-reset"));
}
function report(sync, error = "") {
  session.sync = sync;
  session.error = typeof error === "string" ? error : error.message;
  session.failure = error || null;
  window.dispatchEvent(new CustomEvent("niuma:sync"));
}
export async function api(path, body, method = "POST", options = {}) {
  const { timeoutMs = 60000 } = options;
  const epoch = accountEpoch,
    controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), timeoutMs);
  const abort = () => controller.abort();
  requests.add(controller);
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : method,
      credentials: "same-origin",
      headers: body === undefined || options.formData ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : options.formData ? body : JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await response.json();
    if (epoch !== accountEpoch) throw new Error("账号已切换，请重新操作");
    if (!response.ok) {
      const error = new Error(
        data.error && typeof data.error === "object"
          ? data.error.message
          : data.error || data.message || "请求失败",
      );
      error.status = response.status;
      error.code = typeof data.error === "object" ? data.error?.code : data.code;
      error.failure = typeof data.error === "object" ? data.error?.failure : data.failure;
      error.diagnostic = typeof data.error === "object" ? data.error?.diagnostic : data.diagnostic;
      error.diagnostic_id = typeof data.error === "object" ? data.error?.diagnostic_id : data.diagnostic_id;
      error.diagnostics = typeof data.error === "object" ? data.error?.diagnostics : data.diagnostics;
      if (Number.isFinite(data.retryAfter)) error.retryAfter = data.retryAfter;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请重试");
    if (error instanceof TypeError) throw new Error("无法连接服务，请检查网络后重试");
    throw error;
  } finally {
    clearTimeout(timer);
    requests.delete(controller);
    options.signal?.removeEventListener("abort", abort);
  }
}
export function readStore() {
  return empty();
}
// Cancelled tasks stay out of active lists but still explain import-history results.
export function readCancelledTasks() {
  return clone(canonicalTasks.filter(task => task.status === "cancelled" && !task.recycleBin));
}
const status = {
  completed: "done",
  in_progress: "doing",
  not_started: "todo",
};
const canonicalStatus = {
  done: "completed",
  doing: "in_progress",
  todo: "not_started",
};
function toView(task) {
  return {
    ...task,
    content_type: task.content_type || "task",
    source: task.source === "微信" && task.recognitionHistory?.some(
      entry => entry.action === "create" && entry.requestId?.startsWith("material:"),
    ) ? "图片与文件" : task.source,
    due: task.due?.date || "",
    dueTime: task.due?.time || "",
    course: task.course || "",
    notes: task.description || "",
    status: status[task.status] || "todo",
  };
}
function toCanonical(task, previous = {}) {
  return {
    ...previous,
    id: task.id,
    title: task.title,
    description: task.notes || "",
    course: task.course || null,
    content_type: task.content_type || previous.content_type || "task",
    planningExcluded: task.content_type === "notice" || (previous.content_type === "notice" && task.content_type === "task" ? false : Boolean(task.planningExcluded)),
    status: canonicalStatus[task.status] || "not_started",
    completedAt:
      task.status === "done"
        ? task.completedAt ||
          (previous.status === "completed" ? previous.completedAt || "" : dayKey())
        : "",
    due: task.due
      ? {
          date: task.due,
          precision: task.dueTime ? "datetime" : "date",
          ...(task.dueTime ? { time: task.dueTime } : {}),
        }
      : null,
    source: previous.source || task.source || "手动添加",
    createdAt: previous.createdAt || new Date().toISOString(),
    // The task API requires these arrays; existing metadata passes through untouched.
    plans: previous.plans || [],
    subtasks: previous.subtasks || [],
  };
}
function view() {
  return viewOf(canonicalTasks);
}
function viewOf(tasks) {
  return clone({
    tasks: tasks
      .filter((task) => task.status !== "cancelled" && !task.recycleBin)
      .map(toView),
    trash: tasks
      .filter((task) => task.recycleBin)
      .map((task) => toView({ ...task, status: task.recycleBin.status })),
  });
}
async function loadSession(discardPending = false) {
  let epoch = accountEpoch;
  try {
    while (true) {
      await drainWrites();
      if (epoch !== accountEpoch) throw new Error("账号已切换");
      if (!discardPending && failed) throw new Error(session.error || "还有未保存的数据");
      let revision = editRevision;
      const result = await api("/api/auth/me");
      if (epoch !== accountEpoch) throw new Error("账号已切换");
      if (session.user && session.user.id !== result.user.id) {
        resetAccount();
        epoch = accountEpoch;
        revision = editRevision;
      }
      const next = await api("/api/workspace");
      if (epoch !== accountEpoch) throw new Error("账号已切换");
      if (revision !== editRevision || latest && !discardPending) {
        // A read started before a local edit must wait for that edit and read again.
        discardPending = false;
        continue;
      }
      session.user = result.user;
      if ((next.tasks?.version || 0) >= taskVersion) {
        canonicalTasks = clone(next.tasks?.value || []);
        taskVersion = next.tasks?.version || 0;
        taskGeneration = next.taskWriteGeneration || 0;
      }
      if (discardPending) {
        await removeOperations(session.user.id, pendingChanges.map(change => change.operation.id));
        if (epoch !== accountEpoch) throw new Error('账号已切换');
        latest = null;
        failed = false;
        pendingChanges = [];
        session.conflict = false;
      }
      if (journalLoaded !== session.user.id) {
        const operations = await readOperations(session.user.id);
        if (epoch !== accountEpoch) throw new Error('账号已切换');
        pendingChanges = operations.map(operation => ({ operation, epoch, retryOnFailure: true, reloadOnStale: true }));
        journalLoaded = session.user.id;
        if (pendingChanges.length) {
          try { await reconcileChanges(epoch); }
          catch (error) {
            if (epoch !== accountEpoch) throw error;
            if (error.status !== 409) throw error;
            failed = true; session.conflict = true; report('error', error);
            window.dispatchEvent(new CustomEvent('niuma:session-ready'));
            return view();
          }
          let projected = clone(canonicalTasks);
          try {
            for (const change of pendingChanges) projected = transformIntent(change.operation.intent)(projected);
          } catch (error) {
            failed = true;
            session.conflict = true;
            report('error', error);
          }
          const restored = viewOf(projected);
          if (!failed) {
            report(pendingChanges.length ? 'saving' : 'saved');
            for (const change of pendingChanges) enqueueChange(change).catch(() => {});
          }
          window.dispatchEvent(new CustomEvent('niuma:session-ready'));
          return restored;
        }
      }
      report("saved");
      window.dispatchEvent(new CustomEvent("niuma:session-ready"));
      return view();
    }
  } catch (error) {
    if (epoch !== accountEpoch) throw error;
    if (error.status === 401) {
      resetAccount();
      report("saved");
      return empty();
    }
    report("error", error);
    throw error;
  }
}
export async function initSession() {
  return loadSession();
}
export async function reconnectStore() {
  const epoch = accountEpoch;
  await drainWrites();
  if (epoch !== accountEpoch) throw new Error('账号已切换');
  journalLoaded = null;
  failed = false;
  session.conflict = false;
  return loadSession();
}
export async function refreshStore() {
  const epoch = accountEpoch;
  await flushStore();
  if (epoch !== accountEpoch) throw new Error("账号已切换");
  return initSession();
}
export async function reloadStore() {
  if (session.changing) throw new Error("正在切换账号，请稍后重新读取");
  const epoch = accountEpoch;
  await drainWrites();
  if (epoch !== accountEpoch) throw new Error("账号已切换");
  // Explicitly replace a failed local draft only after fresh data has arrived.
  return loadSession(true);
}
function taskPayload(data) {
  const previous = new Map(canonicalTasks.map((task) => [task.id, task]));
  const incoming = new Set(data.tasks.map((task) => task.id));
  return [
    // Deletion is explicit; absence from an older UI snapshot must not erase a task.
    ...canonicalTasks.filter((task) => task.status === "cancelled" || task.recycleBin || !incoming.has(task.id)),
    ...data.tasks
      .filter((task) => !previous.get(task.id)?.recycleBin)
      .map((task) => toCanonical(task, previous.get(task.id))),
  ];
}
function enqueue(snapshot) {
  const epoch = accountEpoch;
  queue = queue.then(async () => {
    if (failed || epoch !== accountEpoch) return;
    report("saving");
    try {
      const value = taskPayload(snapshot);
      if (JSON.stringify(value) !== JSON.stringify(canonicalTasks)) {
        const result = await api(
          "/api/data/tasks",
          { value, version: taskVersion },
          "PUT",
        );
        if (epoch !== accountEpoch) return;
        taskVersion = result.version;
        canonicalTasks = clone(value);
      }
      if (latest === snapshot) latest = null;
      report(latest || pendingChanges.length ? "saving" : "saved");
    } catch (error) {
      if (epoch !== accountEpoch) return;
      failed = true;
      session.conflict = error.status === 409;
      report("error", error);
    }
  });
  return queue;
}
export function saveStore(data) {
  if (!session.user || session.changing) {
    report("error", "请先登录账号，或等待账号切换完成");
    return false;
  }
  const previous = new Map((latest?.tasks || canonicalTasks).map(task => [task.id, task]));
  for (const task of data.tasks) {
    if (task.content_type === "notice") task.planningExcluded = true;
    else if (previous.get(task.id)?.content_type === "notice" && task.content_type === "task") task.planningExcluded = false;
  }
  editRevision++;
  latest = clone(data);
  if (!failed) enqueue(latest);
  return true;
}
function removeChange(change) {
  pendingChanges = pendingChanges.filter(item => item !== change);
}
function enqueueChange(change) {
  const { epoch, retryOnFailure, reloadOnStale, operation } = change;
  const update = queue.then(async () => {
    await change.staged;
    if (epoch !== accountEpoch) throw new Error("账号已切换");
    if (!pendingChanges.includes(change)) return;
    if (failed) {
      throw new Error(session.error || "还有未保存的数据");
    }
    let value;
    try {
      // Apply each intent to the last acknowledged tasks, never a stale UI list.
      value = transformIntent(operation.intent)(clone(canonicalTasks));
    } catch (error) {
      if (reloadOnStale) {
        // An optimistic edit based on stale content needs a fresh workspace.
        failed = true;
        session.conflict = true;
        report("error", error);
      } else {
        await removeOperations(operation.owner, [operation.id]);
        removeChange(change);
        report(latest || pendingChanges.length ? "saving" : "saved");
      }
      throw error;
    }
    report("saving");
    try {
      const result = await api(
        "/api/data/tasks", { value, version: taskVersion, operation: operationIdentity(operation) }, "PUT",
      );
      if (epoch !== accountEpoch) throw new Error("账号已切换");
      if (result.operationId !== operation.id) throw new Error('服务器未确认操作编号，请更新服务后重试；本地操作仍保留');
      taskVersion = result.version;
      canonicalTasks = clone(result.replayed ? result.value : value);
      await removeOperations(operation.owner, [operation.id]);
      if (epoch !== accountEpoch) throw new Error('账号已切换');
      removeChange(change);
      if (result.replayed && pendingChanges.length) await reconcileChanges(epoch);
      session.conflict = false;
      report(latest || pendingChanges.length ? "saving" : "saved");
    } catch (error) {
      if (epoch === accountEpoch) {
        session.conflict = error.status === 409;
        const rejected = error.status >= 400 && error.status < 500;
        if (!retryOnFailure && rejected) {
          await removeOperations(operation.owner, [operation.id]);
          removeChange(change);
        }
        // Preserve the failed intent and every following edit for explicit retry.
        if (retryOnFailure || session.conflict || !rejected) {
          failed = true;
          report("error", error);
        } else report(latest || pendingChanges.length ? "saving" : "saved");
      }
      throw error;
    }
  });
  queue = update.catch(() => {});
  return update;
}
function operationIdentity({ id, owner, intent }) { return { id, owner, intent }; }
async function reconcileChanges(epoch) {
  if (!pendingChanges.length) return;
  const result = await api('/api/tasks/reconcile', {
    owner: session.user.id, operations: pendingChanges.map(change => operationIdentity(change.operation)),
  });
  if (epoch !== accountEpoch) throw new Error('账号已切换');
  if (!Array.isArray(result.applied) || !Array.isArray(result.tasks?.value) || !Number.isInteger(result.tasks.version))
    throw new Error('服务器未提供有效的保存回执，请更新服务后重试；本地操作仍保留');
  await removeOperations(session.user.id, result.applied);
  if (epoch !== accountEpoch) throw new Error('账号已切换');
  pendingChanges = pendingChanges.filter(change => !result.applied.includes(change.operation.id));
  canonicalTasks = clone(result.tasks.value);
  taskVersion = result.tasks.version;
  taskGeneration = result.generation || 0;
}
function changeStoredTasks(intent, { retryOnFailure = true, reloadOnStale = false } = {}) {
  if (!session.user || session.changing) {
    const rejected = Promise.reject(new Error("请先登录账号，或等待账号切换完成"));
    rejected.staged = rejected; rejected.catch(() => {});
    return rejected;
  }
  editRevision++;
  const operation = { format: 1, id: [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, '0')).join(''), owner: session.user.id,
    order: order = Math.max(Date.now(), order + 1), intent: { ...clone(intent), workspaceGeneration: taskGeneration } };
  const change = { operation, retryOnFailure, reloadOnStale, epoch: accountEpoch };
  pendingChanges.push(change);
  change.staged = staging.then(async () => {
    if (change.epoch !== accountEpoch) throw new Error('账号已切换');
    await appendOperation(operation);
    if (change.epoch !== accountEpoch) throw new Error('账号已切换');
  }).catch(error => { removeChange(change); throw error; });
  staging = change.staged.catch(() => {});
  const update = enqueueChange(change);
  update.staged = change.staged;
  update.catch(() => {});
  return update;
}
export function saveStoredTask(task, expected) {
  const draft = Object.fromEntries(['id', 'title', 'notes', 'course', 'content_type', 'due', 'dueTime'].map(key => [key, task[key] ?? '']));
  draft.content_type ||= 'task';
  return changeStoredTasks({ kind: 'save', draft, expected: expected ?? null, createdAt: new Date().toISOString() }, { reloadOnStale: true });
}
export function setStoredTaskStatus(id, next, expected) {
  return changeStoredTasks({ kind: 'status', id, next, expected }, { reloadOnStale: true });
}
function transformIntent(intent) {
  const { kind, id, next, expected, draft, createdAt } = intent;
  if (kind === 'save') return tasks => {
    const index = tasks.findIndex(item => item.id === draft.id);
    const current = tasks[index];
    if (expected !== null) {
      assertCurrentTaskEdit(current && !current.recycleBin && current.status !== "cancelled" ? toView(current) : null, expected);
    } else if (current) throw new Error("任务已存在，请重新读取后核对");
    const previous = current || { createdAt };
    const next = toCanonical({
      ...(current ? toView(current) : { id: draft.id, status: "todo" }),
      title: draft.title, notes: draft.notes, course: draft.course,
      content_type: draft.content_type, due: draft.due, dueTime: draft.dueTime,
    }, previous);
    if (index < 0) tasks.push(next);
    else tasks[index] = next;
    return tasks;
  };
  if (kind === 'status') return tasks => {
    const task = tasks.find(item => item.id === id && !item.recycleBin && item.status !== "cancelled");
    if (!task || status[task.status] !== expected.status ||
        (task.completedAt || "") !== (expected.completedAt || ""))
      throw new Error("任务状态已变化，请重新读取后核对");
    task.status = canonicalStatus[next.status];
    task.completedAt = next.completedAt || "";
    return tasks;
  };
  if (kind === 'deadline') return tasks => {
    const task = tasks.find((item) => item.id === id && !item.recycleBin && item.status !== "cancelled");
    if (!task ||
        (task.due?.date || "") !== expected.due ||
        (task.due?.time || "") !== expected.dueTime)
      throw new Error("任务日期已变化，请重新核对");
    task.due = next.due ? {
      date: next.due,
      precision: next.dueTime ? "datetime" : "date",
      ...(next.dueTime ? { time: next.dueTime } : {}),
    } : null;
    return tasks;
  };
  const selected = new Set(intent.ids);
  if (kind === 'trash') return tasks => {
    if (
      !selected.size ||
      [...selected].some(
        (id) => !tasks.some(
          (task) => task.id === id && task.status !== "cancelled" && !task.recycleBin,
        ),
      )
    ) throw new Error("要删除的任务不存在，请刷新后重试");
    const deletedAt = intent.deletedAt;
    return tasks.map((task) => selected.has(task.id)
      ? {
          ...task,
          recycleBin: { deletedAt, status: task.status },
          status: task.status === "completed" ? "completed" : "cancelled",
        }
      : task,
    );
  };
  if (kind === 'restore') return tasks => {
    if (
      !selected.size ||
      [...selected].some(
        (id) => !tasks.some((task) => task.id === id && task.recycleBin),
      )
    ) throw new Error("回收站任务不存在，请刷新后重试");
    return tasks.map((task) => {
      if (!selected.has(task.id)) return task;
      const { recycleBin, ...restored } = task;
      return { ...restored, status: recycleBin.status };
    });
  };
  throw new Error('待保存操作版本不受支持，请更新页面后恢复');
}
export async function setStoredTaskDeadline(id, next, expected) {
  if ((next.due && !shiftDay(next.due, 0)) || (next.dueTime &&
      (!next.due || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(next.dueTime))))
    throw new Error('截止日期或时刻无效');
  const epoch = accountEpoch;
  await changeStoredTasks({ kind: 'deadline', id, next, expected }, { retryOnFailure: false });
  if (epoch !== accountEpoch) throw new Error('账号已切换');
  return view();
}
export function trashStoredTasks(ids) {
  return changeStoredTasks({ kind: 'trash', ids: [...ids], deletedAt: new Date().toISOString() });
}
export function restoreStoredTasks(ids) {
  return changeStoredTasks({ kind: 'restore', ids: [...ids] });
}
async function drainWrites() {
  let pending;
  do {
    pending = queue;
    await pending;
  } while (pending !== queue);
}
export async function flushStore() {
  await drainWrites();
  if (failed) throw new Error(session.error || "还有未保存的数据");
}
export function retrySave() {
  if (retrying) return retrying;
  const pending = retryPendingChanges();
  retrying = pending;
  pending.finally(() => { if (retrying === pending) retrying = null; }).catch(() => {});
  return pending;
}
async function retryPendingChanges() {
  const epoch = accountEpoch;
  await drainWrites();
  if (epoch !== accountEpoch) throw new Error("账号已切换");
  if (session.conflict) throw new Error("数据已被其他页面修改，请刷新后再编辑");
  await reconcileChanges(epoch);
  failed = false;
  await Promise.all([...pendingChanges].map(change => enqueueChange(change)));
  if (latest) await enqueue(latest);
  await flushStore();
}
export async function authenticate(mode, credentials) {
  if (session.changing) throw new Error("正在切换账号");
  session.changing = true;
  try {
    await flushStore();
    if (session.user) await clearJournal(session.user.id);
    resetAccount();
    await api(
      "/api/auth/" + mode,
      mode === "register"
        ? {
            email: credentials.email,
            password: credentials.password,
            name: credentials.displayName,
            challengeId: credentials.challengeId,
            code: credentials.code,
          }
        : { email: credentials.email, password: credentials.password },
    );
    return await initSession();
  } finally {
    session.changing = false;
  }
}
export async function logout() {
  if (session.changing) throw new Error("正在切换账号");
  session.changing = true;
  try {
    await flushStore();
    const owner = session.user?.id;
    if (owner) await clearJournal(owner);
    resetAccount();
    await api("/api/auth/logout", {});
    report("saved");
    return empty();
  } finally {
    session.changing = false;
  }
}
export function getTaskVersion() {
  return taskVersion;
}
window.addEventListener("beforeunload", (event) => {
  if (latest || failed || pendingChanges.length) {
    event.preventDefault();
    event.returnValue = "";
  }
});

import { analyzeQuickTask } from "./quick-task.js";
import { deadlineLabel } from "./task-model.js";

export function renderQuickEntry({ icon }) {
  return `<form id="quick-task-form">
    <h2 id="drawer-heading" class="sr-only">一句话添加任务</h2>
    <label for="quick-text" class="sr-only">用一句话描述任务</label>
    <div class="quick-editor">
      <div class="quick-mirror" aria-hidden="true"><span></span></div>
      <input id="quick-text" type="text" maxlength="400" required autofocus autocomplete="off" enterkeyhint="done"
        placeholder="例如：明天早上8点上数算课" aria-describedby="task-edit-error">
    </div>
    <div class="quick-toolbar">
      <button type="button" id="quick-date-button" class="quick-property" aria-expanded="false" aria-controls="quick-date-panel">
        ${icon("calendar")}<span></span>
      </button>
      <button type="button" id="quick-course-button" class="quick-property" aria-expanded="false" aria-controls="quick-course-panel">
        ${icon("tasks")}<span></span>
      </button>
      <button type="button" id="quick-more" class="quick-property" aria-label="更多添加方式" aria-expanded="false" aria-controls="quick-import-panel">更多</button>
      <button type="button" class="icon-button quick-cancel" data-action="drawer-close" aria-label="取消添加" title="取消添加">${icon("close")}</button>
      <button type="submit" class="btn primary quick-submit">添加</button>
    </div>
    <div id="quick-date-panel" class="quick-fields" hidden>
      <label class="field">截止日期<input id="quick-date" type="date"></label>
      <label class="field">具体时刻<input id="quick-time" type="time" step="60"></label>
      <button type="button" class="btn quiet quick-reset" data-reset="date" hidden>恢复自动识别</button>
    </div>
    <div id="quick-course-panel" class="quick-fields" hidden>
      <label class="field">课程或分类<input id="quick-course" maxlength="100" list="quick-course-options" autocomplete="off" placeholder="输入或选择分类"></label>
      <datalist id="quick-course-options"></datalist>
      <button type="button" class="btn quiet quick-reset" data-reset="course" hidden>恢复自动识别</button>
    </div>
    <div id="quick-import-panel" class="quick-imports" hidden>
      <button type="button" class="btn quiet" data-quick-import="text">粘贴通知</button>
      <button type="button" class="btn quiet" data-quick-import="files">导入截图或文件</button>
    </div>
    <div id="task-edit-error" class="error" role="alert"></div>
  </form>`;
}

function showTimeSpans(mirror, text, spans) {
  // Build with text nodes so that the highlighted line remains literal user input.
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const { start, end } of spans) {
    fragment.append(text.slice(cursor, start));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(start, end);
    fragment.append(mark);
    cursor = end;
  }
  fragment.append(text.slice(cursor));
  mirror.replaceChildren(fragment);
}

export function bindQuickEntry(form, options) {
  const input = form.querySelector("#quick-text");
  const mirror = form.querySelector(".quick-mirror span");
  const dateButton = form.querySelector("#quick-date-button");
  const courseButton = form.querySelector("#quick-course-button");
  const moreButton = form.querySelector("#quick-more");
  const importPanel = form.querySelector("#quick-import-panel");
  const dateInput = form.querySelector("#quick-date");
  const timeInput = form.querySelector("#quick-time");
  const courseInput = form.querySelector("#quick-course");
  const courseList = form.querySelector("#quick-course-options");
  const dateReset = form.querySelector('[data-reset="date"]');
  const courseReset = form.querySelector('[data-reset="course"]');
  const error = form.querySelector("#task-edit-error");
  for (const course of new Set(options.courses)) courseList.append(new Option(course));

  const getToday = options.getToday ?? (() => options.today);
  let referenceDay = getToday();
  let parsedText = input.value;
  const analyze = source => analyzeQuickTask(source, { ...options, today: referenceDay });
  let analysis = analyze(parsedText);
  let dateOverride = null;
  let courseOverride = null;
  const readDraft = () => ({
    ...analysis.task,
    ...dateOverride,
    course: courseOverride ?? analysis.task.course,
  });

  function syncScroll() {
    mirror.style.transform = `translateX(${-input.scrollLeft}px)`;
  }

  function refresh() {
    const { due, dueTime, course } = readDraft();
    const day = due ? deadlineLabel({ due }, referenceDay) : "日期";
    const when = dueTime ? `${day}，${dueTime}` : day;
    dateButton.querySelector("span").textContent = when;
    dateButton.setAttribute("aria-label", `调整日期：${due ? when : "无截止日期"}`);
    dateButton.title = due ? `${due} ${dueTime}`.trim() : "设置日期";
    dateButton.classList.toggle("has-value", Boolean(due));
    courseButton.querySelector("span").textContent = course || "分类";
    courseButton.setAttribute("aria-label", `调整分类：${course || "未分类"}`);
    courseButton.title = course || "设置分类";
    moreButton.hidden = Boolean(input.value.length);
    if (moreButton.hidden) {
      moreButton.setAttribute("aria-expanded", "false");
      importPanel.hidden = true;
    }
    dateReset.hidden = !dateOverride;
    courseReset.hidden = courseOverride === null;
    if (dateInput.value !== due) dateInput.value = due;
    if (timeInput.value !== dueTime) timeInput.value = dueTime;
    if (document.activeElement !== courseInput) courseInput.value = course;
    showTimeSpans(mirror, input.value, dateOverride ? [] : analysis.spans);
    syncScroll();
  }

  function refreshDay() {
    const nextDay = getToday();
    if (referenceDay === nextDay) return false;
    const previousDue = readDraft().due;
    referenceDay = nextDay;
    // Date overrides are absolute. Reinterpret only the original sentence.
    analysis = analyze(parsedText);
    refresh();
    return readDraft().due !== previousDue;
  }

  function parseInput() {
    refreshDay();
    parsedText = input.value;
    const next = analyze(parsedText);
    if (next.task.due !== analysis.task.due || next.task.dueTime !== analysis.task.dueTime) dateOverride = null;
    if (next.task.course !== analysis.task.course) courseOverride = null;
    analysis = next;
    error.textContent = "";
    refresh();
  }

  input.addEventListener("scroll", syncScroll);
  input.addEventListener("input", event => {
    if (!event.isComposing && !form.classList.contains("is-composing")) parseInput();
  });
  input.addEventListener("compositionstart", () => form.classList.add("is-composing"));
  input.addEventListener("compositionend", () => {
    form.classList.remove("is-composing");
    parseInput();
  });

  // Keep Enter available to the input method when it accepts a candidate.
  // The form guard suppresses only an implicit submit during that key event.
  let confirmingComposition = false;
  input.addEventListener("keydown", event => {
    if ((event.key === "Enter" || event.keyCode === 229) && (event.isComposing || event.keyCode === 229)) {
      confirmingComposition = true;
      setTimeout(() => { confirmingComposition = false; }, 0);
    }
  });
  form.addEventListener("submit", event => {
    if (confirmingComposition || form.classList.contains("is-composing")) {
      event.preventDefault();
      event.stopPropagation();
    } else if (refreshDay()) {
      // A suspended tab may receive a click before its midnight timer runs.
      // Present the revised absolute day before the application saves it.
      event.preventDefault();
      event.stopPropagation();
      const revisedDate = readDraft().due || "无截止日期";
      error.textContent = `已跨日，当前截止日期为「${revisedDate}」。确认后再点添加。`;
    }
  });

  const controls = [dateButton, courseButton, moreButton];
  for (const button of controls) {
    button.addEventListener("click", () => {
      const opening = button.getAttribute("aria-expanded") === "false";
      for (const control of controls) {
        const panel = form.querySelector(`#${control.getAttribute("aria-controls")}`);
        const expanded = opening && control === button;
        control.setAttribute("aria-expanded", String(expanded));
        panel.hidden = !expanded;
        if (expanded) panel.querySelector("input,button")?.focus();
      }
    });
  }
  function setDate() {
    dateOverride = { due: dateInput.value, dueTime: dateInput.value ? timeInput.value : "" };
    refresh();
  }
  dateInput.addEventListener("input", setDate);
  timeInput.addEventListener("input", () => {
    if (timeInput.value && !dateInput.value) dateInput.value = options.defaultDate || getToday();
    setDate();
  });
  courseInput.addEventListener("input", () => {
    courseOverride = courseInput.value.trim();
    refresh();
  });
  dateReset.addEventListener("click", () => {
    dateOverride = null;
    dateButton.focus();
    refresh();
  });
  courseReset.addEventListener("click", () => {
    courseOverride = null;
    courseButton.focus();
    refresh();
  });
  refresh();
  readDraft.recognition = () => {
    const protectedFields = [];
    if (dateOverride || (options.defaultDate && analysis.task.due === options.defaultDate))
      protectedFields.push("due");
    if (courseOverride !== null || analysis.explicitCourse || (options.defaultCourse && analysis.task.course === options.defaultCourse))
      protectedFields.push("course");
    return { text: input.value.trim(), referenceDay, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, protectedFields };
  };
  readDraft.refreshDay = refreshDay;
  return readDraft;
}

import { analyzeQuickTask } from "./quick-task.js";
import { quickAttachmentButton, bindQuickImport } from "./quick-import.js";
import { deadlineLabel } from "./task-model.js";

export function renderQuickEntry({ icon }) {
  return `<form id="quick-task-form">
    <h2 id="drawer-heading" class="sr-only">一句话添加任务</h2>
    <label for="quick-text" class="sr-only">用一句话描述任务</label>
    <div class="quick-editor">
      <div class="quick-mirror" aria-hidden="true"><span></span></div>
      <textarea id="quick-text" rows="1" maxlength="4000" required autofocus autocomplete="off" enterkeyhint="done"
        placeholder="写任务、粘贴通知，或拖入文件" aria-describedby="task-edit-error"></textarea>
    </div>
    <div id="quick-summary" hidden></div>
    <ul id="quick-selected-files" hidden></ul>
    <div class="quick-toolbar">
      ${quickAttachmentButton(icon)}
      <button type="button" id="quick-date-button" class="quick-property" aria-expanded="false" aria-controls="quick-date-panel">
        ${icon("calendar")}<span></span>
      </button>
      <button type="button" id="quick-course-button" class="quick-property" aria-expanded="false" aria-controls="quick-course-panel">
        ${icon("tasks")}<span></span>
      </button>
      <button type="submit" class="btn primary quick-submit">加入</button>
    </div>
    <div id="quick-date-panel" class="quick-fields" hidden>
      <label class="field"><span id="quick-date-label">截止日期</span><input id="quick-date" type="date"></label>
      <label class="field">具体时刻<input id="quick-time" type="time" step="60"></label>
      <button type="button" class="btn quiet quick-reset" data-reset="date" hidden>恢复自动识别</button>
    </div>
    <div id="quick-course-panel" class="quick-fields" hidden>
      <label class="field">课程或分类<input id="quick-course" maxlength="100" list="quick-course-options" autocomplete="off" placeholder="输入或选择分类"></label>
      <datalist id="quick-course-options"></datalist>
      <button type="button" class="btn quiet quick-reset" data-reset="course" hidden>恢复自动识别</button>
    </div>
    <div id="quick-publication-panel" class="quick-fields" hidden>
      <label class="field">原通知发布时间（可选，北京时间）<input id="quick-published" type="datetime-local" step="1"></label>
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
  const imports = bindQuickImport(form, options, refresh);
  const dateInput = form.querySelector("#quick-date");
  const timeInput = form.querySelector("#quick-time");
  const courseInput = form.querySelector("#quick-course");
  const courseList = form.querySelector("#quick-course-options");
  const dateReset = form.querySelector('[data-reset="date"]');
  const publicationField = form.querySelector("#quick-published").closest("label");
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
    mirror.style.transform = `translateY(${-input.scrollTop}px)`;
  }

  function refresh() {
    const { due, dueTime, course, title, timeKind } = readDraft();
    const event = timeKind === "event";
    const summary = form.querySelector("#quick-summary");
    summary.hidden = !title || title === input.value.trim();
    summary.textContent = title;
    form.querySelector("#quick-date-label").textContent = event ? "活动日期" : "截止日期";
    const day = due ? deadlineLabel({ due }, referenceDay) : "日期";
    const when = dueTime ? `${day}，${dueTime}` : day;
    dateButton.querySelector("span").textContent = when;
    dateButton.setAttribute("aria-label", `${event ? "调整活动时间" : "调整日期"}：${due ? when : "无截止日期"}`);
    dateButton.title = due ? `${due} ${dueTime}`.trim() : "设置日期";
    dateButton.classList.toggle("has-value", Boolean(due));
    courseButton.querySelector("span").textContent = course || "分类";
    courseButton.setAttribute("aria-label", `调整分类：${course || "未分类"}`);
    courseButton.title = course || "设置分类";
    const mode = imports.mode(analysis.localOnly);
    const submit = form.querySelector(".quick-submit");
    if (!imports.isBusy()) submit.textContent = "加入";
    summary.hidden ||= Boolean(mode);
    courseButton.hidden = mode === "text" || mode === "files";
    dateButton.hidden = mode === "files";
    const panelId = mode === "text" ? "quick-publication-panel" : "quick-date-panel";
    if (dateButton.getAttribute("aria-controls") !== panelId || dateButton.hidden) {
      form.querySelector("#quick-publication-panel").hidden = true;
      form.querySelector("#quick-date-panel").hidden = true;
      dateButton.setAttribute("aria-expanded", "false");
    }
    dateButton.setAttribute("aria-controls", panelId);
    const publicationPanel = form.querySelector(mode === "personal" ? "#quick-date-panel" : "#quick-publication-panel");
    if (publicationField.parentElement !== publicationPanel) publicationPanel.append(publicationField);
    if (mode === "personal") form.querySelector("#quick-date-label").textContent = "指定任务日期";
    if (mode === "text") {
      dateButton.querySelector("span").textContent = "通知时间";
      dateButton.setAttribute("aria-label", "设置通知发布时间");
      dateButton.title = "相对日期默认按加入时的本地时间计算，可指定原通知时间";
    }
    if (courseButton.hidden) { form.querySelector("#quick-course-panel").hidden = true; courseButton.setAttribute("aria-expanded", "false"); }
    dateReset.hidden = !dateOverride;
    courseReset.hidden = courseOverride === null;
    if (dateInput.value !== due) dateInput.value = due;
    if (timeInput.value !== dueTime) timeInput.value = dueTime;
    if (document.activeElement !== courseInput) courseInput.value = course;
    showTimeSpans(mirror, input.value, dateOverride ? [] : analysis.spans);
    input.style.height = "auto";
    input.style.height = Math.min(140, Math.max(46, input.scrollHeight)) + "px";
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
    parsedText = input.value.trim();
    analysis = analyze(parsedText);
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
    } else if (event.key === "Enter" && !event.shiftKey && !form.classList.contains("is-composing")) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", event => {
    if (confirmingComposition || form.classList.contains("is-composing")) {
      event.preventDefault();
      event.stopPropagation();
    } else if (imports.mode(analysis.localOnly)) {
      event.preventDefault();
      event.stopPropagation();
      const settings = {};
      if (dateOverride) {
        const { due, dueTime } = dateOverride;
        settings.when = due ? { date: due, precision: dueTime ? "datetime" : "date", ...(dueTime && { time: dueTime }) } : null;
      } else if (options.defaultDate) settings.default_date = options.defaultDate;
      if (courseOverride !== null || analysis.explicitCourse) settings.course = readDraft().course;
      else if (options.defaultCourse) settings.default_course = options.defaultCourse;
      void imports.submit(imports.mode(analysis.localOnly), settings);
    } else if (refreshDay()) {
      // A suspended tab may receive a click before its midnight timer runs.
      // Present the revised absolute day before the application saves it.
      event.preventDefault();
      event.stopPropagation();
      const revisedDate = readDraft().due || "无截止日期";
      error.textContent = `已跨日，当前截止日期为「${revisedDate}」。确认后再点加入。`;
    }
  });

  const controls = [dateButton, courseButton];
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
    return { ...(analysis.localOnly ? { localOnly: true } : {}), text: input.value.trim(), referenceDay, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, protectedFields };
  };
  readDraft.refreshDay = refreshDay;
  readDraft.dispose = imports.dispose;
  readDraft.isImporting = imports.isBusy;
  return readDraft;
}

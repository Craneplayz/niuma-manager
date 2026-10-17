// Keep a normal click intact; only a deliberate mouse movement changes a date.
export function createCalendarDrag({ root, getTask, onDrop }) {
  let gesture = null, suppressClick = false;

  function cancel() {
    const previous = gesture;
    gesture = null;
    previous?.cell?.classList.remove("is-drop-target");
    previous?.source.classList.remove("is-dragging");
    previous?.preview?.remove();
    document.body.classList.remove("is-calendar-dragging");
    if (previous && root.hasPointerCapture(previous.pointerId))
      root.releasePointerCapture(previous.pointerId);
  }

  function targetAt(event) {
    return document.elementFromPoint(event.clientX, event.clientY)
      ?.closest(".calendar-view [data-calendar-drop-date]") || null;
  }

  function move(event) {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (event.buttons !== 1 || !gesture.source.isConnected || !getTask(gesture.id)) {
      cancel();
      return;
    }
    if (!gesture.preview) {
      if (Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) < 6) return;
      suppressClick = true;
      root.setPointerCapture(event.pointerId);
      gesture.source.classList.add("is-dragging");
      document.body.classList.add("is-calendar-dragging");
      const preview = document.createElement("div");
      preview.className = "calendar-drag-preview";
      const title = document.createElement("strong");
      title.textContent = gesture.title;
      gesture.previewTitle = title;
      gesture.label = document.createElement("span");
      gesture.label.setAttribute("role", "status");
      preview.append(title, gesture.label);
      document.body.append(preview);
      gesture.preview = preview;
    }
    event.preventDefault();
    const cell = targetAt(event);
    if (cell !== gesture.cell) {
      gesture.cell?.classList.remove("is-drop-target");
      cell?.classList.add("is-drop-target");
      gesture.cell = cell;
    }
    const date = cell?.dataset.calendarDropDate;
    const label = date
      ? `松开设为 ${date.replace(/^(\d+)-(\d+)-(\d+)$/, (_, y, m, d) => `${y} 年 ${Number(m)} 月 ${Number(d)} 日`)}${gesture.dueTime ? ` ${gesture.dueTime}` : ""}截止`
      : "拖到日历日期设置截止日期 · Esc 取消";
    if (gesture.label.textContent !== label) gesture.label.textContent = label;
    const previewRect = gesture.preview.getBoundingClientRect();
    const titleRect = gesture.previewTitle.getBoundingClientRect();
    // Anchor the grabbed title under the pointer instead of trailing it like a tooltip.
    const offsetX = titleRect.left - previewRect.left + titleRect.width * gesture.grabX;
    const offsetY = titleRect.top - previewRect.top + titleRect.height * gesture.grabY;
    gesture.preview.style.left = `${event.clientX - offsetX}px`;
    gesture.preview.style.top = `${event.clientY - offsetY}px`;
  }

  document.addEventListener("pointerdown", (event) => {
    suppressClick = false;
    cancel();
    if (!event.isPrimary || event.button !== 0 || event.pointerType !== "mouse") return;
    const source = event.target.closest("[data-calendar-drag-task]");
    if (!source || !root.contains(source)) return;
    const id = source.dataset.calendarDragTask, task = getTask(id);
    if (!task) return;
    const titleRect = (source.querySelector(".task-title, .calendar-task-title") || source).getBoundingClientRect();
    const onTitle = event.clientX >= titleRect.left && event.clientX <= titleRect.left + titleRect.width &&
      event.clientY >= titleRect.top && event.clientY <= titleRect.top + titleRect.height;
    const grab = (position, start, size) => onTitle && size
      ? (position - start) / size : .5;
    gesture = {
      id, title: task.title, dueTime: task.dueTime, source, pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      grabX: grab(event.clientX, titleRect.left, titleRect.width),
      grabY: grab(event.clientY, titleRect.top, titleRect.height),
    };
  });
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", (event) => {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const id = gesture.id;
    const date = gesture.preview && event.button === 0 && getTask(id)
      ? targetAt(event)?.dataset.calendarDropDate : null;
    if (gesture.preview) event.preventDefault();
    cancel();
    if (date) void onDrop(id, date);
  });
  document.addEventListener("click", (event) => {
    if (!suppressClick || event.detail === 0) return;
    suppressClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener("dragstart", (event) => {
    if (event.target.closest("[data-calendar-drag-task]")) event.preventDefault();
  });
  document.addEventListener("pointercancel", cancel);
  root.addEventListener("lostpointercapture", cancel);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && gesture) {
      if (gesture.preview) event.preventDefault();
      cancel();
    }
  });
  window.addEventListener("blur", cancel);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") cancel();
  });
  return { cancel };
}

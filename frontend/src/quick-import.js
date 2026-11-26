import { newId } from "./id.js";
import { buildTextImport, materialAccept, newMaterialDraft } from "./quick-import-data.js";

export function quickAttachmentButton(icon) {
  return `<button type="button" id="quick-attach" class="quick-property" aria-label="添加图片或文件" title="添加图片或文件，也可直接拖入">${icon("plus")}</button>
    <input id="quick-files" type="file" multiple accept="${materialAccept}" hidden>`;
}

function quickImportMode(text, localOnly, demo, pasted) {
  if ((demo && localOnly) || !text.trim()) return "";
  const notice = text.length > 100 || /[；;]/u.test(text) || /\n|[。；;！？].+|[，,].*(?:再|另|还需|周[一二三四五六日]|明天|后天)/u.test(text);
  if (notice || (demo && pasted)) return "text";
  return demo ? "" : "personal";
}

export function bindQuickImport(form, options, refresh) {
  const input = form.querySelector("#quick-text"), picker = form.querySelector("#quick-files");
  const filesBox = form.querySelector("#quick-selected-files"), error = form.querySelector("#task-edit-error");
  const dialog = form.closest("dialog"), controller = new AbortController();
  const { signal } = controller;
  let files = [], pasted = false, busy = false, draft = null, textBody = null, signature = "";
  let dragDepth = 0;
  function repaintFiles() {
    filesBox.replaceChildren();
    for (const file of files) {
      const row = document.createElement("li"), name = document.createElement("span"), remove = document.createElement("button");
      name.textContent = file.name;
      remove.type = "button"; remove.className = "btn quiet"; remove.textContent = "移除";
      remove.setAttribute("aria-label", "移除 " + file.name);
      remove.onclick = () => { files = files.filter(f => f !== file); draft = null; repaintFiles(); refresh(); };
      row.append(name, remove); filesBox.append(row);
    }
    filesBox.hidden = !files.length;
    input.required = !files.length;
  }
  function select(selected) {
    if (busy || !selected.length) return;
    const allowed = materialAccept.split(",");
    if (selected.some(f => !allowed.some(ext => f.name.toLowerCase().endsWith(ext)))) {
      error.textContent = "支持 PNG、JPG、PDF 和 DOCX，请重新选择。"; return;
    }
    const next = files.slice();
    for (const f of selected) if (!next.some(s => s.name === f.name && s.size === f.size && s.lastModified === f.lastModified)) next.push(f);
    if (next.length > 20 || next.some(f => !f.size)) { error.textContent = "最多选择 20 个文件，文件不能为空。"; return; }
    files = next; draft = null; error.textContent = ""; repaintFiles(); refresh();
  }
  form.querySelector("#quick-attach").onclick = () => picker.click();
  picker.onchange = () => { select([...picker.files]); picker.value = ""; };
  const containsFiles = event => [...(event.dataTransfer?.types || [])].includes("Files");
  for (const type of ["dragenter", "dragover", "dragleave", "drop"]) {
    dialog.addEventListener(type, event => {
      if (!containsFiles(event) && !(type === "dragleave" && dragDepth)) return;
      event.preventDefault();
      if (type === "dragenter") dragDepth++;
      if (type === "dragover") event.dataTransfer.dropEffect = busy ? "none" : "copy";
      if (type === "dragleave") dragDepth = Math.max(0, dragDepth - 1);
      if (type === "drop") { dragDepth = 0; select([...event.dataTransfer.files]); }
      dialog.classList.toggle("is-file-drop", dragDepth > 0 && !busy);
    }, { signal });
  }
  dialog.addEventListener("paste", e => {
    const incoming = [...(e.clipboardData?.files || [])];
    if (incoming.length) { e.preventDefault(); select(incoming); }
    else if (e.target === input) pasted = true;
  }, { signal });
  input.addEventListener("input", () => {
    if (!input.value.trim()) pasted = false;
  });
  const mode = localOnly => files.length ? "files" : quickImportMode(input.value.trim(), localOnly, options.isDemo, pasted);
  async function submit(kind, settings) {
    if (busy) return;
    if (options.isDemo) { error.textContent = "请登录后导入通知或附件。"; return; }
    busy = true; form.setAttribute("aria-busy", "true"); error.textContent = "";
    const controls = [...form.querySelectorAll("button,input,textarea")].filter(el => !el.disabled);
    for (const el of controls) el.disabled = true;
    const primary = form.querySelector(".quick-submit");
    primary.textContent = "提交中…";
    let receipt;
    try {
      const text = input.value.trim();
      if (kind === "files") {
        if (!draft || draft.text !== text) draft = { ...newMaterialDraft("quick-add:"), text };
        receipt = await options.submitFiles(files, draft, signal);
      } else {
        const published = form.querySelector("#quick-published").value;
        const timezone = published ? "Asia/Shanghai" : Intl.DateTimeFormat().resolvedOptions().timeZone;
        const key = JSON.stringify([text, published, timezone, settings]);
        if (!textBody || signature !== key) {
          textBody = buildTextImport(text, published, "quick-add:" + newId(), undefined, options.courses);
          const record = textBody.records[0];
          record.timezone = timezone;
          if (Object.keys(settings).length) record.quick_settings = settings;
          if (Object.hasOwn(settings, "course")) record.course_or_project = settings.course || null;
          signature = key;
        }
        receipt = await options.submitNotice(textBody, signal);
      }
    } catch (cause) { if (!signal.aborted) error.textContent = cause.message || "提交失败，请重试。"; }
    finally {
      busy = false; form.removeAttribute("aria-busy");
      if (!signal.aborted) {
        for (const el of controls) el.disabled = false;
        refresh();
        if (receipt) options.added(receipt);
      }
    }
  }
  return { mode, submit,
    isBusy: () => busy,
    dispose() { controller.abort(); dialog.classList.remove("is-file-drop"); } };
}

// Preserve live controls and disclosure state while updating changed regions.
const bound = new WeakSet(), composing = new WeakSet();
export const isComposing = node => Boolean(node && composing.has(node));
const keys = ['id', 'data-task-id', 'data-review-id', 'data-candidate-id', 'data-material-job',
  'data-import-history-batch-entry', 'data-scope', 'data-import-source', 'data-calendar-day'];
function key(node) {
  if (node.nodeType !== 1) return null;
  for (const name of keys) if (node.hasAttribute(name)) return `${node.tagName}:${name}:${node.getAttribute(name)}`;
  return null;
}
function compatible(a, b) {
  return a.nodeType === b.nodeType && a.nodeName === b.nodeName && key(a) === key(b);
}
function patch(current, next) {
  if (current.nodeType !== 1) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return;
  }
  const disclosure = current.tagName === 'DETAILS' &&
    !next.hasAttribute('data-source-sync-history') && !next.hasAttribute('data-material-history');
  const typing = isComposing(current);
  for (const attr of [...current.attributes]) {
    if (disclosure && attr.name === 'open' || typing && attr.name === 'value') continue;
    if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
  }
  for (const attr of next.attributes) {
    if (disclosure && attr.name === 'open' || typing && attr.name === 'value') continue;
    if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
  }
  // The evidence request owns this pane until the task/evidence identity changes.
  if (next.hasAttribute('data-preserve-content')) return;
  if (current.tagName === 'TEXTAREA') {
    if (!typing && current.value !== next.value) current.value = next.value;
    return;
  }
  if (current.tagName === 'INPUT') {
    if (current.type !== 'file' && !typing && current.value !== next.value) current.value = next.value;
    if (current.checked !== next.checked) current.checked = next.checked;
    return;
  }
  patchChildren(current, next);
  if (current.tagName === 'SELECT' && current.value !== next.value) current.value = next.value;
}
function patchChildren(current, next) {
  const old = [...current.childNodes], keyed = new Map(old.filter(key).map(node => [key(node), node]));
  const used = new Set();
  let cursor = current.firstChild;
  for (const wanted of [...next.childNodes]) {
    const id = key(wanted);
    let node = id ? keyed.get(id) : cursor;
    if (!node || used.has(node) || !compatible(node, wanted)) node = null;
    if (!node) {
      node = wanted.cloneNode(true);
      current.insertBefore(node, cursor);
    } else {
      if (node !== cursor) current.insertBefore(node, cursor);
      patch(node, wanted);
    }
    used.add(node);
    cursor = node.nextSibling;
  }
  for (const node of old) if (!used.has(node)) node.remove();
}
export function updateDOM(root, html, { reset = false } = {}) {
  if (!bound.has(root)) {
    root.addEventListener('compositionstart', event => composing.add(event.target));
    root.addEventListener('compositionend', event => composing.delete(event.target));
    bound.add(root);
  }
  const template = document.createElement('template');
  // Lightweight controller tests have no browser DOM implementation.
  if (reset || !template.content || !root.childNodes) { root.innerHTML = html; return; }
  template.innerHTML = html;
  patchChildren(root, template.content);
}

const COLOR_COUNT = 8;
const STORAGE_PREFIX = "niuma.course-colors.v1.";
const scopes = new Map();
let activeColors = new Map();

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function labelOf(value) {
  return String(value ?? "").trim().replace(/\s+/gu, " ");
}

function normalized(value) {
  return labelOf(value).normalize("NFKC").toLowerCase();
}

// Persist visual assignments without keeping course names or task content.
function fingerprint(value) {
  let first = 0x811c9dc5, second = 0x9e3779b9;
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return (first >>> 0).toString(16).padStart(8, "0") +
    (second >>> 0).toString(16).padStart(8, "0");
}

function keyOf(value) {
  const name = normalized(value);
  return name ? fingerprint(name) : "";
}

function defaultStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

function readColors(storage, key) {
  try {
    const text = storage?.getItem(key);
    const value = text ? JSON.parse(text) : null;
    if (value?.version !== 1 || !value.colors || typeof value.colors !== "object") return [];
    return Object.entries(value.colors).filter(([name, tone]) =>
      /^[a-f\d]{16}$/.test(name) && Number.isInteger(tone) && tone >= 0 && tone < COLOR_COUNT);
  } catch { return []; }
}

/** Register current courses once before rendering a workspace, under its account scope. */
export function syncCourseColors(names, { scope = "default", storage } = {}) {
  const storageKey = STORAGE_PREFIX + fingerprint(String(scope));
  let colors = scopes.get(storageKey);
  if (!colors) scopes.set(storageKey, colors = new Map());
  const store = storage === undefined ? defaultStorage() : storage;
  for (const [key, tone] of readColors(store, storageKey)) {
    if (!colors.has(key)) colors.set(key, tone);
  }
  activeColors = colors;

  const keys = [...new Set((Array.isArray(names) ? names : []).map(keyOf).filter(Boolean))];
  // Removed courses keep their assignment, but do not reserve unused colors forever.
  const used = new Set(keys.filter((key) => colors.has(key)).map((key) => colors.get(key)));
  for (const key of keys) {
    if (colors.has(key)) continue;
    const preferred = Number.parseInt(key.slice(0, 8), 16) % COLOR_COUNT;
    let tone = preferred;
    if (used.size < COLOR_COUNT) {
      while (used.has(tone)) tone = (tone + 1) % COLOR_COUNT;
    }
    colors.set(key, tone);
    used.add(tone);
  }
  try {
    const serialized = JSON.stringify({ version: 1, colors: Object.fromEntries(colors) });
    if (store && store.getItem(storageKey) !== serialized) store.setItem(storageKey, serialized);
  } catch { /* A blocked or full preference store must not prevent reading tasks. */ }
}

const shortNames = new Map([
  ["概率论", "概率"],
  ["线性代数", "线代"],
  ["数据结构", "数构"],
]);

export function courseIdentity(name) {
  const label = labelOf(name);
  const key = keyOf(name);
  if (!key) return { key: "", tone: "neutral", label: "未分类", shortLabel: "未分类" };
  return {
    key,
    tone: activeColors.get(key) ?? Number.parseInt(key.slice(0, 8), 16) % COLOR_COUNT,
    label,
    shortLabel: shortNames.get(normalized(name)) || Array.from(label).slice(0, 3).join(""),
  };
}

export function renderCourseBadge(name, { compact = false, className = "" } = {}) {
  const course = courseIdentity(name);
  const extra = String(className).split(/\s+/).filter((token) => /^[a-z][\w-]*$/i.test(token));
  const classes = ["course-badge", compact && "is-compact", ...extra].filter(Boolean).join(" ");
  return `<span class="${classes}" data-course-tone="${course.tone}" title="${escapeHtml(course.label)}"${compact ? ` aria-label="${escapeHtml(course.label)}"` : ""}><span class="course-dot" aria-hidden="true"></span><span class="course-name">${escapeHtml(compact ? course.shortLabel : course.label)}</span></span>`;
}

export function renderCourseDot(name) {
  const course = courseIdentity(name);
  return `<span class="course-dot" data-course-tone="${course.tone}" aria-hidden="true"></span>`;
}

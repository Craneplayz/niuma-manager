import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { courseIdentity, renderCourseBadge, renderCourseDot, syncCourseColors } from "../src/course-identity.js";

function memoryStorage() {
  const values = new Map();
  return { values, getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}

test("eight active courses receive different colors and retain them when courses are added or reordered", () => {
  const storage = memoryStorage();
  const names = Array.from({ length: 8 }, (_, i) => `课程 ${i}`);
  syncCourseColors(names, { scope: "eight-courses", storage });
  const before = names.map((name) => courseIdentity(name).tone);
  assert.equal(new Set(before).size, 8);
  syncCourseColors(["新增课程", ...names.toReversed()], { scope: "eight-courses", storage });
  assert.deepEqual(names.map((name) => courseIdentity(name).tone), before);
  assert.ok(Number.isInteger(courseIdentity("新增课程").tone));
  const saved = [...storage.values.values()][0];
  for (const name of [...names, "新增课程"]) assert.ok(!saved.includes(name));
});

test("a fresh module restores persisted colors without reassigning existing courses", async () => {
  const storage = memoryStorage();
  const names = ["概率论", "线性代数", "数据结构"];
  syncCourseColors(names, { scope: "restore", storage });
  const before = names.map((name) => courseIdentity(name).tone);
  const fresh = await import(`../src/course-identity.js?restore=${Date.now()}`);
  fresh.syncCourseColors(["新课", ...names.toReversed()], { scope: "restore", storage });
  assert.deepEqual(names.map((name) => fresh.courseIdentity(name).tone), before);
});

test("account scopes retain independent visual preferences", () => {
  const storage = memoryStorage();
  syncCourseColors(["课程甲", "课程乙"], { scope: "account-one", storage });
  const accountOne = courseIdentity("课程甲").tone;
  const firstEntry = [...storage.values.entries()][0];
  syncCourseColors(["课程丙"], { scope: "account-two", storage });
  assert.equal(storage.values.size, 2);
  assert.equal(storage.values.get(firstEntry[0]), firstEntry[1]);
  assert.ok(![...storage.values.keys()].some((key) => key.includes("account")));
  syncCourseColors(["课程甲"], { scope: "account-one", storage });
  assert.equal(courseIdentity("课程甲").tone, accountOne);
});

test("blocked preference access does not interrupt rendering or lose in-memory assignments", () => {
  const storage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  syncCourseColors(["课程一", "课程二"], { scope: "blocked", storage });
  const before = courseIdentity("课程一").tone;
  assert.doesNotThrow(() => syncCourseColors(["新课程", "课程一"], { scope: "blocked", storage }));
  assert.equal(courseIdentity("课程一").tone, before);
  assert.doesNotThrow(() => renderCourseBadge("课程一"));
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  try {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("security"); } });
    assert.doesNotThrow(() => syncCourseColors(["本地课程"], { scope: "blocked-getter" }));
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  }
});

test("course identity normalizes case, width and spacing while preserving the displayed name", () => {
  syncCourseColors([" ＡＲＣＨ　Lab "], { scope: "normalize", storage: null });
  const original = courseIdentity(" ＡＲＣＨ　Lab ");
  const normalized = courseIdentity("arch Lab");
  assert.equal(original.key, normalized.key);
  assert.equal(original.tone, normalized.tone);
  assert.equal(original.label, "ＡＲＣＨ Lab");
  assert.equal(courseIdentity("\t \n").tone, "neutral");
  assert.equal(courseIdentity(null).label, "未分类");
});

test("all course renderers share the same assignment and escape untrusted names", () => {
  const unsafe = '<img src=x onerror="alert(1)"> & \'课程\'';
  syncCourseColors([unsafe], { scope: "safe-render", storage: null });
  const tone = courseIdentity(unsafe).tone;
  for (const html of [renderCourseBadge(unsafe), renderCourseBadge(unsafe, { compact: true }), renderCourseDot(unsafe)]) {
    assert.match(html, new RegExp(`data-course-tone="${tone}"`));
    assert.ok(!html.includes("<img"));
  }
  const markup = renderCourseBadge(unsafe, { className: 'safe-class " onclick="bad()' });
  assert.match(markup, /&lt;img/);
  assert.match(markup, /&quot;/);
  assert.ok(!markup.includes('onclick="bad()'));
  assert.match(renderCourseBadge("数据结构", { compact: true }), />数构<\/span>/);
});

test("invalid stored assignments are ignored without poisoning valid entries", () => {
  const storage = memoryStorage();
  syncCourseColors(["有效课程"], { scope: "invalid-storage", storage });
  const [storageKey, serialized] = [...storage.values][0];
  const validTone = courseIdentity("有效课程").tone;
  const payload = JSON.parse(serialized);
  payload.colors.__proto__ = { polluted: true };
  payload.colors.invalid = 999;
  payload.colors.abcdef1234567890 = "2";
  storage.values.set(storageKey, JSON.stringify(payload));
  assert.doesNotThrow(() => syncCourseColors(["有效课程", "新增"], { scope: "invalid-storage", storage }));
  assert.equal(courseIdentity("有效课程").tone, validTone);
  assert.equal({}.polluted, undefined);
});

test("each course label palette keeps readable text contrast", async () => {
  const css = await readFile(new URL("../src/course-identity.css", import.meta.url), "utf8");
  const luminance = (hex) => hex.match(/[a-f\d]{2}/gi).map((value) => parseInt(value, 16) / 255)
    .map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  const palettes = [...css.matchAll(/--course-ink:\s*(#[a-f\d]{6});\s*--course-bg:\s*(#[a-f\d]{6});/g)];
  assert.equal(palettes.length, 9);
  for (const [, ink, background] of palettes) {
    const first = luminance(ink), second = luminance(background);
    assert.ok((Math.max(first, second) + .05) / (Math.min(first, second) + .05) >= 4.5, `${ink} on ${background}`);
  }
});

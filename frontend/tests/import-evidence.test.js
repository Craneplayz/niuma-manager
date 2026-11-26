import test from "node:test";
import assert from "node:assert/strict";
import {
  failureSourcesMarkup,
} from "../src/import-evidence.js";

const record = (input_id, overrides = {}) => ({
  input_id,
  text: "请提交报告。\n格式要求：PDF，附件中还有完整评分标准。",
  source_label: "第二次作业通知",
  source_url: "https://course.example.test/notice/2",
  platform: "teaching_web",
  course_or_project: "概率论",
  ...overrides,
});
test("partial failures show only affected source identities and safe links", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("ok", { source_label: "已成功通知", course_or_project: "成功课程" }), record('failed-<1>', { source_label: "原通知 <报告>", course_or_project: "概率 & 统计" })] },
    result: {
      status: "partial_success",
      errors: [{ input_id: 'failed-<1>', message: "private-token /private/backend/path" }],
      record_results: [{ input_id: "ok", status: "success" }, { input_id: 'failed-<1>', status: "failed" }],
    },
  });
  assert.match(html, /概率 &amp; 统计 · 原通知 &lt;报告&gt;/);
  assert.match(html, /通知标识 <code>failed-&lt;1&gt;<\/code>/);
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.doesNotMatch(html, /已成功通知|成功课程|private-token|private\/backend/);
});

test("incomplete source rows retain course, notification identity, and URL", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("incomplete")] },
    result: { status: "partial_success", errors: [{ input_id: "incomplete", code: "incomplete_source" }], record_results: [{ input_id: "incomplete", status: "failed" }] },
  });
  assert.match(html, /概率论 · 第二次作业通知/);
  assert.match(html, /<code>incomplete<\/code>/);
  assert.match(html, /href="https:\/\/course\.example\.test\/notice\/2"/);
});

test("batch failures without row IDs identify every original notification", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("first"), record("second", { source_label: "另一份通知" })] },
    result: { status: "failed", errors: [{ input_id: null, message: "internal exception" }], record_results: [] },
  });
  assert.match(html, /<code>first<\/code>/);
  assert.match(html, /另一份通知/);
  assert.match(html, /<code>second<\/code>/);
  assert.doesNotMatch(html, /internal exception/);
});

test("unknown failed IDs use candidate source identity without leaking error objects", () => {
  const html = failureSourcesMarkup({
    request: { records: [] },
    result: { status: "failed", errors: [{ input_id: "unknown", message: "API_KEY=private" }], tasks: [] },
    items: [{ candidate: { input_id: "unknown", course_or_project: "算法", sources: [{ input_id: "unknown", label: "来源通知", url: "https://course.example.test/another", platform: "teaching_web" }] } }],
  });
  assert.match(html, /算法 · 来源通知/);
  assert.match(html, /<code>unknown<\/code>/);
  assert.match(html, /打开教学网原页面/);
  assert.doesNotMatch(html, /API_KEY|private|\[object Object\]/);
  assert.match(failureSourcesMarkup({ result: { status: "failed", errors: [{ input_id: "missing" }] } }), /通知<\/p>.*<code>missing<\/code>/);
});

test("fallback names are concise and ignore reference paths and upstream metadata", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("fallback", {
      source_label: null,
      source_url: "file:///private/secret.json",
      text: "\n" + "通知".repeat(100) + "\n正文不应显示在失败身份卡",
      upstream_hints: { references: [{ file_ref: "/private/hidden.pdf" }], password: "private-password" },
    })] },
    result: { status: "failed" },
  });
  assert.ok(html.includes("通知".repeat(60)));
  assert.ok(!html.includes("通知".repeat(61)));
  assert.doesNotMatch(html, /正文不应|private|hidden\.pdf|<a /);
});

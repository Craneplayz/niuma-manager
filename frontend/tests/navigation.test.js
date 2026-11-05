import test from "node:test";
import assert from "node:assert/strict";
import { readNavigation, navigationURL } from "../src/navigation.js";

const options = { today: "2026-10-18", preferences: { view: "calendar", mode: "week" } };
const read = search => readNavigation(new URLSearchParams(search), options);

test("legacy links use preferences, while explicit list/month links override them", () => {
  assert.equal(read("view=calendar").view, "calendar");
  assert.equal(read("").view, "calendar");
  assert.equal(read("view=list&mode=month").view, "list");
  assert.equal(read("view=list&mode=month").mode, "month");
  assert.equal(read("").selectedDate, "2026-10-18");
});

test("unknown navigation values and impossible dates fall back safely", () => {
  const navigation = read("page=admin&scope=anything&view=bad&mode=bad&pane=bad&importSource=unsupported&anchor=2026-02-30&date=<script>");
  assert.deepEqual(navigation, {
    page: "tasks", scope: "all", view: "calendar", mode: "week", pane: "day",
    importSource: "text", anchor: "2026-10-18", selectedDate: "2026-10-18",
  });
  assert.equal(read("date=2024-02-29").selectedDate, "2024-02-29");
  assert.equal(read("date=2025-02-29").selectedDate, "2026-10-18");
});

test("navigation round trips without leaking filters, credentials or form drafts", () => {
  const navigation = read("page=import&scope=done&view=list&mode=month&anchor=2026-12-01&date=2026-12-24&pane=undated&importSource=teaching");
  const url = navigationURL("https://example.test/?demo=1&other=kept#main", {
    ...navigation, query: "private search", text: "private notice", password: "private password",
  });
  assert.deepEqual(readNavigation(url.searchParams, { today: "2027-01-01" }), navigation);
  assert.equal(url.searchParams.get("demo"), "1");
  assert.equal(url.searchParams.get("other"), "kept");
  assert.equal(url.hash, "#main");
  assert.doesNotMatch(url.href, /private|query|password|text=/);
  assert.equal(navigationURL(url, navigation).href, url.href);
});

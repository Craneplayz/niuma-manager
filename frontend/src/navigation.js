import { shiftDay, todayKey } from "./task-model.js";

const choices = {
  page: ["today", "courses", "planning", "tasks", "import"],
  scope: ["all", "notices", "near", "done"],
  view: ["list", "calendar"],
  mode: ["month", "week"],
  pane: ["day", "undated"],
  importSource: ["text", "teaching", "files"],
};
const choice = (key, value, fallback) => choices[key].includes(value) ? value : fallback;
const date = (value, fallback) => shiftDay(value, 0) || fallback;

// Only navigation belongs in the URL; task filters and private drafts stay in memory.
export function readNavigation(params, { today = todayKey(), preferences = {} } = {}) {
  const taskLink = ["page", "view", "scope", "anchor", "date", "mode", "pane", "demo"].some(key => params.has(key));
  return {
    page: choice("page", params.get("page"), taskLink ? "tasks" : "today"),
    scope: choice("scope", params.get("scope"), "all"),
    view: choice("view", params.get("view"), choice("view", preferences?.view, "list")),
    mode: choice("mode", params.get("mode"), choice("mode", preferences?.mode, "month")),
    anchor: date(params.get("anchor"), today),
    selectedDate: date(params.get("date"), today),
    pane: choice("pane", params.get("pane"), "day"),
    importSource: choice("importSource", params.get("importSource"), "text"),
  };
}

export function navigationURL(href, navigation) {
  const url = new URL(href);
  for (const key of Object.keys(choices)) url.searchParams.set(key, navigation[key]);
  url.searchParams.set("anchor", navigation.anchor);
  url.searchParams.set("date", navigation.selectedDate);
  url.searchParams.delete("activity");
  return url;
}

const paths = {
  sidebar: "M4 4h16v16H4zM9 4v16M6 8h.1M6 12h.1",
  filter: "M4 6h16M7 12h10M10 18h4",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  tasks: "M9 6h12M9 12h12M9 18h12M3 6h1M3 12h1M3 18h1",
  calendar: "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16",
  plus: "M12 5v14M5 12h14",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14M15 15l6 6",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v5l3 2",
  check: "M5 12l4 4L19 6",
  chevron: "M9 5l7 7-7 7",
  close: "M6 6l12 12M6 18L18 6",
  cow: "M3 4l5 5h8l5-5M5 11v7l7 4 7-4v-7M9 14h.1M15 14h.1",
};

export const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.tasks}"/></svg>`;

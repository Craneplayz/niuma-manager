import { DateTime } from "luxon";
import { cleanText } from "./utils.js";

export const DEFAULT_TIMEZONE = "Asia/Shanghai";

export interface ParsedTime {
  start: string | null;
  end: string | null;
  allDay: boolean;
  resolution: "exact" | "context_inferred" | "unresolved";
  warnings: string[];
}

interface DateParts { year?: number; month: number; day: number; explicitYear: boolean; }
interface Clock { hour: number; minute: number; meridiem?: "am" | "pm"; }

export function parseEventTime(rawValue: string | undefined, publicationRaw?: string): ParsedTime {
  const raw = cleanText(rawValue)?.replace(/[–—−]/g, "-").replace(/至|到/g, "-");
  if (!raw) return unresolved("missing event time");

  const publication = parseDateParts(publicationRaw);
  const parsedDate = parseDateParts(raw);
  let date = parsedDate;
  const resolution: ParsedTime["resolution"] = parsedDate?.explicitYear ? "exact" : "context_inferred";
  if (!date) return unresolved("event date is missing or unsupported");
  if (!date.explicitYear) {
    const year = inferNearestYear(date, publication);
    if (!year) return unresolved("event year is missing and no valid publication-date context is available");
    date = { ...date, year };
  }
  if (!date.year) return unresolved("event year could not be resolved");

  const timeText = removeDateText(raw);
  const clocks = parseClocks(timeText);
  if (!clocks) return unresolved("event clock is invalid or unsupported");
  if (!clocks.length) {
    const start = makeDateTime(date, { hour: 0, minute: 0 });
    return start ? { start, end: null, allDay: true, resolution, warnings: [] } : unresolved("invalid event date");
  }
  const first = applyMeridiem(clocks[0], clocks.length > 1 ? clocks[1] : undefined, false);
  const second = clocks.length > 1 ? applyMeridiem(clocks[1], first, true) : undefined;
  const start = makeDateTime(date, first);
  const end = second ? makeDateTime(date, second) : null;
  if (!start) return unresolved("invalid event date or time");
  if (end && DateTime.fromISO(end).toMillis() < DateTime.fromISO(start).toMillis()) {
    return { start, end: null, allDay: false, resolution, warnings: ["end time precedes start time; end time was omitted"] };
  }
  return { start, end, allDay: false, resolution, warnings: [] };
}

export function parsePublicationTime(rawValue: string | undefined): string | null {
  const parsed = parseEventTime(rawValue);
  return parsed.start;
}

function unresolved(warning: string): ParsedTime {
  return { start: null, end: null, allDay: false, resolution: "unresolved", warnings: [warning] };
}

function parseDateParts(value: string | undefined): DateParts | null {
  const raw = cleanText(value);
  if (!raw) return null;
  let match = raw.match(/(?<!\d)(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*(?:日)?/);
  if (match) return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]), explicitYear: true };
  match = raw.match(/\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+([A-Za-z]+)\s+(\d{1,2}),\s*(20\d{2})\b/i);
  if (match) {
    const month = monthNumber(match[1]);
    if (month) return { year: Number(match[3]), month, day: Number(match[2]), explicitYear: true };
  }
  match = raw.match(/\b([A-Za-z]+)\s+(\d{1,2}),?\s*(20\d{2})?\b/i);
  if (match) {
    const month = monthNumber(match[1]);
    if (month) return { year: match[3] ? Number(match[3]) : undefined, month, day: Number(match[2]), explicitYear: Boolean(match[3]) };
  }
  match = raw.match(/(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?/);
  if (match) return { month: Number(match[1]), day: Number(match[2]), explicitYear: false };
  match = raw.match(/(?<!\d)(20\d{2})\s*[/-](\d{1,2})\s*[/-](\d{1,2})(?!\d)/);
  if (match) return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]), explicitYear: true };
  return null;
}

function removeDateText(value: string): string {
  return value
    .replace(/(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+[A-Za-z]+\s+\d{1,2},?\s*20\d{2}/gi, " ")
    .replace(/[A-Za-z]+\s+\d{1,2},?\s*20\d{2}/gi, " ")
    .replace(/20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?/g, " ")
    .replace(/\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*20\d{2}/g, " ")
    .replace(/\d{1,2}\s*月\s*\d{1,2}\s*日?/g, " ")
}

/** Require clock syntax; bare numbers (assignment IDs, rooms) are not times. */
function parseClocks(value: string): Clock[] | null {
  const result: Clock[] = [];
  const regex = /(?<![\d:：])(上午|下午|am\b|pm\b)?\s*(\d{1,2})(?:\s*[:：]\s*(\d{2})(?!\d)|\s*[点时](?:\s*(\d{1,2})\s*分?)?(?![\d半刻])|\s*(am|pm)\b)\s*(am\b|pm\b)?/gi;
  const remainder = value.replace(regex, (_match, prefix: string | undefined, hourRaw: string, colonMinute: string | undefined, chineseMinute: string | undefined, suffix: string | undefined, trailing: string | undefined) => {
    const hour = Number(hourRaw);
    const minute = Number(colonMinute ?? chineseMinute ?? 0);
    const meridiemRaw = (prefix || suffix || trailing || "").toLowerCase();
    const meridiem = meridiemRaw === "上午" || meridiemRaw === "am" ? "am" : meridiemRaw === "下午" || meridiemRaw === "pm" ? "pm" : undefined;
    result.push({ hour, minute, meridiem });
    return " ";
  });
  // Do not silently convert malformed clocks to all-day dates or truncate a range.
  if (/\d\s*[:：点时]|(?:上午|下午)\s*\d|\d\s*(?:am|pm)\b/i.test(remainder)
    || result.length > 2 || result.some(({ hour, minute }) => hour > 23 || minute > 59)) return null;
  return result;
}

/** Choose the closest calendar date in the adjacent years, retaining inferred resolution. */
function inferNearestYear(date: DateParts, context: DateParts | null): number | null {
  if (!context?.year) return null;
  const reference = DateTime.fromObject({ year: context.year, month: context.month, day: context.day }, { zone: DEFAULT_TIMEZONE });
  if (!reference.isValid) return null;
  const candidates = [context.year, context.year - 1, context.year + 1]
    .map((year) => DateTime.fromObject({ year, month: date.month, day: date.day }, { zone: DEFAULT_TIMEZONE }))
    .filter((candidate) => candidate.isValid)
    .sort((a, b) => Math.abs(a.toMillis() - reference.toMillis()) - Math.abs(b.toMillis() - reference.toMillis()));
  return candidates[0]?.year ?? null;
}

function applyMeridiem(clock: Clock, context?: Clock, isRangeEnd = false): Clock {
  const meridiem = clock.meridiem ?? context?.meridiem;
  let hour = clock.hour;
  if (meridiem === "pm" && hour < 12) hour += 12;
  if (meridiem === "am" && hour === 12 && !isRangeEnd) hour = 0;
  return { ...clock, hour };
}

function makeDateTime(date: DateParts, clock: Clock): string | null {
  const dt = DateTime.fromObject({ year: date.year, month: date.month, day: date.day, hour: clock.hour, minute: clock.minute }, { zone: DEFAULT_TIMEZONE });
  return dt.isValid ? dt.toISO({ suppressMilliseconds: true }) : null;
}

function monthNumber(name: string): number | null {
  const months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
  const index = months.findIndex((month) => month.startsWith(name.toLowerCase()));
  return index >= 0 ? index + 1 : null;
}

import { DateTime } from "luxon";
import { DEFAULT_TIMEZONE } from "./time.js";
import type { NormalizedEvent } from "./schemas.js";

export interface FutureEventFilterResult {
  events: NormalizedEvent[];
  pastEvents: number;
  unresolvedEvents: number;
}

export function resolveTimeFilterAsOf(rawValue?: string, fallback = DateTime.now().setZone(DEFAULT_TIMEZONE)): DateTime<boolean> {
  if (!rawValue) return fallback.setZone(DEFAULT_TIMEZONE);
  const parsed = DateTime.fromISO(rawValue, { setZone: true });
  if (!parsed.isValid) throw new Error(`invalid time-filter as-of value: ${rawValue}`);
  return parsed.setZone(DEFAULT_TIMEZONE);
}

/** Keeps only events whose start time is strictly later than the filter instant. */
export function filterFutureEvents(events: NormalizedEvent[], asOf: DateTime<boolean> = DateTime.now().setZone(DEFAULT_TIMEZONE)): FutureEventFilterResult {
  const future: NormalizedEvent[] = [];
  let pastEvents = 0;
  let unresolvedEvents = 0;
  for (const event of events) {
    if (!event.start_time) {
      unresolvedEvents += 1;
      continue;
    }
    const start = DateTime.fromISO(event.start_time).setZone(DEFAULT_TIMEZONE);
    if (!start.isValid) {
      unresolvedEvents += 1;
    } else if (start > asOf) {
      future.push(event);
    } else {
      pastEvents += 1;
    }
  }
  return { events: future, pastEvents, unresolvedEvents };
}

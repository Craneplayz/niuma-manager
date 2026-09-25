import { normalizedEventSchema, type NormalizedEvent } from "./core/schemas.js";
import type { EventCandidate } from "./core/types.js";
import { parseEventTime, parsePublicationTime, DEFAULT_TIMEZONE } from "./core/time.js";
import { canonicalizeUrl, cleanText, sha256, splitPeople } from "./core/utils.js";

export interface NormalizationResult { event: NormalizedEvent | null; warnings: string[]; }

export function normalizeCandidate(candidate: EventCandidate): NormalizationResult {
  const title = cleanText(candidate.title_raw)?.replace(/\s+/g, " ").trim();
  if (!title) return { event: null, warnings: ["title is empty"] };
  const parsedTime = parseEventTime(candidate.time_raw, candidate.publication_time_raw);
  const publication = parsePublicationTime(candidate.publication_time_raw);
  const warnings = [...parsedTime.warnings];
  const timeResolution = parsedTime.resolution;
  if (!candidate.time_raw) warnings.push("event time is missing");
  const sourceUrl = safeUrl(candidate.source_url);
  if (!sourceUrl) return { event: null, warnings: [...warnings, "source URL is invalid"] };
  const eventType = mapEventType(candidate.event_type_raw, title);
  const sourceItemId = cleanText(candidate.source_item_id) ?? null;
  const id = `evt_${sha256(`${candidate.source_id}\n${sourceItemId ?? sourceUrl}`).slice(0, 24)}`;
  const event: NormalizedEvent = {
    id,
    title,
    description: cleanText(candidate.description_raw) ?? null,
    start_time: parsedTime.start,
    end_time: parsedTime.end,
    timezone: DEFAULT_TIMEZONE,
    location: cleanText(candidate.location_raw) ?? null,
    organizers: splitPeople(candidate.organizer_raw),
    speakers: splitPeople(candidate.speaker_raw),
    speaker_affiliation: cleanText(candidate.speaker_affiliation_raw) ?? null,
    host: cleanText(candidate.host_raw) ?? null,
    moderator: cleanText(candidate.moderator_raw) ?? null,
    registration_url: safeUrl(candidate.registration_url),
    online_url: safeUrl(candidate.online_url),
    contact: cleanText(candidate.contact_raw) ?? null,
    language: cleanText(candidate.language_raw) ?? null,
    tags: (candidate.tags_raw ?? []).map((tag) => cleanText(tag)).filter((tag): tag is string => Boolean(tag)),
    cancellation_status: cleanText(candidate.cancellation_status_raw) ?? null,
    all_day: parsedTime.allDay,
    event_type: eventType,
    event_type_raw: cleanText(candidate.event_type_raw) ?? null,
    publication_time: publication,
    source: { source_id: candidate.source_id, source_url: sourceUrl, source_item_id: sourceItemId },
    raw: {
      time: cleanText(candidate.time_raw) ?? null,
      location: cleanText(candidate.location_raw) ?? null,
      organizer: cleanText(candidate.organizer_raw) ?? null,
      speaker: cleanText(candidate.speaker_raw) ?? null,
    },
    parse: {
      status: parsedTime.start ? (warnings.length ? "partial" : "ok") : "partial",
      warnings,
      time_resolution: timeResolution,
    },
  };
  const validation = normalizedEventSchema.safeParse(event);
  return validation.success ? { event: validation.data, warnings } : { event: null, warnings: [...warnings, validation.error.message] };
}

function safeUrl(value: string | undefined): string | null {
  if (!value) return null;
  try { return canonicalizeUrl(value); } catch { return null; }
}

function mapEventType(raw: string | undefined, title: string): NormalizedEvent["event_type"] {
  const value = `${raw ?? ""} ${title}`.toLowerCase();
  if (/讲座|lecture|talk|报告/.test(value)) return "lecture";
  if (/研讨|seminar|colloquium/.test(value)) return "seminar";
  if (/workshop|工作坊|培训/.test(value)) return "workshop";
  if (/conference|会议|大会/.test(value)) return "conference";
  if (/论坛|forum/.test(value)) return "forum";
  if (/沙龙|salon/.test(value)) return "salon";
  if (/其他|other/.test(value)) return "other";
  return "unknown";
}

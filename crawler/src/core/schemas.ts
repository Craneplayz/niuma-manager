import { z } from "zod";

export const normalizedEventSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  description: z.string().nullable(),
  start_time: z.string().datetime({ offset: true }).nullable(),
  end_time: z.string().datetime({ offset: true }).nullable(),
  timezone: z.string().min(1),
  location: z.string().nullable(),
  organizers: z.array(z.string()),
  speakers: z.array(z.string()),
  speaker_affiliation: z.string().nullable(),
  host: z.string().nullable(),
  moderator: z.string().nullable(),
  registration_url: z.string().url().nullable(),
  online_url: z.string().url().nullable(),
  contact: z.string().nullable(),
  language: z.string().nullable(),
  tags: z.array(z.string()),
  cancellation_status: z.string().nullable(),
  all_day: z.boolean(),
  event_type: z.enum(["lecture", "seminar", "workshop", "conference", "forum", "salon", "other", "unknown"]),
  event_type_raw: z.string().nullable(),
  publication_time: z.string().datetime({ offset: true }).nullable(),
  source: z.object({
    source_id: z.string().min(1),
    source_url: z.string().url(),
    source_item_id: z.string().nullable(),
  }),
  raw: z.object({
    time: z.string().nullable(),
    location: z.string().nullable(),
    organizer: z.string().nullable(),
    speaker: z.string().nullable(),
  }),
  parse: z.object({
    status: z.enum(["ok", "partial", "failed"]),
    warnings: z.array(z.string()),
    time_resolution: z.enum(["exact", "context_inferred", "unresolved"]),
  }),
});

export type NormalizedEvent = z.infer<typeof normalizedEventSchema>;

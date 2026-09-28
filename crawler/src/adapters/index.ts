import type { SourceAdapter } from "../core/types.js";
import { bioAdapter } from "./bio.js";
import { csAdapter } from "./cs.js";
import { gseAdapter } from "./gse.js";

export const adapters: Record<string, SourceAdapter> = {
  cs: csAdapter,
  bio: bioAdapter,
  gse: gseAdapter,
};

export const allAdapters = Object.values(adapters);

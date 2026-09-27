import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { RawDocument } from "../core/types.js";
import { sha256 } from "../core/utils.js";

export interface StoragePolicy { persistRaw: boolean; }

export class RawSnapshotStore {
  constructor(private readonly rootDir = "data/raw", private readonly policy: StoragePolicy = { persistRaw: true }) {}

  async persist(document: RawDocument): Promise<string | null> {
    if (!this.policy.persistRaw) return null;
    const directory = join(this.rootDir, document.sourceId, document.crawlRunId);
    await mkdir(directory, { recursive: true });
    const stem = `${sha256(document.requestedUrl).slice(0, 16)}-${document.contentHash.slice(0, 16)}`;
    const htmlPath = join(directory, `${stem}.html`);
    const metadataPath = join(directory, `${stem}.json`);
    await writeFile(htmlPath, document.body, "utf8");
    await writeFile(metadataPath, JSON.stringify({
      source_id: document.sourceId,
      crawl_run_id: document.crawlRunId,
      requested_url: document.requestedUrl,
      final_url: document.finalUrl,
      discovered_from_url: document.discoveredFromUrl ?? null,
      fetched_at: document.fetchedAt,
      status: document.status,
      status_text: document.statusText,
      headers: document.headers,
      content_type: document.contentType,
      content_hash: document.contentHash,
      duration_ms: document.durationMs,
      raw_html_file: htmlPath,
    }, null, 2), "utf8");
    return directory;
  }
}

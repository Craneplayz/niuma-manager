/** Bounded product entry for the existing cs/bio/gse public adapters. */
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import pino from "pino";
import { adapters } from "./adapters/index.js";
import { crawlSources } from "./crawler.js";
import { PublicHttpFetcher } from "./fetcher/public-http-fetcher.js";

async function main(): Promise<void> {
  const [key, directory] = process.argv.slice(2);
  if (!key || !Object.hasOwn(adapters, key) || !directory) throw new Error("unknown public source");
  const selected = adapters[key];
  const fetcher = new PublicHttpFetcher({ timeoutMs: 8000, maxRetries: 0, maxResponseBytes: 2 * 1024 * 1024,
    fetchImpl: (input, init) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      if (!selected.metadata.allowedOrigins.includes(url.origin)) throw new Error("redirect outside configured public source");
      // Robots requests otherwise follow redirects inside fetch, bypassing this
      // per-request origin check. The general fetcher follows page redirects itself.
      return fetch(input, { ...init, redirect: "manual" });
    },
  });
  const summary = await crawlSources([{ ...selected, metadata: { ...selected.metadata, maxPages: 6 } }], fetcher, {
    logger: pino({ enabled: false }), rawRoot: resolve(directory, "raw"),
    normalizedRoot: resolve(directory, "normalized"), extractedRoot: resolve(directory, "extracted"),
  });
  const data = await readFile(summary.normalizedOutputLocation, "utf8");
  const events = data.split("\n").filter(Boolean).map(line => JSON.parse(line)).slice(0, 100).map(event => ({
    id: event.id, title: event.title.slice(0, 512), description: (event.description || "").slice(0, 20000),
    start_time: event.start_time, end_time: event.end_time, location: event.location?.slice(0, 512) || null,
    url: event.source.source_url, source_id: key,
  }));
  process.stdout.write(JSON.stringify({contract_version: "public-import/0.1", events, pages_failed: summary.pagesFailed}));
}
main().catch(() => { process.stderr.write("Public source collection failed\n"); process.exitCode = 1; });

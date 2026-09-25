import { mkdir } from "node:fs/promises";
import { adapters, allAdapters } from "./adapters/index.js";
import { crawlSources, reprocessRun } from "./crawler.js";
import { PublicHttpFetcher } from "./fetcher/public-http-fetcher.js";
import { createLogger } from "./logger.js";
import { writeReviewReport } from "./review-report.js";

const [command, target] = process.argv.slice(2);
const asOfArgument = process.argv.find((argument) => argument.startsWith("--as-of="));
const timeFilterAsOf = asOfArgument?.slice("--as-of=".length);

async function main(): Promise<void> {
  if (command === "crawl") {
    const selected = target === "all" || !target ? allAdapters : target.split(",").map((key) => adapters[key]).filter(Boolean);
    if (!selected.length) throw new Error(`unknown source: ${target}`);
    const runId = `${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}-${Math.random().toString(36).slice(2, 8)}`;
    await mkdir("logs", { recursive: true });
    const logger = await createLogger(`logs/${runId}.jsonl`, runId);
    const summary = await crawlSources(selected, new PublicHttpFetcher(), { runId, logger, logPath: `logs/${runId}.jsonl`, timeFilterAsOf });
    printSummary(summary);
    logger.flush();
    return;
  }
  if (command === "reprocess" && target) {
    const logger = await createLogger(`logs/${target}-reprocessed.jsonl`, target);
    const summary = await reprocessRun(target, allAdapters, { logger, logPath: `logs/${target}-reprocessed.jsonl`, timeFilterAsOf });
    printSummary(summary);
    logger.flush();
    return;
  }
  if (command === "review" && target) {
    const report = await writeReviewReport(target);
    console.log(JSON.stringify({ "review report": report.outputPath, "events displayed": report.eventCount }, null, 2));
    return;
  }
  console.error("Usage: pnpm crawler crawl all|cs|bio|gse|cs,bio [--as-of=ISO], pnpm crawler reprocess <run-id> [--as-of=ISO], or pnpm crawler review <run-id>");
  process.exitCode = 2;
}

function printSummary(summary: Awaited<ReturnType<typeof crawlSources>>): void {
  console.log(JSON.stringify({
    "crawl run ID": summary.runId,
    "sources attempted": summary.sourcesAttempted,
    "sources completed": summary.sourcesCompleted,
    "pages requested": summary.pagesRequested,
    "pages fetched": summary.pagesFetched,
    "pages failed": summary.pagesFailed,
    "event candidates found": summary.candidatesFound,
    "normalized events produced": summary.normalizedEventsProduced,
    "time filter as of": summary.timeFilterAsOf,
    "past events filtered": summary.pastEventsFiltered,
    "unresolved events filtered": summary.unresolvedEventsFiltered,
    "validation failures": summary.validationFailures,
    "duplicates removed": summary.duplicatesRemoved,
    "raw snapshot location": summary.rawSnapshotLocation,
    "normalized output location": summary.normalizedOutputLocation,
    "log location": summary.logLocation,
    failures: summary.failures.slice(0, 10),
  }, null, 2));
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

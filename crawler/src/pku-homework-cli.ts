import { scrapePkuHomeworks } from "./pku-homework.js";
import { toRecognitionRequests } from "./pku-recognition.js";
import { toTeachingRequests, toTeachingCaptures } from "./pku-teaching.js";
import { toTeachingRequests as exportEvidence } from "./pku-teaching-evidence.js";

try {
  const args = process.argv.slice(2).filter((arg) => arg !== "--");
  if (args.includes("--help")) {
    console.log("pku:homework [--term=LABEL | --all-terms] [--all-sections] [--format=raw|recognition|teaching-v04|teaching|teaching-capture] [--request-id=ID]\nBy default only assignment sections are collected; --all-sections also collects course announcements and every content section.\nraw: JSON with all source_records and legacy homework views.\nrecognition: JSONL, one v0.2 request per line (up to 100 records and 2,000,000 UTF-8 bytes); exports only, does not call a model.");
  } else {
    if (args.some((arg) => !["--all-terms", "--all-sections"].includes(arg) && !/^--(?:term|format|request-id)=.+$/.test(arg))) throw new Error("unknown or empty argument; use --help");
    const termArg = args.find((arg) => arg.startsWith("--term="));
    if (termArg && args.includes("--all-terms")) throw new Error("--term and --all-terms cannot be combined");
    const term = args.includes("--all-terms") ? null : termArg?.slice("--term=".length);
    const format = args.find((arg) => arg.startsWith("--format="))?.slice("--format=".length) ?? "raw";
    if (!["raw", "recognition", "teaching-v04", "teaching", "teaching-capture"].includes(format)) throw new Error("format must be raw, recognition, teaching-v04, teaching or teaching-capture");
    const requestId = args.find((arg) => arg.startsWith("--request-id="))?.slice("--request-id=".length);
    const result = await scrapePkuHomeworks({ term, allSections: args.includes("--all-sections"), ...(["teaching", "teaching-capture"].includes(format) ? { teachingSections: true } : {}) });
    if (format === "teaching-v04") {
      const output = exportEvidence(result.source_records, requestId);
      for (const skipped of output.skipped) console.error(JSON.stringify({ type: "teaching_export_skipped", ...skipped }));
      if (output.requests.length) console.log(output.requests.map(request => JSON.stringify(request)).join("\n"));
      if (output.skipped.length) process.exitCode = 2;
    } else if (format !== "raw") {
      const requests = format === "teaching-capture" ? toTeachingCaptures(result.source_records, requestId)
        : format === "teaching" ? toTeachingRequests(result.source_records, requestId) : toRecognitionRequests(result.source_records, requestId);
      // Convert the entire run before writing anything, so a conversion error is explicit.
      console.log(requests.map((request) => JSON.stringify(request)).join("\n"));
    } else {
      console.log(JSON.stringify(result, null, 2));
    }
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}

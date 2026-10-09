import { scrapePkuHomeworks } from "./pku-homework.js";
import { toRecognitionRequests } from "./pku-recognition.js";
import { toTeachingRequests } from "./pku-teaching.js";

try {
  const args = process.argv.slice(2).filter((arg) => arg !== "--");
  if (args.includes("--help")) {
    console.log("pku:homework [--term=LABEL | --all-terms] [--all-sections] [--format=raw|recognition] [--request-id=ID]\nBy default only assignment sections are collected; --all-sections also collects course announcements and every content section.\nraw: JSON with all source_records and legacy homework views.\nrecognition: JSONL, one v0.2 request per line (up to 100 records and 2,000,000 UTF-8 bytes); exports only, does not call a model.");
  } else {
    if (args.some((arg) => !["--all-terms", "--all-sections"].includes(arg) && !/^--(?:term|format|request-id)=.+$/.test(arg))) throw new Error("unknown or empty argument; use --help");
    const termArg = args.find((arg) => arg.startsWith("--term="));
    if (termArg && args.includes("--all-terms")) throw new Error("--term and --all-terms cannot be combined");
    const term = args.includes("--all-terms") ? null : termArg?.slice("--term=".length);
    const format = args.find((arg) => arg.startsWith("--format="))?.slice("--format=".length) ?? "raw";
    if (!["raw", "recognition", "teaching"].includes(format)) throw new Error("format must be raw, recognition or teaching");
    const requestId = args.find((arg) => arg.startsWith("--request-id="))?.slice("--request-id=".length);
    const result = await scrapePkuHomeworks({ term, allSections: args.includes("--all-sections"), ...(format === "teaching" ? { teachingSections: true } : {}) });
    if (format === "recognition" || format === "teaching") {
      const requests = format === "teaching" ? toTeachingRequests(result.source_records, requestId) : toRecognitionRequests(result.source_records, requestId);
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

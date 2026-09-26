import { scrapePkuHomeworks } from "./pku-homework.js";
import { toRecognitionRequests } from "./pku-recognition.js";

try {
  const args = process.argv.slice(2).filter((arg) => arg !== "--");
  if (args.includes("--help")) {
    console.log("pku:homework [--term=LABEL | --all-terms] [--format=raw|recognition] [--request-id=ID]\nraw: JSON with all source_records and legacy homework views.\nrecognition: JSONL, one v0.2 request per line (up to 100 records); exports only, does not call a model.");
  } else {
    if (args.some((arg) => arg !== "--all-terms" && !/^--(?:term|format|request-id)=.+$/.test(arg))) throw new Error("unknown or empty argument; use --help");
    const termArg = args.find((arg) => arg.startsWith("--term="));
    if (termArg && args.includes("--all-terms")) throw new Error("--term and --all-terms cannot be combined");
    const term = args.includes("--all-terms") ? null : termArg?.slice("--term=".length);
    const format = args.find((arg) => arg.startsWith("--format="))?.slice("--format=".length) ?? "raw";
    if (format !== "raw" && format !== "recognition") throw new Error("format must be raw or recognition");
    const requestId = args.find((arg) => arg.startsWith("--request-id="))?.slice("--request-id=".length);
    const result = await scrapePkuHomeworks({ term });
    if (format === "recognition") {
      const requests = toRecognitionRequests(result.source_records, requestId);
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

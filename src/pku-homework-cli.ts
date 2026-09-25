import { scrapePkuHomeworks } from "./pku-homework.js";

const args = new Set(process.argv.slice(2));
const termArgument = process.argv.find((argument) => argument.startsWith("--term="));
const term = args.has("--all-terms") ? null : termArgument ? termArgument.slice("--term=".length) : undefined;

try {
  const result = await scrapePkuHomeworks({ term });
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}

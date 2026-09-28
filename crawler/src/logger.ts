import pino, { type Logger } from "pino";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";

export async function createLogger(path: string, runId: string): Promise<Logger> {
  await mkdir(dirname(path), { recursive: true });
  return pino({ base: { service: "niuma-crawler", run_id: runId } }, pino.destination(path));
}

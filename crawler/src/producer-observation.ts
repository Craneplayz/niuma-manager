import { performance } from "node:perf_hooks";

/** Shape defined by contracts/schemas/producer-observation-v0.1.schema.json. */
export interface DownloadObservation {
  contract_version: "producer-observation/0.1";
  stage: "attachment_download";
  duration_ms: number;
  result_code: "ok" | "download_failed";
}
export type DownloadObserver = (event: DownloadObservation) => void | Promise<void>;

/** Measures only one actual read, including redirects/body transfer, never parsing. */
export async function observeDownload<T>(read: () => Promise<T>, observer?: DownloadObserver): Promise<T> {
  const started = performance.now();
  let result: DownloadObservation["result_code"] = "download_failed";
  try {
    const value = await read();
    result = "ok";
    return value;
  } finally {
    const event: DownloadObservation = {
      contract_version: "producer-observation/0.1", stage: "attachment_download",
      duration_ms: Math.max(0, Math.round((performance.now() - started) * 1000) / 1000),
      result_code: result,
    };
    try {
      // Do not await an exporter; its latency/failure must not delay imports.
      const pending = observer?.(event);
      if (pending) void Promise.resolve(pending).catch(() => {});
    } catch { /* Optional diagnostics must not alter the read result. */ }
  }
}

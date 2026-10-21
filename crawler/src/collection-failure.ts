/** Closed, content-free failure envelope shared with contracts/collection_failure.py. */
export type CollectionStage = "credentials" | "browser" | "login" | "navigation" | "export" | "collection";
export type CollectionCode = "operation_failed" | "timeout" | "http_error" | "session_expired";
export class CollectionFailure extends Error {
  constructor(public readonly stage: CollectionStage, public readonly code: CollectionCode, message: string = code) {
    super(message);
  }
}
export async function collectionStage<T>(stage: CollectionStage, operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) {
    if (error instanceof CollectionFailure) throw error;
    throw new CollectionFailure(stage, error instanceof Error && error.name === "TimeoutError" ? "timeout" : "operation_failed");
  }
}
export function collectionFailureEvent(error: unknown) {
  const failure = error instanceof CollectionFailure ? error : new CollectionFailure("collection", "operation_failed");
  return { contract_version: "collection-failure/0.1", stage: failure.stage, code: failure.code };
}

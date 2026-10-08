import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const spawn = vi.hoisted(() => vi.fn());
vi.mock("node:child_process", () => ({ spawn }));
import { extractAttachmentText } from "../src/pku-attachments.js";

class Parser extends EventEmitter {
  stdin = new PassThrough();
  kill = vi.fn(() => { queueMicrotask(() => this.emit("close", null, "SIGTERM")); return true; });
}
const file = { bytes: new TextEncoder().encode("%PDF-1.4 synthetic"), contentType: "application/pdf" };
const parsed = { text: "Submit report.", partial: false };
let parser: Parser;
beforeEach(() => { parser = new Parser(); spawn.mockReset().mockReturnValue(parser); });
afterEach(() => { vi.useRealTimers(); });

describe("isolated PDF parser lifecycle", () => {
  it("waits for process cleanup and exit after receiving text", async () => {
    const done = vi.fn();
    const result = extractAttachmentText(file, "pdf").then(done);
    parser.emit("message", parsed);
    await Promise.resolve();
    expect(done).not.toHaveBeenCalled();
    expect(parser.kill).not.toHaveBeenCalled();
    parser.emit("close", 0);
    await result;
    expect(done).toHaveBeenCalledWith(parsed);
  });
  it("rejects a native crash even if text arrived before the crash", async () => {
    const result = extractAttachmentText(file, "pdf");
    const check = expect(result).rejects.toThrow("parse_failed");
    parser.emit("message", parsed);
    parser.emit("close", null, "SIGSEGV");
    await check;
    spawn.mockReturnValue(parser = new Parser());
    const next = extractAttachmentText(file, "pdf");
    parser.emit("message", parsed);
    parser.emit("close", 0);
    await expect(next).resolves.toEqual(parsed);
  });
  it("rejects an exit without a result rather than leaving the sync pending", async () => {
    const result = extractAttachmentText(file, "pdf");
    parser.emit("close", 0);
    await expect(result).rejects.toThrow("parse_failed");
  });
  it("handles a spawn failure and a closed input pipe without leaking errors", async () => {
    const result = extractAttachmentText(file, "pdf");
    parser.stdin.emit("error", new Error("synthetic EPIPE"));
    parser.emit("error", new Error("synthetic spawn failure"));
    await expect(result).rejects.toThrow("parse_failed");
  });
  it("kills a timed-out parser and waits for its close before finishing", async () => {
    vi.useFakeTimers();
    const result = extractAttachmentText(file, "pdf");
    const check = expect(result).rejects.toThrow("parse_failed");
    await vi.advanceTimersByTimeAsync(15_000);
    await check;
    expect(parser.kill).toHaveBeenCalledOnce();
  });
  it("preserves the explicit text/size failure after a clean exit", async () => {
    const result = extractAttachmentText(file, "pdf");
    parser.emit("message", { reason: "too_large" });
    parser.emit("close", 0);
    await expect(result).rejects.toThrow("too_large");
  });
});

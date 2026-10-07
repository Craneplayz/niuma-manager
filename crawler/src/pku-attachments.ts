import { request } from "node:https";
import { Worker } from "node:worker_threads";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import type { BrowserContext } from "playwright";
import { UNREAD_REFERENCE_WARNING, type CourseSourceRecord, type SourceReference } from "./pku-source.js";

const ORIGIN = "https://course.pku.edu.cn";
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const MAX_TEXT = 50_000;
const MAX_PAGES = 50;
class ReadFailure extends Error {}
export interface AttachmentFile { bytes: Uint8Array; contentType: string; }
export type AttachmentReader = (url: string) => Promise<AttachmentFile>;

/** Only teacher content; never send session cookies to arbitrary body links. */
export function isTeacherAttachmentUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const path = decodeURIComponent(url.pathname);
    return url.origin === ORIGIN && !url.username && !url.password
      && !/\/(?:users?|students?|submissions?|attempts?|grades?)(?:\/|$)/i.test(path)
      && (/^\/bbcswebdav\/pid-[\w]+-dt-content-rid-[\w]+(?:\/|$)/i.test(path)
        || /^\/bbcswebdav\/courses\/[^/]+\//i.test(path));
  } catch { return false; }
}

/** Enforce size while streaming, and revalidate every redirect before attaching cookies. */
export function authenticatedAttachmentReader(context: Pick<BrowserContext, "cookies">): AttachmentReader {
  return async function download(url: string, redirects = 0): Promise<AttachmentFile> {
    if (!isTeacherAttachmentUrl(url)) throw new ReadFailure("untrusted_attachment_url");
    const cookies = await context.cookies(url);
    return new Promise((resolve, reject) => {
      const req = request(url, { headers: { Cookie: cookies.map((c) => `${c.name}=${c.value}`).join("; ") } }, (res) => {
        res.on("error", () => {}); // Discard errors on redirects/error bodies after closing them.
        const status = res.statusCode ?? 0;
        if (status >= 300 && status < 400) {
          let next = "";
          try { next = res.headers.location ? new URL(res.headers.location, url).href : ""; } catch { /* Invalid redirects stay unread. */ }
          res.destroy();
          if (redirects >= 3 || !isTeacherAttachmentUrl(next)) reject(new ReadFailure("authentication_required"));
          else download(next, redirects + 1).then(resolve, reject);
          return;
        }
        if (status !== 200) { reject(new ReadFailure(status === 401 || status === 403 ? "authentication_required" : "http_error")); res.destroy(); return; }
        if (Number(res.headers["content-length"] ?? 0) > MAX_ATTACHMENT_BYTES) {
          reject(new ReadFailure("too_large")); res.destroy(); return;
        }
        const chunks: Buffer[] = [];
        let size = 0;
        res.on("data", (chunk: Buffer) => {
          size += chunk.length;
          if (size > MAX_ATTACHMENT_BYTES) { reject(new ReadFailure("too_large")); res.destroy(); }
          else chunks.push(chunk);
        });
        res.on("end", () => resolve({ bytes: new Uint8Array(Buffer.concat(chunks)), contentType: String(res.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase() }));
        res.on("error", () => reject(new ReadFailure("download_failed")));
      });
      const timer = setTimeout(() => req.destroy(new ReadFailure("download_failed")), 15_000);
      req.on("close", () => clearTimeout(timer));
      req.on("error", (error) => reject(error instanceof ReadFailure ? error : new ReadFailure("download_failed")));
      req.end();
    });
  };
}

function formatOf(reference: SourceReference): "pdf" | "txt" | null {
  const name = `${reference.label} ${decodeURIComponent(new URL(reference.url).pathname)}`;
  if (/\.pdf(?:\s|$)/i.test(name)) return "pdf";
  if (/\.txt(?:\s|$)/i.test(name)) return "txt";
  return null;
}

export async function extractAttachmentText(file: AttachmentFile, format: "pdf" | "txt"): Promise<{ text: string; partial: boolean }> {
  if (file.bytes.length > MAX_ATTACHMENT_BYTES) throw new ReadFailure("too_large");
  if (/text\/html|application\/xhtml/i.test(file.contentType)) throw new ReadFailure("authentication_required");
  if (format === "txt") {
    if (file.contentType && !["text/plain", "application/octet-stream"].includes(file.contentType)) throw new ReadFailure("unsupported_format");
    let text: string;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(file.bytes).trim(); } catch { throw new ReadFailure("parse_failed"); }
    if ([...text].some((char) => char.charCodeAt(0) < 32 && ![9, 10, 13].includes(char.charCodeAt(0)))) throw new ReadFailure("parse_failed");
    if (!text) throw new ReadFailure("empty_text");
    if (text.length > MAX_TEXT) throw new ReadFailure("too_large");
    return { text, partial: false };
  }
  if (new TextDecoder().decode(file.bytes.slice(0, 5)) !== "%PDF-") throw new ReadFailure("parse_failed");
  // Parse untrusted files in a bounded worker so a broken PDF cannot stall the sync.
  return new Promise((resolve, reject) => {
    const worker = new Worker(`
      const { parentPort, workerData } = require("node:worker_threads");
      (async () => {
        const { getDocument, OPS } = await import(workerData.module);
        const loading = getDocument({ data: workerData.bytes, isEvalSupported: false, useSystemFonts: false, verbosity: 0 });
        try {
          const pdf = await loading.promise;
          if (pdf.numPages > ${MAX_PAGES}) throw new Error("too_large");
          const pages = [];
          let partial = false;
          for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const content = await page.getTextContent();
            const text = content.items.map((item) => "str" in item ? item.str + (item.hasEOL ? "\\n" : " ") : "").join("").trim();
            const ops = await page.getOperatorList();
            partial ||= !text || ops.fnArray.some((op) => [OPS.paintImageXObject, OPS.paintInlineImageXObject, OPS.paintImageMaskXObject, OPS.paintImageMaskXObjectGroup, OPS.paintInlineImageXObjectGroup, OPS.constructPath].includes(op));
            partial ||= (await page.getAnnotations()).length > 0;
            pages.push(text);
            if (pages.join("\\n\\n").length > ${MAX_TEXT}) throw new Error("too_large");
            page.cleanup();
          }
          const text = pages.join("\\n\\n").trim();
          if (!text) throw new Error("empty_text");
          parentPort.postMessage({ text, partial });
        } finally { await loading.destroy(); }
      })().catch((error) => parentPort.postMessage({ reason: ["too_large", "empty_text"].includes(error.message) ? error.message : "parse_failed" }));
    `, { eval: true, workerData: { bytes: file.bytes, module: pathToFileURL(createRequire(import.meta.url).resolve("pdfjs-dist/legacy/build/pdf.mjs")).href }, resourceLimits: { maxOldGenerationSizeMb: 128, maxYoungGenerationSizeMb: 32 } });
    const timer = setTimeout(() => { void worker.terminate(); reject(new ReadFailure("parse_failed")); }, 15_000);
    worker.once("message", (result: { text: string; partial: boolean; reason?: string }) => {
      clearTimeout(timer);
      void worker.terminate();
      if (result.reason) reject(new ReadFailure(result.reason));
      else resolve(result);
    });
    worker.once("error", () => { clearTimeout(timer); reject(new ReadFailure("parse_failed")); });
    worker.once("exit", (code) => { clearTimeout(timer); if (code !== 0) reject(new ReadFailure("parse_failed")); });
  });
}

/** Preserve failed/unread evidence, never synthesize dates or task decisions. */
export async function enrichSourceAttachments(sources: CourseSourceRecord[], read: AttachmentReader): Promise<CourseSourceRecord[]> {
  const results: CourseSourceRecord[] = [];
  const cache = new Map<string, SourceReference>();
  for (const source of sources) {
    let body = source.body;
    const references: SourceReference[] = [];
    for (const original of source.references) {
      let ref = { ...original };
      if (ref.kind === "attachment" && !ref.content_read) {
        const cached = cache.get(ref.url);
        if (cached) ref = { ...cached, label: ref.label };
        else {
          try {
            const format = formatOf(ref);
            if (!format) throw new ReadFailure("unsupported_format");
            if (!isTeacherAttachmentUrl(ref.url)) throw new ReadFailure("untrusted_attachment_url");
            const file = await read(ref.url).catch((error: unknown) => { throw error instanceof ReadFailure ? error : new ReadFailure("download_failed"); });
            const { text, partial } = await extractAttachmentText(file, format);
            ref = { ...ref, text, content_read: !partial, read_status: partial ? "partial" : "complete", reason: partial ? "partial_pdf" : null };
          } catch (error) {
            const reason = error instanceof ReadFailure ? error.message : "parse_failed";
            ref = { ...ref, content_read: false, read_status: ["unsupported_format", "untrusted_attachment_url"].includes(reason) ? "unsupported" : "failed", reason };
          }
          cache.set(ref.url, ref);
        }
        if (ref.text) {
          const addition = `${body ? "\n\n" : ""}附件正文：${ref.label || "未命名附件"}\n${ref.text}`;
          if (source.title.length + body.length + addition.length + 2 > 100_000) {
            delete ref.text;
            ref = { ...ref, content_read: false, read_status: "failed", reason: "too_large" };
          } else body += addition;
        }
      }
      references.push(ref);
    }
    const unread = references.some((ref) => !ref.content_read);
    const warnings = source.warnings.filter((warning) => warning !== UNREAD_REFERENCE_WARNING);
    if (unread) warnings.push(UNREAD_REFERENCE_WARNING);
    if (body && body !== source.body) {
      const missing = warnings.indexOf("listing body is missing; only the title was collected");
      if (missing >= 0) warnings.splice(missing, 1);
    }
    results.push({ ...source, body, references, warnings, text_quality: !body ? source.text_quality : unread || (source.text_quality === "partial" && !source.references.some((ref) => !ref.content_read)) ? "partial" : "complete" });
  }
  return results;
}

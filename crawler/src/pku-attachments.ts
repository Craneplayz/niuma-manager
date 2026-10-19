import { request } from "node:https";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { BrowserContext } from "playwright";
import { UNREAD_REFERENCE_WARNING, sourceTextFits, type CourseSourceRecord, type SourceReference } from "./pku-source.js";

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
      && (/^\/bbcswebdav\/pid-[\w]+-dt-(?:content|announcement)-rid-[\w]+(?:\/|$)/i.test(path)
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

function formatOf(reference: SourceReference): "pdf" | "txt" | "docx" | "png" | "jpeg" | null {
  let path = "";
  try { path = decodeURIComponent(new URL(reference.url).pathname); } catch { /* Invalid URLs are rejected before any download. */ }
  const name = `${reference.label} ${path}`;
  if (/\.pdf(?:\s|$)/i.test(name)) return "pdf";
  if (/\.txt(?:\s|$)/i.test(name)) return "txt";
  if (/\.docx(?:\s|$)/i.test(name)) return "docx";
  if (/\.png(?:\s|$)/i.test(name)) return "png";
  if (/\.jpe?g(?:\s|$)/i.test(name)) return "jpeg";
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
  // Native PDF dependencies must live outside the crawler process (including Vitest threads).
  // Wait for cleanup and process exit; a native crash can then only fail this attachment.
  return new Promise((resolve, reject) => {
    const parser = spawn(process.execPath, ["--max-old-space-size=128", "--max-semi-space-size=16", "--input-type=commonjs", "--eval", `
      (async () => {
        const chunks = [];
        for await (const chunk of process.stdin) chunks.push(chunk);
        const bytes = new Uint8Array(Buffer.concat(chunks));
        const { getDocument, OPS } = await import(process.argv[1]);
        let degraded = false;
        console.warn = () => { degraded = true; };
        console.info = (message) => {
          // PDF.js also logs successful page timings; these are not recovery diagnostics.
          if (!/^Info: page=\\d+ - get(?:TextContent|OperatorList): time=\\d+ms(?:, len=\\d+)?$/.test(String(message))) degraded = true;
        };
        const loading = getDocument({ data: bytes, isEvalSupported: false, useSystemFonts: true, stopAtErrors: true, verbosity: 5 });
        // Unknown graphics must not be mistaken for text just because extraction returned words.
        const textOnlyOps = new Set([
          "dependency", "save", "restore", "transform", "setLineWidth", "setLineCap", "setLineJoin", "setMiterLimit", "setDash", "setRenderingIntent", "setFlatness",
          "beginText", "endText", "setCharSpacing", "setWordSpacing", "setHScale", "setLeading", "setFont", "setTextRenderingMode", "setTextRise", "moveText", "setLeadingMoveText", "setTextMatrix", "nextLine", "showText", "showSpacedText", "nextLineShowText", "nextLineSetSpacingShowText", "setCharWidth", "setCharWidthAndBounds",
          "setStrokeColorSpace", "setFillColorSpace", "setStrokeColor", "setFillColor", "setStrokeGray", "setFillGray", "setStrokeRGBColor", "setFillRGBColor", "setStrokeCMYKColor", "setFillCMYKColor", "setStrokeTransparent", "setFillTransparent",
          "markPoint", "markPointProps", "beginMarkedContent", "beginMarkedContentProps", "endMarkedContent", "beginCompat", "endCompat", "paintFormXObjectBegin", "paintFormXObjectEnd"
        ].map((name) => OPS[name]));
        try {
          const pdf = await loading.promise;
          if (pdf.numPages > ${MAX_PAGES}) throw new Error("too_large");
          const pages = [];
          // Embedded files are outside page text; discard their bytes without recursive extraction.
          let partial = Object.keys(await pdf.getAttachments() || {}).length > 0;
          for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const content = await page.getTextContent();
            const text = content.items.map((item) => "str" in item ? item.str + (item.hasEOL ? "\\n" : " ") : "").join("").trim();
            const ops = await page.getOperatorList();
            partial ||= !text || ops.fnArray.some((op) => !textOnlyOps.has(op));
            partial ||= (await page.getAnnotations()).length > 0;
            pages.push(text);
            if (pages.join("\\n\\n").length > ${MAX_TEXT}) throw new Error("too_large");
            page.cleanup();
          }
          const text = pages.join("\\n\\n").trim();
          if (!text) throw new Error("empty_text");
          return { text, partial: partial || degraded };
        } finally { await loading.destroy(); }
      })().catch((error) => ({ reason: ["too_large", "empty_text"].includes(error.message) ? error.message : "parse_failed" }))
        .then((result) => process.send(result, (error) => {
          if (error) process.exitCode = 1;
          process.disconnect();
        }));
    `, pathToFileURL(createRequire(import.meta.url).resolve("pdfjs-dist/legacy/build/pdf.mjs")).href], {
      stdio: ["pipe", "ignore", "ignore", "ipc"],
      windowsHide: true,
    });
    let result: { text: string; partial: boolean; reason?: string } | undefined;
    let timedOut = false;
    // The deadline must also stop a parser that ignores graceful termination.
    const timer = setTimeout(() => { timedOut = true; parser.kill("SIGKILL"); }, 15_000);
    parser.once("message", (message) => { result = message as typeof result; });
    parser.once("error", () => { clearTimeout(timer); reject(new ReadFailure("parse_failed")); });
    parser.once("close", (code) => {
      clearTimeout(timer);
      if (timedOut || code !== 0 || !result) reject(new ReadFailure("parse_failed"));
      else if (result.reason) reject(new ReadFailure(result.reason));
      else resolve(result);
    });
    // A child crash may close stdin before all bytes are written; close handles the failure.
    parser.stdin!.on("error", () => {});
    parser.stdin!.end(file.bytes);
  });
}

/** Preserve failed/unread evidence, never synthesize dates or task decisions. */
export async function enrichSourceAttachments(sources: CourseSourceRecord[], read: AttachmentReader, staging?: string): Promise<CourseSourceRecord[]> {
  const results: CourseSourceRecord[] = [];
  const cache = new Map<string, SourceReference>();
  // Different course/assignment URLs can serve the same PDF. Keep only bounded
  // parsed text for this capture; never reuse source identity or retain bytes.
  const parsed = new Map<string, { text: string; partial: boolean }>();
  let parsedChars = 0;
  const parse = async (file: AttachmentFile, format: "pdf" | "txt") => {
    if (file.bytes.length > MAX_ATTACHMENT_BYTES) throw new ReadFailure("too_large");
    const key = JSON.stringify([format, file.contentType, createHash("sha256").update(file.bytes).digest("hex")]);
    const previous = parsed.get(key);
    if (previous) return previous;
    const result = await extractAttachmentText(file, format);
    while (parsed.size && (parsed.size >= 64 || parsedChars + result.text.length > 1_000_000)) {
      const oldest = parsed.keys().next().value!;
      parsedChars -= parsed.get(oldest)!.text.length;
      parsed.delete(oldest);
    }
    parsed.set(key, result);
    parsedChars += result.text.length;
    return result;
  };
  const stored = new Set<string>();
  let storedBytes = 0;
  const stage = async (file: AttachmentFile, format: Exclude<ReturnType<typeof formatOf>, null>): Promise<NonNullable<SourceReference["asset"]>> => {
    const mime = { pdf: "application/pdf", txt: "text/plain", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", png: "image/png", jpeg: "image/jpeg" }[format];
    if (file.bytes.length > MAX_ATTACHMENT_BYTES || !file.bytes.length) throw new ReadFailure("too_large");
    if (file.contentType && ![mime, "application/octet-stream", ...(format === "docx" ? ["application/zip"] : [])].includes(file.contentType)) throw new ReadFailure("unsupported_format");
    const sha256 = createHash("sha256").update(file.bytes).digest("hex");
    if (!stored.has(sha256)) {
      if (storedBytes + file.bytes.length > 100 * 1024 * 1024) throw new ReadFailure("capture_size_limit");
      await mkdir(staging!, { recursive: true, mode: 0o700 });
      await writeFile(path.join(staging!, sha256 + ".bin"), file.bytes, { mode: 0o600 });
      stored.add(sha256); storedBytes += file.bytes.length;
    }
    return { sha256, mime_type: mime, size_bytes: file.bytes.length };
  };
  for (const source of sources) {
    let body = source.body;
    const references: SourceReference[] = [];
    for (const original of source.references) {
      let ref = { ...original };
      if ((ref.kind === "attachment" || staging && ref.kind === "image") && !ref.content_read) {
        const format = formatOf(ref);
        const cacheKey = `${ref.url}\n${format}`;
        const cached = format ? cache.get(cacheKey) : undefined;
        if (cached) ref = { ...cached, label: ref.label };
        else {
          try {
            if (!format || !staging && !["pdf", "txt"].includes(format)) throw new ReadFailure("unsupported_format");
            if (!isTeacherAttachmentUrl(ref.url)) throw new ReadFailure("untrusted_attachment_url");
            const file = await read(ref.url).catch((error: unknown) => { throw error instanceof ReadFailure ? error : new ReadFailure("download_failed"); });
            // Server captures hand PDF bytes to the visibility-aware reader.
            // Avoid parsing twice, and do not label a PDF text layer complete
            // before hidden/optional content has been checked against the page.
            if (format === "txt" || format === "pdf" && !staging) {
              const { text, partial } = await parse(file, format);
              ref = { ...ref, text, content_read: !partial, read_status: partial ? "partial" : "complete", reason: partial ? "partial_pdf" : null };
            }
            if (staging && !ref.content_read && format !== "txt") {
              ref = { ...ref, asset: await stage(file, format), reason: "awaiting_file_reader" };
            }
          } catch (error) {
            const reason = error instanceof ReadFailure ? error.message : "parse_failed";
            ref = { ...ref, content_read: false, read_status: ["unsupported_format", "untrusted_attachment_url"].includes(reason) ? "unsupported" : "failed", reason };
          }
          if (format) cache.set(cacheKey, { ...ref });
        }
        if (ref.text) {
          const addition = `${body ? "\n\n" : ""}附件正文：${ref.label || "未命名附件"}\n${ref.text}`;
          if (!sourceTextFits(source.title, body + addition)) {
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

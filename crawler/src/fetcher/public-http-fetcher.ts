import { setTimeout as delay } from "node:timers/promises";
import { sha256 } from "../core/utils.js";
import type { FetchRequest, FetchResult, Fetcher } from "../core/types.js";

export class FetchError extends Error {
  constructor(
    message: string,
    public readonly category: "network_error" | "robots_disallowed",
    public readonly url: string,
  ) { super(message); }
}

interface PublicHttpFetcherOptions {
  userAgent?: string;
  timeoutMs?: number;
  maxRetries?: number;
  maxResponseBytes?: number;
  minDelayMs?: number;
  maxRedirects?: number;
  fetchImpl?: typeof fetch;
}

interface RobotsRules { disallow: string[]; allow: string[]; crawlDelayMs?: number; }

export class PublicHttpFetcher implements Fetcher {
  private readonly userAgent: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly maxResponseBytes: number;
  private readonly minDelayMs: number;
  private readonly maxRedirects: number;
  private readonly fetchImpl: typeof fetch;
  private readonly nextAllowedAt = new Map<string, number>();
  private readonly robotsCache = new Map<string, { expiresAt: number; rules: RobotsRules | null }>();

  constructor(options: PublicHttpFetcherOptions = {}) {
    this.userAgent = options.userAgent ?? "niuma-manager-crawler/0.1 (+public-event-research; contact=maintainers)";
    this.timeoutMs = options.timeoutMs ?? 20_000;
    this.maxRetries = options.maxRetries ?? 2;
    this.maxResponseBytes = options.maxResponseBytes ?? 8 * 1024 * 1024;
    this.minDelayMs = options.minDelayMs ?? 750;
    this.maxRedirects = options.maxRedirects ?? 5;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async fetch(request: FetchRequest): Promise<FetchResult> {
    const target = new URL(request.url);
    if (!/^https?:$/.test(target.protocol)) throw new FetchError("only HTTP(S) URLs are supported", "network_error", request.url);
    if (!(await this.allowedByRobots(target))) throw new FetchError("robots.txt disallows this URL", "robots_disallowed", request.url);

    let lastError: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
      try {
        await this.waitForOrigin(target.origin);
        return await this.requestOnce(request);
      } catch (error) {
        lastError = error;
        if (error instanceof FetchError && error.category === "robots_disallowed") throw error;
        if (attempt < this.maxRetries) await delay(300 * 2 ** attempt + Math.floor(Math.random() * 150));
      }
    }
    throw new FetchError(lastError instanceof Error ? lastError.message : "request failed", "network_error", request.url);
  }

  private async requestOnce(request: FetchRequest): Promise<FetchResult> {
    const started = Date.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), request.timeoutMs ?? this.timeoutMs);
    try {
      let currentUrl = request.url;
      let response: Response;
      for (let redirectCount = 0; ; redirectCount += 1) {
        const currentOrigin = new URL(currentUrl).origin;
        await this.waitForOrigin(currentOrigin);
        response = await this.fetchImpl(currentUrl, {
          headers: { "user-agent": this.userAgent, accept: "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5" },
          redirect: "manual",
          signal: controller.signal,
        });
        if (response.status < 300 || response.status >= 400) break;
        const location = response.headers.get("location");
        if (!location || redirectCount >= this.maxRedirects) throw new Error(`redirect limit exceeded for ${request.url}`);
        currentUrl = new URL(location, currentUrl).toString();
        if (!(await this.allowedByRobots(new URL(currentUrl)))) throw new FetchError("redirect target is disallowed by robots.txt", "robots_disallowed", currentUrl);
      }
      const body = await readBodyWithLimit(response, request.maxResponseBytes ?? this.maxResponseBytes);
      const headers: Record<string, string> = {};
      response.headers.forEach((value, key) => { headers[key] = value; });
      return {
        requestedUrl: request.url,
        finalUrl: response.url || currentUrl,
        status: response.status,
        statusText: response.statusText,
        headers,
        contentType: response.headers.get("content-type"),
        body,
        contentHash: sha256(body),
        fetchedAt: new Date().toISOString(),
        durationMs: Date.now() - started,
      };
    } catch (error) {
      throw new FetchError(error instanceof Error ? error.message : "request failed", "network_error", request.url);
    } finally {
      clearTimeout(timeout);
    }
  }

  private async waitForOrigin(origin: string): Promise<void> {
    const now = Date.now();
    const waitMs = Math.max(0, (this.nextAllowedAt.get(origin) ?? now) - now);
    if (waitMs) await delay(waitMs);
    this.nextAllowedAt.set(origin, Date.now() + this.minDelayMs);
  }

  private async allowedByRobots(target: URL): Promise<boolean> {
    if (target.pathname === "/robots.txt") return true;
    const origin = target.origin;
    const cached = this.robotsCache.get(origin);
    let rules = cached && cached.expiresAt > Date.now() ? cached.rules : undefined;
    if (rules === undefined) {
      try {
        await this.waitForOrigin(origin);
        const response = await this.fetchImpl(`${origin}/robots.txt`, {
          headers: { "user-agent": this.userAgent, accept: "text/plain,*/*;q=0.5" },
          redirect: "follow",
          signal: AbortSignal.timeout(Math.min(this.timeoutMs, 10_000)),
        });
        rules = response.ok ? parseRobots(await response.text(), this.userAgent) : null;
      } catch {
        // A robots endpoint that cannot be fetched is not treated as permission to crawl blindly.
        rules = null;
      }
      this.robotsCache.set(origin, { expiresAt: Date.now() + 15 * 60_000, rules });
    }
    if (!rules) return true;
    if (rules.crawlDelayMs) this.nextAllowedAt.set(origin, Math.max(this.nextAllowedAt.get(origin) ?? 0, Date.now() + rules.crawlDelayMs));
    const path = `${target.pathname}${target.search}`;
    const matchedAllow = rules.allow.filter((rule) => rule && path.startsWith(rule)).sort((a, b) => b.length - a.length)[0];
    const matchedDisallow = rules.disallow.filter((rule) => rule && path.startsWith(rule)).sort((a, b) => b.length - a.length)[0];
    return !matchedDisallow || Boolean(matchedAllow && matchedAllow.length >= matchedDisallow.length);
  }
}

async function readBodyWithLimit(response: Response, maxBytes: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const item = await reader.read();
    if (item.done) break;
    size += item.value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      throw new Error(`response exceeded ${maxBytes} bytes`);
    }
    chunks.push(item.value);
  }
  return new TextDecoder().decode(concat(chunks, size));
}

function concat(chunks: Uint8Array[], size: number): Uint8Array {
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

function parseRobots(text: string, userAgent: string): RobotsRules {
  const groups: Array<{ agents: string[]; disallow: string[]; allow: string[]; crawlDelayMs?: number }> = [];
  let current: (typeof groups)[number] | null = null;
  let hasRule = false;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.split("#", 1)[0].trim();
    if (!line) { if (current && hasRule) { groups.push(current); current = null; hasRule = false; } continue; }
    const [key, ...rest] = line.split(":");
    const value = rest.join(":").trim();
    if (/^user-agent$/i.test(key)) {
      if (current && hasRule) groups.push(current);
      current = { agents: [value.toLowerCase()], disallow: [], allow: [] };
      hasRule = false;
    } else if (current && /^disallow$/i.test(key)) { current.disallow.push(value); hasRule = true; }
    else if (current && /^allow$/i.test(key)) { current.allow.push(value); hasRule = true; }
    else if (current && /^crawl-delay$/i.test(key) && Number.isFinite(Number(value))) current.crawlDelayMs = Number(value) * 1000;
  }
  if (current && hasRule) groups.push(current);
  const token = userAgent.split(/[ /]/, 1)[0].toLowerCase();
  const chosen = groups.find((group) => group.agents.includes(token)) ?? groups.find((group) => group.agents.includes("*"));
  return chosen ? { disallow: chosen.disallow, allow: chosen.allow, crawlDelayMs: chosen.crawlDelayMs } : { disallow: [], allow: [] };
}

import { createHash } from "node:crypto";

export function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

export function canonicalizeUrl(value: string, base?: string): string {
  const url = new URL(value, base);
  url.hash = "";
  for (const key of [...url.searchParams.keys()]) {
    if (/^(utm_|fbclid$|gclid$)/i.test(key)) url.searchParams.delete(key);
  }
  return url.toString();
}

export function cleanText(value: string | undefined | null): string | undefined {
  if (!value) return undefined;
  const cleaned = value
    .normalize("NFKC")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\s*\n\s*/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n")
    .trim();
  return cleaned || undefined;
}

export function stripLabel(value: string | undefined, labels: string[]): string | undefined {
  const cleaned = cleanText(value);
  if (!cleaned) return undefined;
  const pattern = new RegExp(`^(?:${labels.map(escapeRegExp).join("|")})\\s*[:：]\\s*`, "i");
  return cleanText(cleaned.replace(pattern, ""));
}

export function splitPeople(value: string | undefined): string[] {
  const cleaned = cleanText(value);
  if (!cleaned) return [];
  return cleaned.split(/[、,，;；\n]|\s+and\s+/i).map((item) => item.trim()).filter(Boolean);
}

export function absoluteUrl(href: string | undefined, base: string): string | undefined {
  if (!href || /^(?:javascript:|mailto:|tel:|#)/i.test(href.trim())) return undefined;
  try { return canonicalizeUrl(href.trim(), base); } catch { return undefined; }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

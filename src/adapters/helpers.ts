import * as cheerio from "cheerio";
import type { Cheerio } from "cheerio";
import type { AnyNode } from "domhandler";
import { absoluteUrl, cleanText } from "../core/utils.js";

export function loadHtml(html: string): cheerio.CheerioAPI {
  return cheerio.load(html);
}

export function elementText(node: Cheerio<AnyNode>): string | undefined {
  return cleanText(node.text());
}

export function firstText($: cheerio.CheerioAPI, selectors: string[]): string | undefined {
  for (const selector of selectors) {
    const value = elementText($(selector).first());
    if (value) return value;
  }
  return undefined;
}

export function linksInPage($: cheerio.CheerioAPI, baseUrl: string): Array<{ url: string; text: string; node: Cheerio<AnyNode> }> {
  const links: Array<{ url: string; text: string; node: Cheerio<AnyNode> }> = [];
  $("a[href]").each((_, element) => {
    const node = $(element);
    const url = absoluteUrl(node.attr("href"), baseUrl);
    if (url) links.push({ url, text: elementText(node) ?? "", node });
  });
  return links;
}

export function itemContainer($: cheerio.CheerioAPI, node: Cheerio<AnyNode>): Cheerio<AnyNode> {
  const candidates = [node.closest("li"), node.closest("tr"), node.closest(".news-item"), node.closest(".list-item"), node.parent()];
  let best = node;
  for (const candidate of candidates) {
    const text = elementText(candidate) ?? "";
    if (text.length >= 20 && text.length <= 2_000) { best = candidate; break; }
  }
  return best;
}

export function labeledText(text: string | undefined, labels: string[]): string | undefined {
  const value = cleanText(text);
  if (!value) return undefined;
  const allBoundaries = ["时间", "日期", "地点", "主办方", "主办单位", "承办方", "主讲人", "主讲", "报告人", "邀请人", "主持人", "与评人", "与谈人", "腾讯会议号", "speaker", "organizer", "location", "venue", "host", "摘要", "abstract", "报名", "注册", "讲座"];
  const field = findLabel(value, labels, 0);
  if (!field) return undefined;
  let contentStart = skipWhitespace(value, field.end);
  if (value[contentStart] === "(") {
    const closing = value.indexOf(")", contentStart + 1);
    if (closing >= 0) contentStart = closing + 1;
  }
  contentStart = skipWhitespace(value, contentStart);
  if (value[contentStart] === ":" || value[contentStart] === "：") contentStart += 1;
  contentStart = skipWhitespace(value, contentStart);
  let end = value.length;
  for (const boundary of allBoundaries) {
    const next = findLabel(value, [boundary], contentStart);
    if (next && next.start < end) end = next.start;
  }
  return cleanText(value.slice(contentStart, end));
}

function findLabel(value: string, labels: string[], from: number): { start: number; end: number } | undefined {
  let best: { start: number; end: number } | undefined;
  for (const rawLabel of labels) {
    const label = rawLabel.replace(/\\s\*/g, "");
    for (let start = from; start < value.length; start += 1) {
      let cursor = start;
      let matched = true;
      for (const character of label) {
        while (/\s/.test(value[cursor] ?? "")) cursor += 1;
        if ((value[cursor] ?? "").toLocaleLowerCase() !== character.toLocaleLowerCase()) { matched = false; break; }
        cursor += 1;
      }
      if (matched) {
        const after = skipWhitespace(value, cursor);
        if (value[after] !== ":" && value[after] !== "：" && value[after] !== "(") continue;
        if (!best || start < best.start) best = { start, end: cursor };
        break;
      }
    }
  }
  return best;
}

function skipWhitespace(value: string, position: number): number {
  let current = position;
  while (/\s/.test(value[current] ?? "")) current += 1;
  return current;
}

export function publicationHint(text: string | undefined): string | undefined {
  const value = cleanText(text);
  if (!value) return undefined;
  const match = value.match(/(?:发布时间|发布日期|发布于|信息日期|日期|Date|发表于)\s*[:：]?\s*([^\n|]+)/i);
  if (match) return cleanText(match[1]);
  const date = value.match(/\b20\d{2}[./-]\d{1,2}[./-]\d{1,2}\b|\b20\d{2}年\d{1,2}月\d{1,2}日\b/);
  return date?.[0];
}

export function dateHint(text: string | undefined): string | undefined {
  const value = cleanText(text);
  if (!value) return undefined;
  return value.match(/(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|20\d{2}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|[A-Za-z]+\s+\d{1,2},?\s*20\d{2})/)?.[0];
}

export function descriptionFromPage($: cheerio.CheerioAPI, title?: string): string | undefined {
  const value = firstText($, [".article-content", ".v_news_content", "#vsb_content", ".content", ".news-content", ".article", ".ar_article"]);
  if (!value) return undefined;
  const withoutTitle = title ? value.replace(title, "") : value;
  return cleanText(withoutTitle);
}

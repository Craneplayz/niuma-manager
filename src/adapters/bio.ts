import type { EventCandidate, RawDocument, SourceAdapter } from "../core/types.js";
import { canonicalizeUrl, cleanText } from "../core/utils.js";
import { dateHint, descriptionFromPage, elementText, firstText, itemContainer, labeledText, linksInPage, loadHtml, publicationHint } from "./helpers.js";

const LIST_URL = "https://web.bio.pku.edu.cn/index.php?a=news_jz&cid=29&m=Index";

export const bioAdapter: SourceAdapter = {
  metadata: {
    id: "pku-life-sciences-lectures",
    name: "PKU School of Life Sciences lectures",
    allowedOrigins: ["https://web.bio.pku.edu.cn"],
    seedUrls: [LIST_URL],
    maxPages: 40,
  },
  isAllowedUrl(url) {
    try {
      const parsed = new URL(url);
      if (parsed.origin !== "https://web.bio.pku.edu.cn" || !/index\.php/i.test(parsed.pathname)) return false;
      const action = parsed.searchParams.get("a");
      return action === "news_jz" && parsed.searchParams.get("cid") === "29" || action === "news_cont";
    } catch { return false; }
  },
  discover(document) {
    const $ = loadHtml(document.body);
    const urls = linksInPage($, document.finalUrl)
      .filter((link) => this.isAllowedUrl(link.url))
      .filter((link) => /a=news_cont|a=news_jz.*(?:cid=29|p=)|下一页|上一页/i.test(`${link.text} ${link.url}`))
      .map((link) => ({ url: link.url, kind: /page=|下一页|上一页/i.test(`${link.text} ${link.url}`) ? "pagination" as const : "detail" as const, discoveredFromUrl: document.finalUrl }));
    return { urls };
  },
  parse(document) {
    const $ = loadHtml(document.body);
    const results: EventCandidate[] = [];
    const seen = new Set<string>();
    const links = linksInPage($, document.finalUrl).filter((link) => this.isAllowedUrl(link.url));
    const currentAction = new URL(document.finalUrl).searchParams.get("a");
    for (const link of links) {
      if (currentAction === "news_cont") continue;
      if (new URL(link.url).searchParams.get("a") !== "news_cont") continue;
      if (!link.node.closest("li").length) continue;
      const container = itemContainer($, link.node);
      const text = elementText(container) ?? link.text;
      const signal = /\b20\d{2}[-./]\d{1,2}[-./]\d{1,2}\b|讲座|lecture|报告厅|room|building/i.test(`${link.text} ${text}`);
      if (!signal || !link.text || /查看更多|上一页|下一页|首页/i.test(link.text)) continue;
      const url = canonicalizeUrl(link.url);
      if (seen.has(url)) continue;
      seen.add(url);
      results.push(candidateFromText(document, url, link.text, text, $));
    }
    if (!results.length) {
      const title = firstText($, [".tit h3", "h1", "h2", ".title", ".news_title"]);
      const bodyText = firstText($, [".txt", ".article-content", ".news-content", ".content"]) ?? cleanText($("body").text());
      if (title && /讲座|lecture|seminar|报告/i.test(`${title} ${bodyText ?? ""}`)) results.push(candidateFromText(document, document.finalUrl, title, bodyText ?? "", $));
    }
    return results;
  },
};

function candidateFromText(document: RawDocument, url: string, titleText: string, text: string, $: ReturnType<typeof loadHtml>): EventCandidate {
  const title = cleanBioTitle(titleText) ?? "Untitled event";
  const time = labeledText(text, ["时间", "日期", "time", "date"]) ?? dateHint(text);
  const location = labeledText(text, ["地点", "location", "venue"]) ?? inferBioLocation(text);
  const speaker = labeledText(text, ["报告人", "主讲人", "speaker", "讲者"]) ?? inferBioSpeaker(text, title);
  return {
    source_id: "pku-life-sciences-lectures",
    source_item_id: url,
    source_url: url,
    title_raw: title,
    description_raw: descriptionFromPage($, title),
    time_raw: time,
    location_raw: location,
    speaker_raw: speaker,
    organizer_raw: "北京大学生命科学学院",
    publication_time_raw: publicationHint(text),
    event_type_raw: "讲座",
    discovered_from_url: document.discoveredFromUrl ?? document.requestedUrl,
    fetched_at: document.fetchedAt,
  };
}

function inferBioLocation(text: string): string | undefined {
  return cleanText(text.match(/(?:Rm\.?\s*\d+|Room\s+)[^\n,;]*(?:Building|Hall|报告厅|会议室)?/i)?.[0] ?? text.match(/[^\s,，]+(?:报告厅|会议室)/i)?.[0]);
}

function inferBioSpeaker(text: string, title: string): string | undefined {
  const dateIndex = text.search(/\b20\d{2}[-./]\d{1,2}[-./]\d{1,2}\b/);
  if (dateIndex > 0) return cleanText(text.slice(0, dateIndex).replace(title, ""));
  return undefined;
}

function cleanBioTitle(value: string): string | undefined {
  const cleaned = cleanText(value);
  if (!cleaned) return undefined;
  const withoutDate = cleaned.replace(/^20\d{2}[./-]\d{1,2}[./-]\d{1,2}\s*/, "");
  return cleanText(withoutDate.split(/(?:主讲人|报告人|时间|地点)\s*[:：]/i)[0]);
}

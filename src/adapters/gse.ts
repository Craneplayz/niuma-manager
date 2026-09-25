import type { EventCandidate, RawDocument, SourceAdapter } from "../core/types.js";
import { canonicalizeUrl, cleanText } from "../core/utils.js";
import { dateHint, descriptionFromPage, elementText, firstText, itemContainer, labeledText, linksInPage, loadHtml, publicationHint } from "./helpers.js";

const LIST_URL = "https://www.gse.pku.edu.cn/syxw/jzxx/index.htm";

function isListUrl(url: string): boolean {
  return /\/index(?:_?\d+)?\.htm$/i.test(new URL(url).pathname);
}

export const gseAdapter: SourceAdapter = {
  metadata: {
    id: "pku-gse-lectures",
    name: "PKU Graduate School of Education lectures",
    allowedOrigins: ["https://www.gse.pku.edu.cn"],
    seedUrls: [LIST_URL],
    maxPages: 35,
  },
  isAllowedUrl(url) {
    try {
      const parsed = new URL(url);
      return parsed.origin === "https://www.gse.pku.edu.cn" && /\/syxw\/jzxx\//i.test(parsed.pathname) && !/\/index\.html$/i.test(parsed.pathname);
    } catch { return false; }
  },
  discover(document) {
    const $ = loadHtml(document.body);
    const urls = linksInPage($, document.finalUrl)
      .filter((link) => this.isAllowedUrl(link.url))
      .map((link) => ({ url: link.url, kind: isListUrl(link.url) ? "pagination" as const : "detail" as const, discoveredFromUrl: document.finalUrl }));
    return { urls };
  },
  parse(document) {
    const $ = loadHtml(document.body);
    const isDetailPage = !isListUrl(document.finalUrl);
    if (isDetailPage) return parseDetailPage(document, $);
    const results: EventCandidate[] = [];
    const seen = new Set<string>();
    for (const link of linksInPage($, document.finalUrl).filter((item) => this.isAllowedUrl(item.url))) {
      if (isListUrl(link.url)) continue;
      const container = itemContainer($, link.node);
      const text = elementText(container) ?? link.text;
      if (!link.text || /首页|上一页|下一页|最后页|跳转/i.test(link.text)) continue;
      if (!/讲座|论坛|seminar|lecture|\[20\d{2}-\d{2}-\d{2}\]|20\d{2}[./-]\d{1,2}[./-]\d{1,2}/i.test(`${link.text} ${text}`)) continue;
      const url = canonicalizeUrl(link.url);
      if (seen.has(url)) continue;
      seen.add(url);
      results.push(candidateFromText(document, url, link.text, text, $));
    }
    if (!results.length) {
      const title = firstText($, ["h1", "h2", ".title", ".news_title"]);
      const bodyText = cleanText($("body").text());
      if (title && /讲座|论坛|lecture|forum/i.test(`${title} ${bodyText ?? ""}`)) results.push(candidateFromText(document, document.finalUrl, title, bodyText ?? "", $));
    }
    return results;
  },
};

function parseDetailPage(document: RawDocument, $: ReturnType<typeof loadHtml>): EventCandidate[] {
  const fullArticleText = firstText($, ["#articleDiv", ".article", ".article-content", ".content"]) ?? cleanText($("body").text()) ?? "";
  const articleText = fullArticleText.split(/\d{4}年\d{1,2}月\d{1,2}日第二场讲座|第二场讲座|第二场报告/i)[0];
  const title = firstGseContentTitle($) ?? firstText($, ["#title", ".article .title", ".title", "h1", "h2"]);
  if (!title || !/讲座|论坛|lecture|forum|seminar/i.test(`${title} ${articleText}`)) return [];
  return [candidateFromText(document, document.finalUrl, title, articleText, $)];
}

function firstGseContentTitle($: ReturnType<typeof loadHtml>): string | undefined {
  let sawTopic = false;
  let result: string | undefined;
  $("#articleDiv p, .article-content p").each((_, element) => {
    if (result) return;
    const text = elementText($(element));
    if (!text) return;
    if (/讲座题目|报告题目|lecture topic|talk title/i.test(text)) { sawTopic = true; return; }
    if (sawTopic && !/讲座内容|摘要|时间|地点|主持人|主讲人|报告人/i.test(text)) result = text;
  });
  return cleanText(result);
}

function candidateFromText(document: RawDocument, url: string, titleText: string, text: string, $: ReturnType<typeof loadHtml>): EventCandidate {
  const bracketDate = titleText.match(/\[(20\d{2}-\d{1,2}-\d{1,2})\]/)?.[1];
  const title = cleanText(titleText.replace(/\s*\[20\d{2}-\d{1,2}-\d{1,2}\]\s*$/, "")) ?? "Untitled event";
  const time = /时间|日期|time\s*[:：]|date\s*[:：]/i.test(text) ? labeledText(text, ["时间", "日期", "time", "date"]) ?? dateHint(text) ?? bracketDate : dateHint(text) ?? bracketDate;
  return {
    source_id: "pku-gse-lectures",
    source_item_id: url,
    source_url: url,
    title_raw: title,
    description_raw: descriptionFromPage($, title),
    time_raw: time,
    location_raw: labeledText(text, ["地点", "location", "venue"]),
    organizer_raw: labeledText(text, ["主办", "主办方", "organizer"]),
    speaker_raw: primaryGseSpeaker(text),
    publication_time_raw: firstText($, ["#date"]) ?? publicationHint(text) ?? bracketDate,
    event_type_raw: /论坛|forum/i.test(title) ? "论坛" : "讲座",
    discovered_from_url: document.discoveredFromUrl ?? document.requestedUrl,
    fetched_at: document.fetchedAt,
  };
}

function primaryGseSpeaker(text: string): string | undefined {
  const value = labeledText(text, ["主讲人", "报告人", "主持人", "speaker"]);
  return cleanText(value?.split(/\r?\n|与谈人|与评人/i)[0].split(/[，,]/)[0]);
}

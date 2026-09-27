import type { EventCandidate, RawDocument, SourceAdapter } from "../core/types.js";
import { canonicalizeUrl, cleanText } from "../core/utils.js";
import { dateHint, descriptionFromPage, elementText, firstText, itemContainer, labeledText, linksInPage, loadHtml, publicationHint } from "./helpers.js";

const LIST_URL = "https://cs.pku.edu.cn/xyxw/jzxx.htm";

export const csAdapter: SourceAdapter = {
  metadata: {
    id: "pku-cs-lectures",
    name: "PKU School of Computer Science academic lectures",
    allowedOrigins: ["https://cs.pku.edu.cn"],
    seedUrls: [LIST_URL],
    maxPages: 30,
  },
  isAllowedUrl(url) {
    try {
      const parsed = new URL(url);
      return parsed.origin === "https://cs.pku.edu.cn" && (/\/xyxw\/jzxx/i.test(parsed.pathname) || /\/info\/1019\//i.test(parsed.pathname));
    } catch { return false; }
  },
  discover(document) {
    const $ = loadHtml(document.body);
    const urls = linksInPage($, document.finalUrl)
      .filter((link) => this.isAllowedUrl(link.url))
      .map((link) => ({ url: link.url, kind: /\/info\/1019\//i.test(new URL(link.url).pathname) ? "detail" as const : "pagination" as const, discoveredFromUrl: document.finalUrl }));
    return { urls };
  },
  parse(document) {
    const $ = loadHtml(document.body);
    const results: EventCandidate[] = [];
    const links = linksInPage($, document.finalUrl).filter((link) => this.isAllowedUrl(link.url));
    const seen = new Set<string>();
    for (const link of links) {
      if (!/\/info\/1019\//i.test(new URL(link.url).pathname)) continue;
      if (!link.node.closest("li").length) continue;
      const container = itemContainer($, link.node);
      const containerText = elementText(container) ?? link.text;
      const hasEventSignal = /时间|地点|主讲人|主办方|lecture|讲座|论坛|研讨/i.test(containerText);
      if (!hasEventSignal || !link.text || /首页|上一页|下一页|尾页|查看更多/i.test(link.text)) continue;
      const url = canonicalizeUrl(link.url);
      if (seen.has(url)) continue;
      seen.add(url);
      results.push(candidateFromText(document, url, elementText(link.node.find("h4").first()) ?? link.text, containerText, $));
    }
    if (!results.length) {
      const title = firstText($, [".nav01 h3", "meta[name='pageTitle']", "h3", "h1", "h2", ".title", ".news_title"]);
      const bodyText = cleanText($("body").text());
      if (title && /讲座|论坛|seminar|lecture/i.test(`${title} ${bodyText ?? ""}`) && (dateHint(bodyText) || /时间/i.test(bodyText ?? ""))) {
        results.push(candidateFromText(document, document.finalUrl, title, bodyText ?? "", $));
      }
    }
    return results;
  },
};

function candidateFromText(document: RawDocument, url: string, titleText: string, text: string, $: ReturnType<typeof loadHtml>): EventCandidate {
  const title = cleanText(titleText)?.replace(/^——\s*/, "") ?? "Untitled event";
  const time = labeledText(text, ["时间", "time", "date"])
    ?? dateHint(text);
  const speaker = labeledText(text, ["主讲人", "主讲", "speaker", "讲者"]);
  const location = labeledText(text, ["地点", "location", "venue"]);
  const organizer = labeledText(text, ["主办方", "主办单位", "承办方", "organizer"]);
  const publication = publicationHint(text);
  return {
    source_id: "pku-cs-lectures",
    source_item_id: url,
    source_url: url,
    title_raw: title,
    description_raw: descriptionFromPage($, title),
    time_raw: time,
    location_raw: location,
    organizer_raw: organizer,
    speaker_raw: speaker,
    publication_time_raw: publication,
    event_type_raw: /论坛|forum/i.test(title) ? "论坛" : /研讨|seminar/i.test(title) ? "seminar" : "讲座",
    discovered_from_url: document.discoveredFromUrl ?? document.requestedUrl,
    fetched_at: document.fetchedAt,
  };
}

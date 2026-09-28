import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import { bioAdapter } from "../src/adapters/bio.js";
import { csAdapter } from "../src/adapters/cs.js";
import { gseAdapter } from "../src/adapters/gse.js";
import { normalizeCandidate } from "../src/normalizer.js";
import type { RawDocument } from "../src/core/types.js";

async function document(path: string, sourceId: string, url: string): Promise<RawDocument> {
  return { sourceId, crawlRunId: "fixture", requestedUrl: url, finalUrl: url, status: 200, statusText: "OK", headers: {}, contentType: "text/html", body: await readFile(path, "utf8"), contentHash: "fixture", fetchedAt: "2026-09-22T00:00:00.000Z", durationMs: 1 };
}

describe("source adapters", () => {
  it("extracts CS events, pagination, Chinese and English times", async () => {
    const doc = await document("tests/fixtures/cs/list.html", csAdapter.metadata.id, "https://cs.pku.edu.cn/xyxw/jzxx.htm");
    const candidates = csAdapter.parse(doc);
    expect(candidates).toHaveLength(2);
    const english = normalizeCandidate(candidates[0]).event!;
    const chinese = normalizeCandidate(candidates[1]).event!;
    expect(english.start_time).toBe("2026-08-17T15:00:00+08:00");
    expect(english.end_time).toBe("2026-08-17T17:00:00+08:00");
    expect(chinese.start_time).toBe("2026-08-17T10:00:00+08:00");
    expect(chinese.location).toContain("理科二号楼");
    expect(csAdapter.discover(doc).urls.some((item) => item.kind === "pagination")).toBe(true);
  });

  it("extracts Life Sciences list items with missing optional fields", async () => {
    const doc = await document("tests/fixtures/bio/list.html", bioAdapter.metadata.id, "https://web.bio.pku.edu.cn/index.php?a=news_jz&cid=29&m=Index");
    const candidates = bioAdapter.parse(doc);
    expect(candidates).toHaveLength(2);
    expect(normalizeCandidate(candidates[0]).event?.start_time).toBe("2026-10-16T00:00:00+08:00");
    expect(normalizeCandidate(candidates[0]).event?.all_day).toBe(true);
    expect(normalizeCandidate(candidates[0]).event?.location).toContain("Jinguang");
  });

  it("extracts GSE list and detail-style fields", async () => {
    const list = await document("tests/fixtures/gse/list.html", gseAdapter.metadata.id, "https://www.gse.pku.edu.cn/syxw/jzxx/index.htm");
    const candidates = gseAdapter.parse(list);
    expect(candidates).toHaveLength(2);
    expect(candidates[0].publication_time_raw).toBe("2026-09-03");
    const detail = await document("tests/fixtures/gse/detail.html", gseAdapter.metadata.id, "https://www.gse.pku.edu.cn/syxw/jzxx/2026/0903/347.htm");
    const detailed = gseAdapter.parse(detail)[0];
    expect(normalizeCandidate(detailed).event?.speakers).toEqual(["Jane Doe"]);
    expect(normalizeCandidate(detailed).event?.description).toContain("higher education");
  });

  it("isolates malformed pages", async () => {
    const doc = await document("tests/fixtures/malformed.html", gseAdapter.metadata.id, "https://www.gse.pku.edu.cn/syxw/jzxx/index.htm");
    expect(() => gseAdapter.parse(doc)).not.toThrow();
    expect(gseAdapter.parse(doc)).toEqual([]);
  });

  it.each([
    ["cs", csAdapter],
    ["gse", gseAdapter],
  ] as const)("discovers %s detail URLs without requiring title keywords", async (key, adapter) => {
    const doc = await document(`tests/fixtures/${key}/list.html`, adapter.metadata.id, adapter.metadata.seedUrls[0]);
    const detailUrls = adapter.discover(doc).urls.filter((link) => link.kind === "detail").map((link) => link.url);
    for (const candidate of adapter.parse(doc)) expect(detailUrls).toContain(candidate.source_url);
    // A relevant detail link is identified by its source path, even if its label is just a title.
    doc.body = '<a href="' + adapter.parse(doc)[0].source_url + '">Some research topic</a><a href="https://example.test/unrelated">lecture</a>';
    expect(adapter.discover(doc).urls).toHaveLength(1);
    expect(adapter.discover(doc).urls[0].kind).toBe("detail");
  });

  it.each(["index.htm", "index_2.htm", "index2.htm"])("parses GSE %s as a list and excludes pagination from events", async (page) => {
    const doc = await document("tests/fixtures/gse/list.html", gseAdapter.metadata.id, `https://www.gse.pku.edu.cn/syxw/jzxx/${page}`);
    doc.body += '<a href="index2.htm">2</a><a href="index_3.htm">3</a>';
    expect(gseAdapter.parse(doc)).toHaveLength(2);
    const discovery = gseAdapter.discover(doc).urls;
    expect(discovery.filter((link) => link.kind === "detail")).toHaveLength(2);
    expect(discovery.filter((link) => link.kind === "pagination")).toHaveLength(3);
  });
});

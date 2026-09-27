import { describe, expect, it, vi } from "vitest";
import { PublicHttpFetcher } from "../src/fetcher/public-http-fetcher.js";

function response(body: string, status = 200) { return new Response(body, { status, headers: { "content-type": "text/html" } }); }

describe("public fetcher", () => {
  it("checks robots and returns provenance plus content hash", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(response("User-agent: *\nDisallow: /private")).mockResolvedValueOnce(response("<html>ok</html>"));
    const fetcher = new PublicHttpFetcher({ fetchImpl, minDelayMs: 0 });
    const result = await fetcher.fetch({ url: "https://example.test/page" });
    expect(result.status).toBe(200);
    expect(result.requestedUrl).toContain("/page");
    expect(result.contentHash).toHaveLength(64);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
  it("does not fetch a robots-disallowed path", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(response("User-agent: *\nDisallow: /private"));
    const fetcher = new PublicHttpFetcher({ fetchImpl, minDelayMs: 0 });
    await expect(fetcher.fetch({ url: "https://example.test/private/x" })).rejects.toMatchObject({ category: "robots_disallowed" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});

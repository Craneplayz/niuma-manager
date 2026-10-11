import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import { authenticatedAttachmentReader, MAX_ATTACHMENT_BYTES } from "../src/pku-attachments.js";

const transport = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("node:https", () => ({ request: transport.request }));
afterEach(() => transport.request.mockReset());
const url = "https://course.pku.edu.cn/bbcswebdav/pid-1-dt-content-rid-2/HW.txt";
const preview = "https://course.pku.edu.cn/webapps/blackboard/execute/content/file?cmd=view&content_id=_1_1&course_id=_2_1";
const opaque = "https://course.pku.edu.cn/bbcswebdav/pid-1-dt-content-rid-2/xid-2_1";
function response(status: number, headers: Record<string, string>, chunks: Buffer[] = []) {
  transport.request.mockImplementationOnce((_url, _options, callback) => {
    const req = new EventEmitter() as EventEmitter & { end: () => void; destroy: (error: Error) => void };
    req.destroy = (error) => { req.emit("error", error); req.emit("close"); };
    req.end = () => {
      const res = Object.assign(new EventEmitter(), { statusCode: status, headers, resume: vi.fn(), destroy: () => res.emit("error", new Error("closed")) });
      callback(res);
      for (const chunk of chunks) res.emit("data", chunk);
      res.emit("end");
      req.emit("close");
    };
    return req;
  });
}
function context() { return { cookies: vi.fn().mockResolvedValue([{ name: "synthetic", value: "session" }]) }; }

describe("authenticated attachment transport boundaries", () => {
  it.each(['iframe src', 'embed src', 'object data'])("resolves opaque file → preview → embedded teacher file (%s)", async (element) => {
    const [tag, attribute] = element.split(" ");
    response(302, { location: preview });
    response(200, { "content-type": "text/html; charset=utf-8" }, [Buffer.from(`<html><${tag} ${attribute}="${url}"></${tag}><a href="https://evil.test/ignored">Navigation</a></html>`)]);
    response(200, { "content-type": "text/plain" }, [Buffer.from("Submit report.")]);
    const session = context();
    const file = await authenticatedAttachmentReader(session)(opaque);
    expect(new TextDecoder().decode(file.bytes)).toBe("Submit report.");
    expect(session.cookies.mock.calls.map(([href]) => href)).toEqual([opaque, preview, url]);
    expect(transport.request.mock.calls.map(([href]) => href)).toEqual([opaque, preview, url]);
  });
  it.each([
    preview.replace("cmd=view", "cmd=delete"), `${preview}&attempt_id=_1_1`, `${preview}&cmd=view`,
    preview.replace("_1_1", "private"), preview.replace("course.pku.edu.cn", "evil.test"),
  ])("rejects unrecognized preview actions before accessing cookies: %s", async (href) => {
    const session = context();
    await expect(authenticatedAttachmentReader(session)(href)).rejects.toThrow("untrusted_attachment_url");
    expect(session.cookies).not.toHaveBeenCalled();
    expect(transport.request).not.toHaveBeenCalled();
  });
  it.each([
    '<form>Login</form>', '<iframe src="https://evil.test/a.pdf"></iframe>',
    '<object data="/bbcswebdav/courses/c1/submissions/private.pdf"></object>',
    `<iframe src="${preview}"></iframe>`,
    `<iframe src="${url}"></iframe><embed src="${url.replace("HW.txt", "Other.txt")}">`,
  ])("does not request login, external, private, nested or ambiguous preview embeds", async (html) => {
    response(200, { "content-type": "text/html" }, [Buffer.from(html)]);
    const session = context();
    await expect(authenticatedAttachmentReader(session)(preview)).rejects.toThrow("authentication_required");
    expect(session.cookies).toHaveBeenCalledOnce();
    expect(transport.request).toHaveBeenCalledOnce();
  });
  it("counts preview resolution against the shared redirect limit", async () => {
    response(302, { location: opaque });
    response(302, { location: opaque });
    response(302, { location: preview });
    response(200, { "content-type": "text/html" }, [Buffer.from(`<iframe src="${url}"></iframe>`)]);
    await expect(authenticatedAttachmentReader(context())(opaque)).rejects.toThrow("authentication_required");
    expect(transport.request).toHaveBeenCalledTimes(4);
  });
  it("never obtains or sends cookies for an untrusted initial URL", async () => {
    const session = context();
    await expect(authenticatedAttachmentReader(session)("https://evil.test/a.txt")).rejects.toThrow("untrusted_attachment_url");
    expect(session.cookies).not.toHaveBeenCalled();
    expect(transport.request).not.toHaveBeenCalled();
  });
  it("follows a teacher-content redirect but blocks an external redirect before requesting it", async () => {
    response(302, { location: "/bbcswebdav/pid-1-dt-content-rid-3/HW.txt" });
    response(200, { "content-type": "text/plain; charset=utf-8" }, [Buffer.from("Submit")]);
    const session = context();
    expect((await authenticatedAttachmentReader(session)(url)).contentType).toBe("text/plain");
    expect(session.cookies).toHaveBeenCalledTimes(2);
    response(302, { location: "https://evil.test/a.txt" });
    await expect(authenticatedAttachmentReader(session)(url)).rejects.toThrow("authentication_required");
    expect(session.cookies).toHaveBeenCalledTimes(3);
    expect(transport.request).toHaveBeenCalledTimes(3);
  });
  it("rejects declared oversize and streaming oversize without Content-Length", async () => {
    response(200, { "content-length": String(MAX_ATTACHMENT_BYTES + 1) });
    await expect(authenticatedAttachmentReader(context())(url)).rejects.toThrow("too_large");
    response(200, {}, [Buffer.alloc(MAX_ATTACHMENT_BYTES), Buffer.from("x")]);
    await expect(authenticatedAttachmentReader(context())(url)).rejects.toThrow("too_large");
  });
  it("rejects malformed redirects without throwing outside the request", async () => {
    response(302, { location: "https://[invalid" });
    await expect(authenticatedAttachmentReader(context())(url)).rejects.toThrow("authentication_required");
  });
  it.each([401, 403, 500])("preserves a structured failure on HTTP %i", async (status) => {
    response(status, {});
    await expect(authenticatedAttachmentReader(context())(url)).rejects.toThrow(status === 500 ? "http_error" : "authentication_required");
  });
});

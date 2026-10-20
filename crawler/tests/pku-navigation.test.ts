import type { Page } from "playwright";
import { describe, expect, it, vi } from "vitest";
import { navigateCoursePage } from "../src/pku-homework.js";

function page(status = 200, url = "https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp", passwordFields = 0) {
  return {
    goto: vi.fn().mockResolvedValue({ ok: () => status >= 200 && status < 300, status: () => status }),
    url: () => url,
    locator: vi.fn().mockReturnValue({ count: async () => passwordFields }),
  } as unknown as Pick<Page, "goto" | "url" | "locator">;
}

describe("authenticated course navigation", () => {
  it.each([403, 404, 500])("rejects HTTP %s instead of accepting an empty source list", async (status) => {
    await expect(navigateCoursePage(page(status), "https://course.pku.edu.cn/course")).rejects.toThrow(`HTTP ${status}`);
  });
  it.each([
    "https://iaaa.pku.edu.cn/iaaa/oauth.jsp?ticket=private",
    "https://course.pku.edu.cn/webapps/bb-sso-BBLEARN/login.html",
    "https://course.pku.edu.cn/",
  ])("rejects login redirects without including URL credentials: %s", async (url) => {
    await expect(navigateCoursePage(page(200, url), "https://course.pku.edu.cn/course")).rejects.toThrow("login may have expired");
  });
  it("rejects a login form served at a nominal course URL", async () => {
    await expect(navigateCoursePage(page(200, undefined, 1), "https://course.pku.edu.cn/course")).rejects.toThrow("login may have expired");
  });
  it("propagates navigation failures without continuing on the previous page", async () => {
    const mock = page();
    const timeout = new Error("PRIVATE_URL_TOKEN");
    timeout.name = "TimeoutError";
    vi.mocked(mock.goto).mockRejectedValue(timeout);
    await expect(navigateCoursePage(mock, "https://course.pku.edu.cn/course")).rejects.toMatchObject({stage:"navigation", code:"timeout", message:"timeout"});
    expect(mock.locator).not.toHaveBeenCalled();
  });
  it("accepts a successful authenticated listing", async () => {
    await expect(navigateCoursePage(page(), "https://course.pku.edu.cn/course")).resolves.toBeUndefined();
  });
});

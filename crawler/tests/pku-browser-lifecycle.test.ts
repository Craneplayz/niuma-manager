import { afterEach, describe, expect, it, vi } from "vitest";
const { launch, credentials } = vi.hoisted(() => ({ launch: vi.fn(), credentials: vi.fn() }));
vi.mock("playwright", () => ({ chromium: { launch } }));
vi.mock("node:fs/promises", async importOriginal => ({
  ...await importOriginal<typeof import("node:fs/promises")>(), readFile: credentials,
}));
import { scrapePkuHomeworks } from "../src/pku-homework.js";

afterEach(() => { vi.resetAllMocks(); });
describe("crawler browser lifetime on early failures", () => {
  it.each(["context", "page", "login"])("closes the launched browser after %s failure", async stage => {
    credentials.mockResolvedValue("synthetic-user\nsynthetic-password");
    const failure = new Error("PRIVATE_PATH_URL_PASSWORD");
    const page = { goto: vi.fn().mockRejectedValue(failure) };
    const context = { newPage: vi.fn().mockResolvedValue(page) };
    const browser = { newContext: vi.fn().mockResolvedValue(context), close: vi.fn().mockResolvedValue(undefined) };
    if (stage === "context") browser.newContext.mockRejectedValue(failure);
    if (stage === "page") context.newPage.mockRejectedValue(failure);
    launch.mockResolvedValue(browser);
    await expect(scrapePkuHomeworks()).rejects.toMatchObject({
      stage: stage === "login" ? "login" : "browser", code: "operation_failed", message: "operation_failed",
    });
    expect(browser.close).toHaveBeenCalledTimes(1);
  });
});

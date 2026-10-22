import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { collectionStage, collectionFailureEvent, CollectionFailure } from "../src/collection-failure.js";

describe("safe collection failures", () => {
  it("preserves stage without leaking provider exception or URL", async () => {
    const events = [];
    for (const stage of ["credentials", "browser", "login", "navigation", "export", "collection"] as const) {
      try { await collectionStage(stage, async () => { throw new Error("PRIVATE_URL_ACCOUNT_KEY"); }); }
      catch (error) { events.push(collectionFailureEvent(error)); }
    }
    expect(JSON.stringify(events)).not.toContain("PRIVATE");
    expect(events.map(e => e.stage)).toEqual(["credentials", "browser", "login", "navigation", "export", "collection"]);
    const checked = spawnSync(process.env.CONTRACT_PYTHON ?? "python", ["-c",
      "import json,sys; sys.path.insert(0,'..'); from contracts.collection_failure import SCHEMA,parse_failure; from jsonschema import Draft202012Validator; data=json.load(sys.stdin); [Draft202012Validator(SCHEMA).validate(e) for e in data]; assert all(parse_failure(json.dumps(e))==e for e in data)"],
    { input: JSON.stringify(events), encoding: "utf8", timeout: 15_000, windowsHide: true });
    expect(checked.status, checked.stderr).toBe(0);
  });
  it("round trips shared examples and preserves a tagged failure", async () => {
    const samples = JSON.parse(readFileSync("../contracts/examples/collection-failures-v01.json", "utf8"));
    for (const sample of samples) {
      const failure = new CollectionFailure(sample.stage, sample.code, "PRIVATE");
      expect(collectionFailureEvent(failure)).toEqual(sample);
      await expect(collectionStage("collection", async () => { throw failure; })).rejects.toBe(failure);
    }
    expect(await collectionStage("collection", async () => 42)).toBe(42);
  });
});

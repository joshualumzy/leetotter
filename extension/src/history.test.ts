import { describe, expect, it } from "vitest";
import { readHistoryCache } from "./history";

describe("NeetCode history cache", () => {
  it("flattens category lists and keeps LeetCode URLs", () => {
    expect(readHistoryCache(JSON.stringify({ completed: { Trees: ["https://leetcode.com/problems/two-sum/"], Graphs: [] } }))).toEqual(["https://leetcode.com/problems/two-sum/"]);
  });
  it("names missing and malformed cache errors", () => {
    expect(() => readHistoryCache(null)).toThrow("synced-progress-cache is missing");
    expect(() => readHistoryCache("not json")).toThrow("synced-progress-cache is not valid JSON");
    expect(() => readHistoryCache('{"completed":{"Trees":"wrong"}}')).toThrow("synced-progress-cache completed must map categories to URL lists");
  });
});

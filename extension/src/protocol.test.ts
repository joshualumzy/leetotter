import { describe, expect, it } from "vitest";
import { parseMessage, validateReaderPayload, validateImportUrls } from "./protocol";

describe("wire messages", () => {
  it("accepts null failing_case like the Python validator", () => {
    expect(validateReaderPayload("run_result", { slug: "two-sum", cases: [{ passed: true, error_type: null, error_message: "", runtime: "0.1", failing_case: null }] })).toBeDefined();
  });
  it("accepts a typed status envelope", () => {
    expect(parseMessage('{"type":"status","ts":"2026-10-15T00:00:00Z","state":"connected"}')).toEqual({
      type: "status", ts: "2026-10-15T00:00:00Z", state: "connected",
    });
  });

  it("rejects an unknown type instead of dropping it", () => {
    expect(() => parseMessage('{"type":"mystery","ts":"2026-10-15T00:00:00Z"}')).toThrow("Unknown message type: mystery");
  });

  it("rejects malformed envelopes", () => {
    expect(() => parseMessage('{"type":"status","state":"connected"}')).toThrow("Message requires ts");
  });

  it("validates import URLs and result messages", () => {
    expect(validateImportUrls(["https://leetcode.com/problems/two-sum/"])).toEqual(["https://leetcode.com/problems/two-sum/"]);
    expect(() => validateImportUrls(["https://evil.test/problems/two-sum/"])).toThrow("Invalid import_history");
    expect(parseMessage('{"type":"import_result","ts":"now","result":{"already_imported":false,"matched":2,"unmatched":[]}}')).toMatchObject({ type: "import_result", result: { matched: 2 } });
    expect(() => parseMessage('{"type":"import_result","ts":"now","result":{"matched":"2"}}')).toThrow("Invalid import_result");
  });
});

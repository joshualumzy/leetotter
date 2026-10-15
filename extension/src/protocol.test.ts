import { describe, expect, it } from "vitest";
import { parseMessage, validateReaderPayload, validateImportUrls } from "./protocol";

describe("wire messages", () => {
  it("accepts a problem statement with optional truncation and extra fields on both wire paths", () => {
    const payload = { slug: "duplicate-integer", statement: "Given an integer array nums...", truncated: true, source: "question-tab" };
    expect(validateReaderPayload("problem_statement", payload)).toEqual(payload);
    expect(parseMessage(JSON.stringify({ type: "problem_statement", ts: "now", payload }))).toMatchObject({ type: "problem_statement", payload });
    for (const invalid of [
      { slug: "", statement: "text" }, { slug: "duplicate-integer", statement: "" },
      { slug: "duplicate-integer", statement: "x".repeat(20_001) },
      { slug: "duplicate-integer", statement: "text", truncated: "true" },
    ]) {
      expect(() => validateReaderPayload("problem_statement", invalid)).toThrow("Invalid problem_statement payload");
      expect(() => parseMessage(JSON.stringify({ type: "problem_statement", ts: "now", payload: invalid }))).toThrow("Invalid problem_statement payload");
    }
  });
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
  it("accepts the companion's closed and restart pause reasons", () => {
    const state = { type: "session_state", ts: "now", state: "paused", mode: "paused", check_message: null, budget_minutes: 45, active_seconds: 1394, running: false, overrun_prompt: false, problem: null, accepted: false };
    for (const paused_reason of ["closed", "restart"]) expect(parseMessage(JSON.stringify({ ...state, paused_reason }))).toMatchObject({ paused_reason });
  });
});

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
  it("validates Tutor requests and replies", () => {
    const valid = [
      { type: "help_request" }, { type: "chat_get" }, { type: "chat_message", payload: { text: "Why?" } },
      { type: "tutor_start", turn_id: 1, request: "help", press_index: 1 },
      { type: "tutor_chunk", turn_id: 1, attempt: 1, text: "Try " },
      { type: "tutor_done", turn_id: 1, reply_text: "Try a set", gap: "pattern", gap_note: null, help_kind: "hint", follow_up_question: null },
      { type: "tutor_error", turn_id: null, message: "Paused" },
      { type: "chat_state", turns: [{ turn_id: 1, kind: "press", press_index: 1 }, { turn_id: 1, kind: "reply", text: "Hint", gap: "pattern", gap_note: null, help_kind: "hint" }], streaming: null },
    ];
    for (const message of valid) expect(parseMessage(JSON.stringify({ ...message, ts: "now" }))).toMatchObject(message);
    const invalid = [
      { type: "help_request", payload: {} }, { type: "chat_get", payload: {} }, { type: "chat_message", payload: { text: " ".repeat(4001) } },
      { type: "tutor_start", turn_id: "1", request: "help", press_index: 1 },
      { type: "tutor_chunk", turn_id: 1, attempt: 0, text: "x" },
      { type: "tutor_done", turn_id: 1, reply_text: "x", gap: "g", gap_note: "", help_kind: "hint" },
      { type: "tutor_done", turn_id: 1, reply_text: "", gap: "g", gap_note: "", help_kind: "hint", follow_up_question: null },
      { type: "tutor_error", turn_id: null, message: "" },
      { type: "chat_state", turns: [{ turn_id: 1, kind: "reply", text: "x" }], streaming: null },
    ];
    for (const message of invalid) expect(() => parseMessage(JSON.stringify({ ...message, ts: "now" }))).toThrow();
  });
});

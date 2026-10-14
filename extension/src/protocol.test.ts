import { describe, expect, it } from "vitest";
import { parseMessage, validateReaderPayload } from "./protocol";

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
});

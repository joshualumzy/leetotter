import { describe, expect, it } from "vitest";
import { parseMessage } from "./protocol";

describe("wire messages", () => {
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

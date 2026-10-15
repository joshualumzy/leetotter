import { describe, expect, it } from "vitest";
import { composerAvailability, messageToSend, enterAction } from "./composer-rules";

describe("composer rules", () => {
  it("explains unavailable, paused, and streaming states", () => {
    expect(composerAvailability("active", true, false)).toEqual({ enabled: true, typing: true, reason: "" });
    expect(composerAvailability("paused", true, false).reason).toMatch(/paused/i);
    expect(composerAvailability("active", false, false).reason).toMatch(/problem/i);
    expect(composerAvailability("active", true, true)).toMatchObject({ enabled: false, typing: true });
    expect(composerAvailability("active", true, true).reason).toMatch(/still answering/i);
    expect(composerAvailability("paused", true, false).typing).toBe(false);
  });
  it("rejects whitespace and over-limit text without truncating", () => {
    expect(messageToSend("  ")).toEqual({ text: null, error: "" });
    expect(messageToSend("x".repeat(4001)).error).toMatch(/4000/);
    expect(messageToSend("😀".repeat(4000)).text).toBe("😀".repeat(4000));
    expect(messageToSend("  hello  ")).toEqual({ text: "hello", error: "" });
  });
  it("uses Enter to send and Shift+Enter for a newline", () => {
    expect(enterAction("Enter", false)).toBe("send");
    expect(enterAction("Enter", true)).toBe("newline");
  });
});

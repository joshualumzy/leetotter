import { describe, expect, it, vi } from "vitest";
import { StreamPacer } from "./stream-pacer";

describe("stream pacer", () => {
  it("reveals about 16 words a second and finishes buffered text after done", () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    const pacer = new StreamPacer();
    pacer.push("one two three four ");
    expect(pacer.tick(Date.now()).text).toBe("");
    vi.advanceTimersByTime(63);
    expect(pacer.tick(Date.now()).text).toBe("one ");
    pacer.finish("one two three four");
    vi.advanceTimersByTime(63);
    expect(pacer.tick(Date.now()).text).toBe("one two ");
    vi.advanceTimersByTime(174);
    expect(pacer.tick(Date.now()).text).toBe("one two three four");
    vi.useRealTimers();
  });

  it("accelerates for a large buffer and cleanly replaces a retry", () => {
    const pacer = new StreamPacer();
    pacer.push(Array.from({ length: 100 }, (_, i) => `w${i} `).join(""));
    pacer.tick(0);
    expect(pacer.tick(1000).wordCount).toBeGreaterThan(16);
    pacer.replace("new answer ");
    expect(pacer.tick(1000).text).toBe("");
    expect(pacer.tick(1100).text).toBe("new ");
  });
  it("paces words added by the validated final reply", () => {
    const pacer = new StreamPacer();
    pacer.push("Try "); pacer.tick(0);
    pacer.finish("Try a set");
    expect(pacer.tick(63).text).toBe("Try ");
    expect(pacer.tick(126).text).toBe("Try a ");
    expect(pacer.tick(190).text).toBe("Try a set");
  });
});

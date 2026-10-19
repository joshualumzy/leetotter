import { describe, expect, it } from "vitest";
import { categoryCount, earlierLabel, healthLine, progressSummary, recapDate, strongSummary, untriedLine } from "./tabs-view";

describe("Recaps and Progress view text", () => {
  it("dates a recap by the day it started", () => {
    expect(recapDate("2026-10-15T09:00:00+00:00")).toBe("15 Oct 2026");
    expect(recapDate("2026-01-02T23:30:00+00:00")).toBe("2 Jan 2026");
    expect(recapDate("not a date")).toBe("not a date");
  });
  it("reads the 150 in three parts and the strong and rusty counts", () => {
    const header = { on_your_own: 62, coming_back: 40, to_do: 48, strong: 5, rusty: 2, total: 150 };
    expect(progressSummary(header)).toBe("62 on your own · 40 coming back · 48 to do");
    expect(strongSummary(header, 18)).toBe("5 of 18 strong · 2 rusty");
  });
  it("keeps levels as counts, not percentages", () => {
    expect(categoryCount(7, 11)).toBe("7 of 11");
    expect(earlierLabel(3)).toBe("3 earlier");
  });
  it("shows untried problems only as counts", () => {
    expect(untriedLine({ not_tried: 4, imported_only: 2 })).toBe("4 not tried · 2 solved before Leetotter, not yet tested");
    expect(untriedLine({ not_tried: 0, imported_only: 1 })).toBe("1 solved before Leetotter, not yet tested");
    expect(untriedLine({ not_tried: 0, imported_only: 0 })).toBe("");
  });
  it("states grading health quietly in plain words", () => {
    expect(healthLine({ graded: 16, disagreements: 1, disagreement_share: 1 / 16, ungraded: 1 })).toBe("Grading health: graders agree on 94% · 1 attempt ungraded");
    expect(healthLine({ graded: 16, disagreements: 1, disagreement_share: 1 / 16, ungraded: 0 })).toBe("Grading health: graders agree on 94%");
    expect(healthLine({ graded: 0, disagreements: 0, disagreement_share: 0, ungraded: 2 })).toBe("Grading health: 2 attempts ungraded");
    expect(healthLine({ graded: 0, disagreements: 0, disagreement_share: 0, ungraded: 0 })).toBe("Grading health: nothing graded yet");
  });
});

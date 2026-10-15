import { expect, it } from "vitest";
import { reactionForReaderEvent } from "./reader-reaction";

it("maps accepted reader results to simple reactions", () => {
  expect(reactionForReaderEvent("code_changed", {})).toBe("typing");
  expect(reactionForReaderEvent("run_result", { cases: [{ passed: true }, { passed: true }] })).toBe("run_pass");
  expect(reactionForReaderEvent("run_result", { cases: [{ passed: true }, { passed: false }] })).toBe("run_fail");
  expect(reactionForReaderEvent("submit_result", { accepted: true })).toBe("submit_pass");
  expect(reactionForReaderEvent("submit_result", { accepted: false })).toBe("submit_fail");
  expect(reactionForReaderEvent("code_snapshot", {})).toBeNull();
});

import type { ReaderMessageType } from "./protocol";

export type ReaderReaction = "typing" | "run_pass" | "run_fail" | "submit_fail" | "submit_pass";

export function reactionForReaderEvent(type: ReaderMessageType, payload: Record<string, unknown>): ReaderReaction | null {
  if (type === "code_changed") return "typing";
  if (type === "run_result") {
    const cases = payload.cases as { passed: boolean }[];
    return cases.every(result => result.passed) ? "run_pass" : "run_fail";
  }
  if (type === "submit_result") return payload.accepted ? "submit_pass" : "submit_fail";
  return null;
}

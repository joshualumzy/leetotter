// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { judgeError, lineDiff, parseRun, parseSubmit } from "./reader";
import runRequest from "./fixtures/run_request.json";
import runAccepted from "./fixtures/run_accepted_response.json";
import runWrong from "./fixtures/run_wrong_answer_response.json";
import submitRequest from "./fixtures/submit_request.json";
import submitAccepted from "./fixtures/submit_accepted_response.json";
import submitWrong from "./fixtures/submit_wrong_answer_response.json";

describe("NeetCode captured traffic", () => {
  it("parses accepted and failed Run cases", () => {
    expect(parseRun(runRequest, runAccepted).cases.map(c => c.passed)).toEqual([true, true]);
    expect(parseRun(runRequest, runWrong).cases[0]).toMatchObject({
      passed: false, error_type: "wrong_answer", runtime: "0.027",
      failing_case: { input: "nums=[1,2,3,3]\n", expected: "true", actual: "false" },
    });
  });
  it("parses accepted and failed Submit results", () => {
    expect(parseSubmit(submitRequest, submitAccepted)).toMatchObject({ accepted: true, passed: 34, total: 34, runtime: "0.032", memory: 14184 });
    expect(parseSubmit(submitRequest, submitWrong)).toMatchObject({
      accepted: false, passed: 0, total: 34,
      failing_case: { input: "nums=[1,2,3,3]", expected: "true", actual: "false" },
    });
  });
  it("maps every specified Judge0 status without guessing", () => {
    expect([3,4,5,6,7,8,9,10,11,12,13,99].map(judgeError)).toEqual([
      null,"wrong_answer","time_limit","compile_error","runtime_error","runtime_error","runtime_error","runtime_error","runtime_error","runtime_error","internal_error","unknown_99",
    ]);
  });
  it("maps queued, processing and format errors", () => {
    expect([1, 2, 14, 99].map(judgeError)).toEqual(["in_queue", "processing", "exec_format_error", "unknown_99"]);
  });
  it("diffs lines against the previous snapshot", () => {
    expect(lineDiff("a\nb\nc", "a\nx\nc")).toEqual({ start_line: 2, removed: ["b"], added: ["x"] });
  });
});

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
    const state = { type: "session_state", ts: "now", state: "paused", mode: "paused", check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 1394, running: false, problem: null, accepted: false };
    for (const paused_reason of ["closed", "restart"]) expect(parseMessage(JSON.stringify({ ...state, paused_reason }))).toMatchObject({ paused_reason });
  });
  it("accepts a cap, time-up card and debrief mode", () => {
    const state = { type: "session_state", ts: "now", state: "active", mode: "debrief", paused_reason: null, check_message: null, cap_minutes: 35, cap_seconds_left: 0, time_up: { can_extend: false }, active_seconds: 2100, running: true, problem: { slug: "daily-temperatures", title: "Daily Temperatures" }, accepted: false, reference_status: "not_checked_yet", reference_message: "Reference not checked yet" };
    expect(parseMessage(JSON.stringify(state))).toMatchObject({ mode: "debrief", cap_seconds_left: 0, time_up: { can_extend: false }, reference_message: "Reference not checked yet" });
    expect(() => parseMessage(JSON.stringify({ ...state, time_up: {} }))).toThrow("Invalid session_state payload");
    expect(() => parseMessage(JSON.stringify({ ...state, mode: "overrun" }))).toThrow("Invalid session_state payload");
  });
  it("sends session_start with no budget and rejects the retired budget and overrun messages", () => {
    expect(parseMessage('{"type":"session_start","ts":"now"}')).toMatchObject({ type: "session_start" });
    expect(parseMessage('{"type":"session_start","ts":"now","payload":{}}')).toMatchObject({ type: "session_start" });
    expect(() => parseMessage('{"type":"session_start","ts":"now","payload":{"budget_minutes":45}}')).toThrow("Invalid session_start payload");
    expect(() => parseMessage('{"type":"session_overrun","ts":"now","payload":{"choice":"finish"}}')).toThrow("Unknown message type: session_overrun");
  });
  it("validates Tutor requests and replies", () => {
    const valid = [
      { type: "help_request" }, { type: "chat_get" }, { type: "chat_message", payload: { text: "Why?" } },
      { type: "tutor_start", turn_id: 1, request: "help", press_index: 1 },
      { type: "tutor_chunk", turn_id: 1, attempt: 0, text: "Try " }, // the companion counts attempts from 0; a retry is 1
      { type: "tutor_chunk", turn_id: 1, attempt: 1, text: "Try again " },
      { type: "tutor_done", turn_id: 1, reply_text: "Try a set", gap: "pattern", gap_note: null, help_kind: "hint", follow_up_question: null },
      { type: "tutor_error", turn_id: null, message: "Paused" },
      { type: "chat_state", turns: [{ turn_id: 1, kind: "press", press_index: 1 }, { turn_id: 1, kind: "reply", text: "Hint", gap: "pattern", gap_note: null, help_kind: "hint" }], streaming: null },
    ];
    for (const message of valid) expect(parseMessage(JSON.stringify({ ...message, ts: "now" }))).toMatchObject(message);
    const invalid = [
      { type: "help_request", payload: {} }, { type: "chat_get", payload: {} }, { type: "chat_message", payload: { text: " ".repeat(4001) } },
      { type: "tutor_start", turn_id: "1", request: "help", press_index: 1 },
      { type: "tutor_chunk", turn_id: 1, attempt: -1, text: "x" },
      { type: "tutor_done", turn_id: 1, reply_text: "x", gap: "g", gap_note: "", help_kind: "hint" },
      { type: "tutor_done", turn_id: 1, reply_text: "", gap: "g", gap_note: "", help_kind: "hint", follow_up_question: null },
      { type: "tutor_error", turn_id: null, message: "" },
      { type: "chat_state", turns: [{ turn_id: 1, kind: "reply", text: "x" }], streaming: null },
    ];
    for (const message of invalid) expect(() => parseMessage(JSON.stringify({ ...message, ts: "now" }))).toThrow();
  });
});


describe("Planner, recap, time cap and reference messages", () => {
  it("accepts the recorded suggestion card by title, cap and steer only", () => {
    const suggestion = { type: "suggestion", ts: "2026-10-19T10:00:00Z", slug: "house-robber", title: "House Robber", difficulty: "Medium", cap_minutes: 35, steer: "more dynamic programming", message: "A dynamic programming problem next.", source: "planner" };
    expect(parseMessage(JSON.stringify(suggestion))).toMatchObject(suggestion);
    const fallback = { ...suggestion, slug: "coin-change", title: "Coin Change", steer: null, message: "The Planner didn't answer; here's a code-ranked suggestion.", source: "fallback" };
    expect(parseMessage(JSON.stringify(fallback))).toMatchObject(fallback);
    for (const invalid of [
      { ...suggestion, title: "" }, { ...suggestion, difficulty: "Brutal" },
      { ...suggestion, cap_minutes: 0 }, { ...suggestion, steer: 3 }, { ...suggestion, source: "model" },
    ]) expect(() => parseMessage(JSON.stringify(invalid))).toThrow("Invalid suggestion payload");
  });
  it("accepts the Planner's client messages and planner_open", () => {
    const valid = [
      { type: "planner_get" }, { type: "planner_go" }, { type: "planner_swap" }, { type: "planner_clear" },
      { type: "planner_chat", payload: { text: "more dynamic programming" } },
      { type: "planner_open", slug: "house-robber", title: "House Robber" },
    ];
    for (const message of valid) expect(parseMessage(JSON.stringify({ ...message, ts: "now" }))).toMatchObject(message);
    expect(() => parseMessage('{"type":"planner_go","ts":"now","payload":{}}')).toThrow();
    expect(() => parseMessage('{"type":"planner_chat","ts":"now","payload":{"text":" "}}')).toThrow();
    expect(() => parseMessage('{"type":"planner_open","ts":"now","slug":"house-robber"}')).toThrow("Invalid planner_open payload");
  });
  it("accepts the recap states and noted message exactly as the companion sends them", () => {
    for (const status of ["preparing", "ready", "failed", "feedback_failed"] as const) {
      const recap = { type: "recap_state", session_id: 12, status, bullets: status === "ready" ? ["Dropped: visited marking."] : [], ...(status === "failed" || status === "feedback_failed" ? { message: "Session review is missing: offline" } : {}) };
      const parsed = parseMessage(JSON.stringify(recap));
      expect(parsed).toMatchObject(recap);
      if (status !== "preparing") expect(parsed).not.toHaveProperty("ts");
    }
    expect(parseMessage('{"type":"recap_noted","session_id":12,"note_id":9,"message":"Noted. For a grade correction, use Progress."}')).toMatchObject({ session_id: 12, note_id: 9 });
    expect(parseMessage('{"type":"recap_get","ts":"now","payload":{"session_id":12}}')).toMatchObject({ type: "recap_get" });
    expect(parseMessage('{"type":"recap_seen","ts":"now","payload":{"session_id":12}}')).toMatchObject({ type: "recap_seen" });
    expect(() => parseMessage('{"type":"recap_seen","ts":"now","payload":{"session_id":0}}')).toThrow("Invalid recap_seen payload");
    expect(parseMessage('{"type":"recap_feedback","ts":"now","payload":{"session_id":12,"text":"Missed the window idea"}}')).toMatchObject({ type: "recap_feedback" });
    expect(() => parseMessage('{"type":"recap_feedback","ts":"now","payload":{"session_id":12,"text":" "}}')).toThrow("Invalid recap_feedback text");
    expect(() => parseMessage('{"type":"recap_state","session_id":0,"status":"ready","bullets":[]}')).toThrow("Invalid recap_state payload");
    expect(() => parseMessage('{"type":"recap_state","session_id":12,"status":"ready","bullets":[1]}')).toThrow("Invalid recap_state payload");
  });
  it("accepts the time-up, done and skip senders", () => {
    expect(parseMessage('{"type":"session_time_up","ts":"now","payload":{"choice":"extend"}}')).toMatchObject({ type: "session_time_up" });
    expect(parseMessage('{"type":"session_time_up","ts":"now","payload":{"choice":"debrief"}}')).toMatchObject({ type: "session_time_up" });
    expect(parseMessage('{"type":"session_done","ts":"now"}')).toMatchObject({ type: "session_done" });
    expect(parseMessage('{"type":"session_skip","ts":"now"}')).toMatchObject({ type: "session_skip" });
    expect(() => parseMessage('{"type":"session_time_up","ts":"now","payload":{"choice":"finish"}}')).toThrow("Invalid session_time_up payload");
    expect(() => parseMessage('{"type":"session_done","ts":"now","payload":{}}')).toThrow();
  });
  it("accepts the reference review messages and flags", () => {
    const state = { type: "reference_state", ts: "now", count: 1, references: [{ slug: "top-k-elements-in-list", title: "Top K Frequent Elements", uncertain: ["heap.complexity"], prompt: "Settle this reference", reason: "Reference is not ready yet" }] };
    expect(parseMessage(JSON.stringify(state))).toMatchObject(state);
    expect(parseMessage('{"type":"reference_get","ts":"now"}')).toMatchObject({ type: "reference_get" });
    expect(parseMessage('{"type":"reference_flag","ts":"now","payload":{"message_id":4}}')).toMatchObject({ type: "reference_flag" });
    expect(parseMessage('{"type":"reference_flag","ts":"now","payload":{"message_id":4,"line":"This misses duplicates"}}')).toMatchObject({ type: "reference_flag" });
    expect(parseMessage('{"type":"reference_flagged","ts":"now","message_id":4,"note_id":9}')).toMatchObject({ type: "reference_flagged" });
    expect(parseMessage(JSON.stringify({ type: "reference_apply", ts: "now", payload: { slug: "top-k-elements-in-list", reply: '{"verdict":"reference is fine"}' } }))).toMatchObject({ type: "reference_apply" });
    expect(parseMessage('{"type":"reference_verdict","ts":"now","slug":"top-k-elements-in-list","accepted":false,"reason":"Invalid verdict"}')).toMatchObject({ type: "reference_verdict" });
    expect(parseMessage('{"type":"reference_verdict","ts":"now","slug":"top-k-elements-in-list","accepted":true,"reason":"reference is fine","graded":[1,2]}')).toMatchObject({ type: "reference_verdict" });
    for (const invalid of [
      { type: "reference_state", ts: "now", count: 1, references: [{ slug: "", title: "x", uncertain: [], prompt: null }] },
      { type: "reference_flag", ts: "now", payload: { message_id: 0 } },
      { type: "reference_flag", ts: "now", payload: { message_id: 4, line: "two\nlines" } },
      { type: "reference_apply", ts: "now", payload: { slug: "x", reply: "" } },
      { type: "reference_verdict", ts: "now", slug: "x", accepted: "yes", reason: "" },
    ]) expect(() => parseMessage(JSON.stringify(invalid))).toThrow();
  });
});


describe("Recaps, Progress and reset messages", () => {
  it("accepts the Recaps tab list newest first with its unread flag", () => {
    const state = {
      type: "recaps_state", ts: "now", unread: true,
      recaps: [
        { session_id: 13, date: "2026-10-19T09:00:00+00:00", status: "ready", bullets: ["Dropped: visited marking."], has_bullets: true, seen: false, message: null },
        { session_id: 12, date: "2026-10-18T09:00:00+00:00", status: "failed", bullets: [], has_bullets: false, seen: true, message: "Session review is missing: offline" },
      ],
    };
    expect(parseMessage(JSON.stringify(state))).toMatchObject(state);
    expect(parseMessage('{"type":"recaps_get","ts":"now"}')).toMatchObject({ type: "recaps_get" });
    for (const invalid of [
      { ...state, recaps: "none" },
      { ...state, unread: "yes" },
      { ...state, recaps: [{ ...state.recaps[0], status: "unknown" }] },
      { ...state, recaps: [{ ...state.recaps[0], bullets: [1] }] },
    ]) expect(() => parseMessage(JSON.stringify(invalid))).toThrow();
    expect(() => parseMessage('{"type":"recaps_get","ts":"now","payload":{}}')).toThrow("Invalid recaps_get payload");
  });
  it("accepts the Progress map and a category detail exactly as the companion sends them", () => {
    const health = { graded: 16, disagreements: 1, disagreement_share: 0.0625, ungraded: 1 };
    const state = {
      type: "progress_state", ts: "now", health,
      header: { on_your_own: 62, coming_back: 40, to_do: 48, strong: 5, rusty: 2, total: 150 },
      categories: [{ name: "Tries", level: "solid", done: 3, total: 3, rusty: false }],
    };
    expect(parseMessage(JSON.stringify(state))).toMatchObject(state);
    const detail = {
      type: "progress_category_state", ts: "now", category: "Tries", level: "solid", done: 3, total: 3, rusty: false,
      next: "To reach strong: a clean solve on a new Medium or Hard problem",
      counts: { not_tried: 0, imported_only: 0 }, health,
      problems: [{
        problem: "implement-trie-prefix-tree", title: "Implement Trie", status: "on_your_own", status_text: "on your own",
        pending: false, pending_reason: null, disagreement: false,
        latest: { review_id: 4, attempt: 9, grade: "good", word: "solid", reason: "solved on your own", at: "now", disagreement: false, failed_grader: null },
        earlier_count: 0, earlier: [],
      }],
    };
    expect(parseMessage(JSON.stringify(detail))).toMatchObject(detail);
    expect(parseMessage('{"type":"progress_get","ts":"now"}')).toMatchObject({ type: "progress_get" });
    expect(parseMessage('{"type":"progress_category","ts":"now","payload":{"category":"Tries"}}')).toMatchObject({ type: "progress_category" });
    for (const invalid of [
      { ...state, header: { ...state.header, total: "150" } },
      { ...state, categories: [{ ...state.categories[0], level: "great" }] },
      { ...state, health: { ...health, disagreement_share: "6%" } },
      { ...detail, problems: [{ ...detail.problems[0], status: "done" }] },
      { ...detail, problems: [{ ...detail.problems[0], latest: { ...detail.problems[0].latest, grade: "solid" } }] },
    ]) expect(() => parseMessage(JSON.stringify(invalid))).toThrow();
    expect(() => parseMessage('{"type":"progress_get","ts":"now","payload":{}}')).toThrow("Invalid progress_get payload");
    expect(() => parseMessage('{"type":"progress_category","ts":"now","payload":{"category":""}}')).toThrow("Invalid progress_category payload");
  });
  it("accepts grade corrections and session exclusions in the shapes the companion validates", () => {
    expect(parseMessage('{"type":"grade_correct","ts":"now","payload":{"review_id":4,"grade":"solid"}}')).toMatchObject({ type: "grade_correct" });
    expect(parseMessage('{"type":"grade_correct","ts":"now","payload":{"problem":"implement-trie-prefix-tree","attempt":9,"grade":"again"}}')).toMatchObject({ type: "grade_correct" });
    expect(parseMessage('{"type":"session_exclude","ts":"now","payload":{"session_id":3}}')).toMatchObject({ type: "session_exclude" });
    for (const invalid of [
      { type: "grade_correct", ts: "now", payload: { review_id: 0, grade: "good" } },
      { type: "grade_correct", ts: "now", payload: { review_id: 4, grade: "great" } },
      { type: "grade_correct", ts: "now", payload: { review_id: 4, problem: "x", attempt: 1, grade: "good" } },
      { type: "grade_correct", ts: "now", payload: { grade: "good" } },
      { type: "session_exclude", ts: "now", payload: { session_id: "3" } },
    ]) expect(() => parseMessage(JSON.stringify(invalid))).toThrow();
  });
  it("accepts the reset request and done message", () => {
    expect(parseMessage('{"type":"reset_progress","ts":"now","payload":{"confirm":"RESET"}}')).toMatchObject({ type: "reset_progress" });
    expect(parseMessage('{"type":"reset_done","ts":"now"}')).toMatchObject({ type: "reset_done" });
    expect(() => parseMessage('{"type":"reset_progress","ts":"now","payload":{"confirm":"reset"}}')).toThrow("Invalid reset_progress payload");
    expect(() => parseMessage('{"type":"reset_done","ts":"now","payload":{}}')).toThrow("Invalid reset_done payload");
  });
  it("accepts an open follow-up question and the skip request, rejecting malformed ones", () => {
    const state = { type: "session_state", ts: "now", state: "active", mode: "watching", paused_reason: null, check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 0, running: true, problem: { slug: "two-sum", title: "Two Sum" }, accepted: true,
      followup: { question_id: 12, question: "What's the time complexity of your approach?", complexity: true } };
    expect(parseMessage(JSON.stringify(state))).toMatchObject(state);
    expect(parseMessage(JSON.stringify({ ...state, followup: null }))).toMatchObject({ followup: null });
    expect(parseMessage(JSON.stringify({ ...state, followup: undefined }))).toMatchObject({ type: "session_state" });
    for (const followup of [
      { question_id: 0, question: "Why?", complexity: false },
      { question_id: 12, question: "", complexity: false },
      { question_id: 12, question: "Why?", complexity: "no" },
      { question_id: 12, question: "Why?" },
      [],
    ]) expect(() => parseMessage(JSON.stringify({ ...state, followup }))).toThrow("Invalid session_state payload");
    expect(parseMessage('{"type":"followup_skip","ts":"now","payload":{"question_id":12}}')).toMatchObject({ type: "followup_skip", payload: { question_id: 12 } });
    for (const payload of [undefined, {}, { question_id: 0 }, { question_id: "12" }]) {
      expect(() => parseMessage(JSON.stringify({ type: "followup_skip", ts: "now", payload }))).toThrow("Invalid followup_skip payload");
    }
  });
  it("keeps linked problem slugs in Tutor replies without requiring them of older messages", () => {
    const done = { type: "tutor_done", ts: "now", turn_id: 1, reply_text: "Try it like Two Sum", gap: "pattern", gap_note: null, help_kind: "analogy", follow_up_question: null, linked: ["two-sum"] };
    expect(parseMessage(JSON.stringify(done))).toMatchObject(done);
    expect(parseMessage(JSON.stringify({ ...done, linked: [] }))).toMatchObject({ linked: [] });
    expect(parseMessage(JSON.stringify({ ...done, linked: undefined }))).toMatchObject({ type: "tutor_done" });
    expect(() => parseMessage(JSON.stringify({ ...done, linked: "two-sum" }))).toThrow("Invalid tutor_done");
    expect(() => parseMessage(JSON.stringify({ ...done, linked: [""] }))).toThrow("Invalid tutor_done");
    const chat = { type: "chat_state", ts: "now", streaming: null, turns: [{ turn_id: 1, kind: "reply", text: "Try a set", gap: "pattern", gap_note: null, help_kind: "analogy", linked: ["two-sum"] }] };
    expect(parseMessage(JSON.stringify(chat))).toMatchObject(chat);
    expect(() => parseMessage(JSON.stringify({ ...chat, turns: [{ ...chat.turns[0], linked: [1] }] }))).toThrow("Invalid chat_state");
  });
});

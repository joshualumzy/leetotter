import { describe, expect, it } from "vitest";
import { applyChat, emptyChat } from "./chat-state";

describe("chat state", () => {
  it("restores a conversation, appends an attempt, replaces a retry, and uses validated done text", () => {
    let state = applyChat(emptyChat(), { type: "chat_state", ts: "now", turns: [
      { turn_id: 1, kind: "press", press_index: 1 },
      { turn_id: 1, kind: "reply", text: "Earlier hint", gap: "weak_intuition", gap_note: null, help_kind: "question" },
    ], streaming: null });
    state = applyChat(state, { type: "tutor_start", ts: "now", turn_id: 2, request: "chat", press_index: null });
    state = applyChat(state, { type: "tutor_chunk", ts: "now", turn_id: 2, attempt: 1, text: "Wrong " });
    state = applyChat(state, { type: "tutor_chunk", ts: "now", turn_id: 2, attempt: 1, text: "path" });
    expect(state.turns.at(-1)?.text).toBe("Wrong path");
    state = applyChat(state, { type: "tutor_chunk", ts: "now", turn_id: 2, attempt: 2, text: "Better " });
    expect(state.turns.at(-1)?.text).toBe("Better ");
    state = applyChat(state, { type: "tutor_done", ts: "now", turn_id: 2, reply_text: "Validated answer", gap: "weak_intuition", gap_note: null, help_kind: "question", follow_up_question: null });
    expect(state.turns.at(-1)).toMatchObject({ kind: "reply", text: "Validated answer", streaming: false });
  });

  it("starts from a missed chunk, withdraws a failed stream, and keeps refusals inline", () => {
    let state = applyChat(emptyChat(), { type: "tutor_chunk", ts: "now", turn_id: 9, attempt: 1, text: "Partial" });
    expect(state.turns).toMatchObject([{ kind: "reply", turn_id: 9, text: "Partial" }]);
    state = applyChat(state, { type: "tutor_error", ts: "now", turn_id: 9, message: "Model failed" });
    expect(state.turns).toMatchObject([{ kind: "failed", turn_id: 9, message: "Model failed" }]);
    state = applyChat(state, { type: "tutor_error", ts: "now", turn_id: null, message: "Wait a moment" });
    expect(state.turns).toHaveLength(1);
    expect(state.notice).toBe("Wait a moment");
  });

  it("withdraws a refused local request and preserves an in-flight stream across a snapshot", () => {
    let state = applyChat(emptyChat(), { type: "local_help" });
    state = applyChat(state, { type: "tutor_error", ts: "now", turn_id: null, message: "Paused" });
    expect(state.turns).toEqual([]);
    state = applyChat(state, { type: "tutor_chunk", ts: "now", turn_id: 3, attempt: 1, text: "Partial " });
    state = applyChat(state, { type: "chat_state", ts: "now", turns: [{ turn_id: 3, kind: "chat", text: "Question" }], streaming: 3 });
    expect(state.turns.at(-1)).toMatchObject({ kind: "reply", text: "Partial " });
  });

  it("shows local sends and reconciles them with server turns", () => {
    let state = applyChat(emptyChat(), { type: "local_chat", text: "Why?" });
    expect(state.turns).toMatchObject([{ kind: "chat", text: "Why?" }]);
    state = applyChat(state, { type: "tutor_start", ts: "now", turn_id: 4, request: "chat", press_index: null });
    expect(state.turns.filter(turn => turn.kind === "chat")).toHaveLength(1);
    expect(state.turns[0].turn_id).toBe(4);
    state = applyChat(state, { type: "local_help" });
    expect(state.turns.at(-1)?.kind).toBe("press");
  });
});

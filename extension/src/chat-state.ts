import type { ChatState, TutorMessage } from "./protocol";

export type ChatTurn = {
  turn_id: number;
  kind: "press" | "chat" | "reply" | "failed";
  text?: string;
  press_index?: number | null;
  message?: string;
  attempt?: number;
  streaming?: boolean;
};
export type Chat = { turns: ChatTurn[]; streaming: number | null; notice: string };
export type ChatEvent = ChatState | TutorMessage | { type: "local_chat"; text: string } | { type: "local_help" };

export function emptyChat(): Chat { return { turns: [], streaming: null, notice: "" }; }

export function applyChat(state: Chat, event: ChatEvent): Chat {
  if (event.type === "chat_state") {
    const inFlight = state.turns.find(turn => turn.kind === "reply" && turn.turn_id === event.streaming && turn.streaming);
    const restored: ChatTurn[] = event.turns.map(turn => ({ ...turn, streaming: turn.kind === "reply" && turn.turn_id === event.streaming }));
    if (inFlight && !restored.some(turn => turn.kind === "reply" && turn.turn_id === inFlight.turn_id)) restored.push({ ...inFlight });
    return {
      turns: restored,
      streaming: event.streaming,
      notice: "",
    };
  }
  if (event.type === "local_chat" || event.type === "local_help") {
    const turn_id = Math.min(0, ...state.turns.map(turn => turn.turn_id)) - 1;
    return { ...state, notice: "", turns: [...state.turns, event.type === "local_chat"
      ? { turn_id, kind: "chat", text: event.text }
      : { turn_id, kind: "press" }] };
  }
  if (event.type === "tutor_error" && event.turn_id === null) return { ...state, turns: state.turns.filter(turn => turn.turn_id > 0), notice: event.message };
  const turns = state.turns.map(turn => ({ ...turn }));
  const turn_id = event.turn_id;
  if (turn_id === null) return state;
  const reply = turns.find(turn => turn.turn_id === turn_id && (turn.kind === "reply" || turn.kind === "failed"));
  if (event.type === "tutor_start") {
    const pending = turns.find(turn => turn.turn_id < 0 && turn.kind === (event.request === "help" ? "press" : "chat"));
    if (pending) { pending.turn_id = turn_id; pending.press_index = event.press_index; }
    else if (!turns.some(turn => turn.turn_id === turn_id && (turn.kind === "press" || turn.kind === "chat"))) {
      turns.push(event.request === "help" ? { turn_id, kind: "press", press_index: event.press_index } : { turn_id, kind: "chat", text: "" });
    }
    return { turns, streaming: turn_id, notice: "" };
  }
  if (event.type === "tutor_chunk") {
    if (reply && reply.kind === "reply") {
      if (event.attempt < (reply.attempt || 0)) return state;
      reply.text = event.attempt > (reply.attempt || 0) ? event.text : (reply.text || "") + event.text;
      reply.attempt = event.attempt;
      reply.streaming = true;
    } else if (!reply) turns.push({ turn_id, kind: "reply", text: event.text, attempt: event.attempt, streaming: true });
    return { turns, streaming: turn_id, notice: "" };
  }
  if (event.type === "tutor_done") {
    if (reply) Object.assign(reply, { kind: "reply", text: event.reply_text, streaming: false });
    else turns.push({ turn_id, kind: "reply", text: event.reply_text, streaming: false });
    return { turns, streaming: state.streaming === turn_id ? null : state.streaming, notice: "" };
  }
  if (reply) Object.assign(reply, { kind: "failed", text: undefined, message: event.message, streaming: false });
  else turns.push({ turn_id, kind: "failed", message: event.message });
  return { turns, streaming: state.streaming === turn_id ? null : state.streaming, notice: "" };
}

export type Difficulty = "Easy" | "Medium" | "Hard";
export type Suggestion = { type: "suggestion"; ts: string; slug: string; title: string; difficulty: Difficulty; cap_minutes: number; steer: string | null; message: string | null; source: "planner" | "fallback" };
export type PlannerOpen = { type: "planner_open"; ts: string; slug: string; title: string };
export type ReferenceItem = { slug: string; title: string; uncertain: string[]; prompt: string | null; reason?: string };
export type ReferenceState = { type: "reference_state"; ts: string; count: number; references: ReferenceItem[] };
export type ReferenceFlagged = { type: "reference_flagged"; ts: string; message_id: number; note_id: number };
export type ReferenceVerdict = { type: "reference_verdict"; ts: string; slug: string; accepted: boolean; reason: string; graded?: number[] };
export type RecapState = { type: "recap_state"; ts?: string; session_id: number; status: "preparing" | "ready" | "failed" | "feedback_failed"; bullets: string[]; message?: string };
export type RecapNoted = { type: "recap_noted"; ts?: string; session_id: number; note_id: number; message: string };

export type Message =
  | { type: "hello" | "ping"; ts: string; session_id?: string }
  | { type: "status"; ts: string; session_id?: string; state: "connected" }
  | { type: "error"; ts: string; session_id?: string; message: string }
  | { type: "get_import_status"; ts: string }
  | { type: "import_status"; ts: string; imported: boolean }
  | { type: "import_history"; ts: string; payload: { urls: string[] } }
  | { type: "import_result"; ts: string; result: { already_imported: boolean; matched: number; unmatched: string[] } }
  | { type: "session_start"; ts: string }
  | { type: "session_pause" | "session_resume" | "session_end" | "session_get_state" | "tab_closed"; ts: string }
  | { type: "session_time_up"; ts: string; payload: { choice: "extend" | "debrief" } }
  | { type: "session_done" | "session_skip"; ts: string }
  | { type: "settings_get"; ts: string }
  | { type: "settings_set"; ts: string; payload: { key: "name" | "theme"; value: string } }
  | { type: "planner_get" | "planner_go" | "planner_swap" | "planner_clear"; ts: string }
  | { type: "planner_chat"; ts: string; payload: { text: string } }
  | { type: "recap_get"; ts: string; payload: { session_id: number } }
  | { type: "recap_seen"; ts: string; payload: { session_id: number } }
  | { type: "recap_feedback"; ts: string; payload: { session_id: number; text: string } }
  | { type: "reference_get"; ts: string }
  | { type: "reference_flag"; ts: string; payload: { message_id: number; line?: string } }
  | { type: "reference_apply"; ts: string; payload: { slug: string; reply: string } }
  | SettingsState
  | SessionState
  | TutorMessage
  | ChatState
  | Suggestion
  | PlannerOpen
  | RecapState
  | RecapNoted
  | ReferenceState
  | ReferenceFlagged
  | ReferenceVerdict
  | { type: "help_request" | "chat_get"; ts: string }
  | { type: "chat_message"; ts: string; payload: { text: string } }
  | { type: ReaderMessageType; ts: string; session_id?: string; payload: Record<string, unknown> };

export type SessionState = { type: "session_state"; ts: string; state: "idle" | "active" | "paused" | "ended"; paused_reason: "button" | "focus" | "check" | "disconnect" | "closed" | "restart" | null; check_message: string | null; cap_minutes: number | null; cap_seconds_left: number | null; time_up: { can_extend: boolean } | null; active_seconds: number; running: boolean; problem: { slug: string; title: string } | null; accepted: boolean; mode: "idle" | "watching" | "paused" | "debrief"; reference_status?: "checked" | "resolved_by_you" | "not_checked_yet"; reference_message?: string };
export type SettingsState = { type: "settings_state"; ts: string; name: string | null; theme: "match" | "light" | "dark" };
export type TutorMessage =
  | { type: "tutor_start"; ts: string; turn_id: number; request: "help" | "chat"; press_index: number | null }
  | { type: "tutor_chunk"; ts: string; turn_id: number; attempt: number; text: string }
  | { type: "tutor_done"; ts: string; turn_id: number; reply_text: string; gap: string; gap_note: string | null; help_kind: string; follow_up_question: string | null }
  | { type: "tutor_error"; ts: string; turn_id: number | null; message: string };
export type StoredTurn =
  | { turn_id: number; kind: "press"; press_index: number }
  | { turn_id: number; kind: "chat"; text: string }
  | { turn_id: number; kind: "reply"; text: string; gap: string; gap_note: string | null; help_kind: string }
  | { turn_id: number; kind: "failed"; reason: string; message: string };
export type ChatState = { type: "chat_state"; ts: string; turns: StoredTurn[]; streaming: number | null };
export type ReaderMessageType = "problem_opened" | "problem_statement" | "code_changed" | "code_snapshot" | "run_result" | "submit_result" | "focus_changed" | "reader_check";
export const MISSING_READER_MESSAGE = "Leetotter can't see this NeetCode page. Reload the tab.";
export const readerTypes = new Set<ReaderMessageType>(["problem_opened", "problem_statement", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"]);

const types = new Set<Message["type"]>(["hello", "ping", "status", "error", "get_import_status", "import_status", "import_history", "import_result", "session_start", "session_pause", "session_resume", "session_end", "tab_closed", "session_time_up", "session_done", "session_skip", "session_get_state", "session_state", "settings_get", "settings_set", "settings_state", "help_request", "chat_message", "chat_get", "tutor_start", "tutor_chunk", "tutor_done", "tutor_error", "chat_state", "suggestion", "planner_get", "planner_go", "planner_swap", "planner_clear", "planner_chat", "planner_open", "recap_get", "recap_state", "recap_feedback", "recap_noted", "recap_seen", "reference_get", "reference_state", "reference_flag", "reference_flagged", "reference_apply", "reference_verdict", ...readerTypes]);

export function validateImportUrls(value: unknown): string[] {
  if (!Array.isArray(value) || !value.every(url => typeof url === "string" && /^https:\/\/(?:www\.)?leetcode\.com\/problems\/[^/?#]+\/?(?:[?#].*)?$/.test(url))) {
    throw new Error("Invalid import_history URLs: expected LeetCode problem URLs");
  }
  return value;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid reader payload");
  return value as Record<string, unknown>;
}
function nonempty(value: unknown): boolean { return typeof value === "string" && value.length > 0; }
function positiveInteger(value: unknown): boolean { return Number.isInteger(value) && (value as number) > 0; }
function oneLine(value: unknown): boolean { return typeof value === "string" && !value.includes("\n") && value.length <= 500; }
function failure(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  const item = record(value);
  return [item.input, item.expected, item.actual].every(v => typeof v === "string");
}
function result(value: unknown): boolean {
  const item = record(value);
  return typeof item.passed === "boolean" && (item.error_type === null || nonempty(item.error_type)) && typeof item.error_message === "string" && typeof item.runtime === "string" && failure(item.failing_case);
}
export function validateReaderPayload(type: ReaderMessageType, value: unknown): Record<string, unknown> {
  const p = record(value);
  let valid = false;
  if (type === "problem_opened") valid = p.site === "neetcode" && nonempty(p.slug) && nonempty(p.title);
  if (type === "problem_statement") valid = nonempty(p.slug) && nonempty(p.statement) && (p.statement as string).length <= 20_000 && (p.truncated === undefined || typeof p.truncated === "boolean");
  if (type === "code_snapshot") valid = typeof p.code === "string";
  if (type === "code_changed") {
    const diff = record(p.diff);
    valid = Number.isInteger(diff.start_line) && (diff.start_line as number) > 0 && Array.isArray(diff.removed) && diff.removed.every(v => typeof v === "string") && Array.isArray(diff.added) && diff.added.every(v => typeof v === "string");
  }
  if (type === "run_result") valid = nonempty(p.slug) && Array.isArray(p.cases) && p.cases.length > 0 && p.cases.every(result);
  if (type === "submit_result") valid = nonempty(p.slug) && typeof p.accepted === "boolean" && (p.error_type === null || nonempty(p.error_type)) && typeof p.error_message === "string" && typeof p.runtime === "string" && typeof p.memory === "number" && Number.isInteger(p.passed) && Number.isInteger(p.total) && failure(p.failing_case);
  if (type === "focus_changed") valid = typeof p.tab_active === "boolean" && typeof p.window_focused === "boolean";
  if (type === "reader_check") valid = typeof p.ok === "boolean" && (p.message === undefined || typeof p.message === "string");
  if (!valid) throw new Error(`Invalid ${type} payload`);
  return p;
}

function validateSuggestion(message: Record<string, unknown>): void {
  if (!nonempty(message.slug) || !nonempty(message.title) ||
    !["Easy", "Medium", "Hard"].includes(String(message.difficulty)) ||
    !positiveInteger(message.cap_minutes) ||
    !(message.steer === null || typeof message.steer === "string") ||
    !(message.message === null || typeof message.message === "string") ||
    !["planner", "fallback"].includes(String(message.source))) throw new Error("Invalid suggestion payload");
}

function validateReferenceState(message: Record<string, unknown>): void {
  if (!Number.isInteger(message.count) || (message.count as number) < 0 || !Array.isArray(message.references) || message.count !== message.references.length ||
    !message.references.every(value => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return false;
      const item = value as Record<string, unknown>;
      return nonempty(item.slug) && nonempty(item.title) && Array.isArray(item.uncertain) && item.uncertain.every(text => typeof text === "string") &&
        (item.prompt === null || typeof item.prompt === "string") && (item.reason === undefined || typeof item.reason === "string");
    })) throw new Error("Invalid reference_state payload");
}

export function parseMessage(raw: string): Message {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error("Invalid JSON message"); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Message must be an object");
  const message = value as Record<string, unknown>;
  if (!types.has(message.type as Message["type"])) throw new Error(`Unknown message type: ${String(message.type)}`);
  // The companion's recap messages are the one wire exception: they carry no ts.
  const tsOptional = message.type === "recap_state" || message.type === "recap_noted";
  if ((!("ts" in message) && !tsOptional) || ("ts" in message && (typeof message.ts !== "string" || !message.ts))) throw new Error("Message requires ts");
  const numericSession = message.type === "recap_state" || message.type === "recap_noted";
  if ("session_id" in message && !numericSession && (typeof message.session_id !== "string" || !message.session_id)) throw new Error("Invalid session_id");
  if (message.type === "status" && message.state !== "connected") throw new Error("Invalid status state");
  if (message.type === "error" && (typeof message.message !== "string" || !message.message)) throw new Error("Error requires message");
  if (["help_request", "chat_get", "planner_get", "planner_go", "planner_swap", "planner_clear", "session_done", "session_skip", "reference_get"].includes(String(message.type)) && "payload" in message) throw new Error(`Invalid ${message.type} payload`);
  for (const type of ["chat_message", "planner_chat"]) {
    if (message.type !== type) continue;
    const payload = message.payload as Record<string, unknown> | undefined;
    if (!payload || typeof payload.text !== "string" || !payload.text.trim() || [...payload.text.trim()].length > 4000) throw new Error(`Invalid ${type} payload`);
  }
  if (["tutor_start", "tutor_chunk", "tutor_done", "tutor_error"].includes(String(message.type))) {
    if (!(Number.isInteger(message.turn_id) && (message.turn_id as number) > 0) && !(message.type === "tutor_error" && message.turn_id === null)) throw new Error("Invalid Tutor turn id");
    if (message.type === "tutor_start" && (!["help", "chat"].includes(String(message.request)) || !(message.press_index === null || positiveInteger(message.press_index)) || (message.request === "help" && message.press_index === null) || (message.request === "chat" && message.press_index !== null))) throw new Error("Invalid tutor_start");
    if (message.type === "tutor_chunk" && (!Number.isInteger(message.attempt) || (message.attempt as number) < 0 || typeof message.text !== "string")) throw new Error("Invalid tutor_chunk");
    if (message.type === "tutor_done" && (!nonempty(message.reply_text) || !nonempty(message.gap) || !(message.gap_note === null || typeof message.gap_note === "string") || !nonempty(message.help_kind) || !(message.follow_up_question === null || typeof message.follow_up_question === "string"))) throw new Error("Invalid tutor_done");
    if (message.type === "tutor_error" && (typeof message.message !== "string" || !message.message)) throw new Error("Invalid tutor_error");
  }
  if (message.type === "chat_state") {
    if (!Array.isArray(message.turns) || !(message.streaming === null || positiveInteger(message.streaming)) || !message.turns.every(value => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return false;
      const turn = value as Record<string, unknown>;
      if (!Number.isInteger(turn.turn_id) || (turn.turn_id as number) < 1) return false;
      if (turn.kind === "press") return positiveInteger(turn.press_index);
      if (turn.kind === "chat") return typeof turn.text === "string";
      if (turn.kind === "reply") return [turn.text, turn.gap, turn.help_kind].every(item => typeof item === "string") && (turn.gap_note === null || typeof turn.gap_note === "string");
      if (turn.kind === "failed") return typeof turn.reason === "string" && typeof turn.message === "string";
      return false;
    })) throw new Error("Invalid chat_state");
  }
  if (message.type === "import_history") validateImportUrls((message.payload as Record<string, unknown> | undefined)?.urls);
  if (message.type === "import_status" && typeof message.imported !== "boolean") throw new Error("Invalid import_status payload");
  if (message.type === "import_result") {
    const result = message.result as Record<string, unknown> | undefined;
    if (!result || typeof result.already_imported !== "boolean" || !Number.isInteger(result.matched) || !Array.isArray(result.unmatched) || !result.unmatched.every(url => typeof url === "string")) throw new Error("Invalid import_result payload");
  }
  if (message.type === "session_start" && "payload" in message) {
    const payload = message.payload;
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).length > 0) throw new Error("Invalid session_start payload");
  }
  if (message.type === "session_time_up" && !["extend", "debrief"].includes(String((message.payload as Record<string, unknown> | undefined)?.choice))) throw new Error("Invalid session_time_up payload");
  if (message.type === "settings_set") {
    const payload = message.payload as Record<string, unknown> | undefined;
    if (!payload || !["name", "theme"].includes(String(payload.key)) || typeof payload.value !== "string" ||
      (payload.key === "name" && payload.value.trim().length > 40) ||
      (payload.key === "theme" && !["match", "light", "dark"].includes(payload.value))) throw new Error("Invalid settings_set payload");
  }
  if (message.type === "settings_state" &&
      (!(message.name === null || (typeof message.name === "string" && message.name.length <= 40)) ||
       !["match", "light", "dark"].includes(String(message.theme)))) throw new Error("Invalid settings_state payload");
  if (message.type === "session_state") {
    if (!["idle", "active", "paused", "ended"].includes(String(message.state)) || !["idle", "watching", "paused", "debrief"].includes(String(message.mode)) ||
      !Number.isInteger(message.active_seconds) || typeof message.running !== "boolean" || typeof message.accepted !== "boolean" ||
      !(message.paused_reason === null || ["button", "focus", "check", "disconnect", "closed", "restart"].includes(String(message.paused_reason))) ||
      !(message.check_message === null || typeof message.check_message === "string") ||
      !(message.cap_minutes === null || positiveInteger(message.cap_minutes)) ||
      !(message.cap_seconds_left === null || (Number.isInteger(message.cap_seconds_left) && (message.cap_seconds_left as number) >= 0)) ||
      !(message.time_up === null || (typeof message.time_up === "object" && !Array.isArray(message.time_up) && typeof (message.time_up as Record<string, unknown>).can_extend === "boolean")) ||
      !(message.reference_status === undefined || ["checked", "resolved_by_you", "not_checked_yet"].includes(String(message.reference_status))) ||
      !(message.reference_message === undefined || typeof message.reference_message === "string") ||
      !(message.problem === null || (typeof message.problem === "object" && !Array.isArray(message.problem) && typeof (message.problem as Record<string, unknown>).slug === "string" && typeof (message.problem as Record<string, unknown>).title === "string"))) throw new Error("Invalid session_state payload");
  }
  if (message.type === "suggestion") validateSuggestion(message);
  if (message.type === "planner_open" && (!nonempty(message.slug) || !nonempty(message.title))) throw new Error("Invalid planner_open payload");
  if (message.type === "recap_get" || message.type === "recap_feedback" || message.type === "recap_seen") {
    const payload = message.payload as Record<string, unknown> | undefined;
    if (!payload || !positiveInteger(payload.session_id)) throw new Error(`Invalid ${message.type} payload`);
    if (message.type === "recap_feedback" && (typeof payload.text !== "string" || !payload.text.trim())) throw new Error("Invalid recap_feedback text");
  }
  if (message.type === "recap_state") {
    if (!positiveInteger(message.session_id) || !["preparing", "ready", "failed", "feedback_failed"].includes(String(message.status)) ||
      !Array.isArray(message.bullets) || !message.bullets.every(bullet => typeof bullet === "string") ||
      !(message.message === undefined || typeof message.message === "string")) throw new Error("Invalid recap_state payload");
  }
  if (message.type === "recap_noted" && (!positiveInteger(message.session_id) || !positiveInteger(message.note_id) || !nonempty(message.message))) throw new Error("Invalid recap_noted payload");
  if (message.type === "reference_state") validateReferenceState(message);
  if (message.type === "reference_flag") {
    const payload = message.payload as Record<string, unknown> | undefined;
    if (!payload || !positiveInteger(payload.message_id) || !(payload.line === undefined || oneLine(payload.line))) throw new Error("Invalid reference_flag payload");
  }
  if (message.type === "reference_flagged" && (!positiveInteger(message.message_id) || !positiveInteger(message.note_id))) throw new Error("Invalid reference_flagged payload");
  if (message.type === "reference_apply") {
    const payload = message.payload as Record<string, unknown> | undefined;
    if (!payload || !nonempty(payload.slug) || !nonempty(payload.reply)) throw new Error("Invalid reference_apply payload");
  }
  if (message.type === "reference_verdict" && (!nonempty(message.slug) || typeof message.accepted !== "boolean" || typeof message.reason !== "string" ||
    !(message.graded === undefined || (Array.isArray(message.graded) && message.graded.every(positiveInteger))))) throw new Error("Invalid reference_verdict payload");
  if (readerTypes.has(message.type as ReaderMessageType)) validateReaderPayload(message.type as ReaderMessageType, message.payload);
  return message as Message;
}

export function makeMessage(type: "hello" | "ping"): Message {
  return { type, ts: new Date().toISOString() };
}

export type Message =
  | { type: "hello" | "ping"; ts: string; session_id?: string }
  | { type: "status"; ts: string; session_id?: string; state: "connected" }
  | { type: "error"; ts: string; session_id?: string; message: string }
  | { type: "get_import_status"; ts: string }
  | { type: "import_status"; ts: string; imported: boolean }
  | { type: "import_history"; ts: string; payload: { urls: string[] } }
  | { type: "import_result"; ts: string; result: { already_imported: boolean; matched: number; unmatched: string[] } }
  | { type: "session_start"; ts: string; payload: { budget_minutes: number } }
  | { type: "session_pause" | "session_resume" | "session_end" | "session_get_state"; ts: string }
  | { type: "session_overrun"; ts: string; payload: { choice: "finish" | "stop" } }
  | SessionState
  | { type: ReaderMessageType; ts: string; session_id?: string; payload: Record<string, unknown> };

export type SessionState = { type: "session_state"; ts: string; state: "idle" | "active" | "paused" | "ended"; paused_reason: "button" | "focus" | "check" | "disconnect" | null; check_message: string | null; budget_minutes: number | null; active_seconds: number; running: boolean; overrun_prompt: boolean; problem: { slug: string; title: string } | null; accepted: boolean; mode: "idle" | "watching" | "paused" };
export type ReaderMessageType = "problem_opened" | "code_changed" | "code_snapshot" | "run_result" | "submit_result" | "focus_changed" | "reader_check";
export const readerTypes = new Set<ReaderMessageType>(["problem_opened", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"]);

const types = new Set<Message["type"]>(["hello", "ping", "status", "error", "get_import_status", "import_status", "import_history", "import_result", "session_start", "session_pause", "session_resume", "session_end", "session_overrun", "session_get_state", "session_state", ...readerTypes]);

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

export function parseMessage(raw: string): Message {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error("Invalid JSON message"); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Message must be an object");
  const message = value as Record<string, unknown>;
  if (!types.has(message.type as Message["type"])) throw new Error(`Unknown message type: ${String(message.type)}`);
  if (typeof message.ts !== "string" || !message.ts) throw new Error("Message requires ts");
  if ("session_id" in message && (typeof message.session_id !== "string" || !message.session_id)) throw new Error("Invalid session_id");
  if (message.type === "status" && message.state !== "connected") throw new Error("Invalid status state");
  if (message.type === "error" && (typeof message.message !== "string" || !message.message)) throw new Error("Error requires message");
  if (message.type === "import_history") validateImportUrls((message.payload as Record<string, unknown> | undefined)?.urls);
  if (message.type === "import_status" && typeof message.imported !== "boolean") throw new Error("Invalid import_status payload");
  if (message.type === "import_result") {
    const result = message.result as Record<string, unknown> | undefined;
    if (!result || typeof result.already_imported !== "boolean" || !Number.isInteger(result.matched) || !Array.isArray(result.unmatched) || !result.unmatched.every(url => typeof url === "string")) throw new Error("Invalid import_result payload");
  }
  if (message.type === "session_start" && (!Number.isInteger((message.payload as Record<string, unknown> | undefined)?.budget_minutes) || ((message.payload as Record<string, unknown>).budget_minutes as number) <= 0)) throw new Error("Invalid session_start payload");
  if (message.type === "session_overrun" && !["finish", "stop"].includes(String((message.payload as Record<string, unknown> | undefined)?.choice))) throw new Error("Invalid session_overrun payload");
  if (message.type === "session_state") {
    if (!["idle", "active", "paused", "ended"].includes(String(message.state)) || !["idle", "watching", "paused"].includes(String(message.mode)) ||
      !Number.isInteger(message.active_seconds) || typeof message.running !== "boolean" || typeof message.overrun_prompt !== "boolean" || typeof message.accepted !== "boolean" ||
      !(message.paused_reason === null || ["button", "focus", "check", "disconnect"].includes(String(message.paused_reason))) ||
      !(message.check_message === null || typeof message.check_message === "string") ||
      !(message.budget_minutes === null || Number.isInteger(message.budget_minutes)) ||
      !(message.problem === null || (typeof message.problem === "object" && !Array.isArray(message.problem) && typeof (message.problem as Record<string, unknown>).slug === "string" && typeof (message.problem as Record<string, unknown>).title === "string"))) throw new Error("Invalid session_state payload");
  }
  if (readerTypes.has(message.type as ReaderMessageType)) validateReaderPayload(message.type as ReaderMessageType, message.payload);
  return message as Message;
}

export function makeMessage(type: "hello" | "ping"): Message {
  return { type, ts: new Date().toISOString() };
}

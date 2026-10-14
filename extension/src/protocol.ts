export type Message =
  | { type: "hello" | "ping"; ts: string; session_id?: string }
  | { type: "status"; ts: string; session_id?: string; state: "connected" }
  | { type: "error"; ts: string; session_id?: string; message: string }
  | { type: ReaderMessageType; ts: string; session_id?: string; payload: Record<string, unknown> };

export type ReaderMessageType = "problem_opened" | "code_changed" | "code_snapshot" | "run_result" | "submit_result" | "focus_changed";
export const readerTypes = new Set<ReaderMessageType>(["problem_opened", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed"]);

const types = new Set<Message["type"]>(["hello", "ping", "status", "error", ...readerTypes]);

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
  if (readerTypes.has(message.type as ReaderMessageType)) validateReaderPayload(message.type as ReaderMessageType, message.payload);
  return message as Message;
}

export function makeMessage(type: "hello" | "ping"): Message {
  return { type, ts: new Date().toISOString() };
}

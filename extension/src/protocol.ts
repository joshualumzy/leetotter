export type Message =
  | { type: "hello" | "ping"; ts: string; session_id?: string }
  | { type: "status"; ts: string; session_id?: string; state: "connected" }
  | { type: "error"; ts: string; session_id?: string; message: string };

const types = new Set<Message["type"]>(["hello", "ping", "status", "error"]);

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
  return message as Message;
}

export function makeMessage(type: "hello" | "ping"): Message {
  return { type, ts: new Date().toISOString() };
}

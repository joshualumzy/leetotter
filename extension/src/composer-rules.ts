export const CHAT_LIMIT = 4000;

export function composerAvailability(state: "idle" | "active" | "paused" | "ended" | undefined, problemOpen: boolean, streaming: boolean): { enabled: boolean; reason: string } {
  if (streaming) return { enabled: false, reason: "The Tutor is still answering…" };
  if (state === "paused") return { enabled: false, reason: "The session is paused. Resume to ask the Tutor." };
  if (state !== "active") return { enabled: false, reason: "Start a session to ask the Tutor." };
  if (!problemOpen) return { enabled: false, reason: "Open a problem to ask the Tutor." };
  return { enabled: true, reason: "" };
}

export function messageToSend(value: string): { text: string | null; error: string } {
  const text = value.trim();
  if (!text) return { text: null, error: "" };
  if ([...text].length > CHAT_LIMIT) return { text: null, error: `Messages must be ${CHAT_LIMIT} characters or fewer.` };
  return { text, error: "" };
}

export function enterAction(key: string, shift: boolean): "send" | "newline" | "ignore" {
  return key !== "Enter" ? "ignore" : shift ? "newline" : "send";
}

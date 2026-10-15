export const CHAT_LIMIT = 4000;

export function composerAvailability(state: "idle" | "active" | "paused" | "ended" | undefined, problemOpen: boolean, streaming: boolean): { enabled: boolean; typing: boolean; reason: string } {
  // While the Tutor answers you can already type the next message (the box keeps its focus); only sending waits.
  if (streaming) return { enabled: false, typing: true, reason: "The Tutor is still answering…" };
  if (state === "paused") return { enabled: false, typing: false, reason: "The session is paused. Resume to ask the Tutor." };
  if (state !== "active") return { enabled: false, typing: false, reason: "Start a session to ask the Tutor." };
  if (!problemOpen) return { enabled: false, typing: false, reason: "Open a problem to ask the Tutor." };
  return { enabled: true, typing: true, reason: "" };
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

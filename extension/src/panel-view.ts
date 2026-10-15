import type { ConnectionState } from "./connection";
import type { SessionState, SettingsState } from "./protocol";

export type Control = "pause" | "resume" | "end";
export type Body = "offline" | "first_run" | "start" | "active" | "paused" | "overrun" | "ended";
export type RemarkPool = "offline" | "start" | "active" | "accepted" |
  "pause_button" | "pause_focus" | "pause_check" | "pause_disconnect" | "overrun" | "ended";
export type PanelView = {
  title: string;
  statusTone: "live" | "good" | "paused" | "error" | "idle";
  controls: Control[];
  body: Body;
  remarkPool: RemarkPool;
  alerts: string[];
  pauseTitle?: string;
  pauseText?: string;
};

export function panelView(input: { transport: ConnectionState; session_state?: SessionState; settings?: SettingsState; readerError: string }): PanelView {
  const { transport, session_state: session, settings, readerError } = input;
  const title = session?.problem?.title || "Leetotter";
  if (transport.kind === "offline") return { title, statusTone: "error", controls: [], body: "offline", remarkPool: "offline", alerts: [] };
  const alerts = [transport.kind === "error" ? transport.message : "", readerError].filter(Boolean);
  const common = { title, alerts };
  if (!session || session.state === "idle") {
    return {
      ...common,
      statusTone: transport.kind === "error" ? "error" : "idle",
      controls: [],
      body: settings?.name == null ? "first_run" : "start",
      remarkPool: "start",
    };
  }
  if (session.state === "ended") return { ...common, statusTone: transport.kind === "error" ? "error" : "idle", controls: [], body: "ended", remarkPool: "ended" };
  if (session.state === "paused") {
    const reason = session.paused_reason || "button";
    const info = {
      button: ["Paused", "The timer's stopped and nothing is watched until you resume."],
      focus: ["Paused while you're away", "The problem tab isn't active. This resumes on its own when you're back on it."],
      check: ["Paused for a reader check", session.check_message || "The page reader's self-check failed. The session resumes when the check passes."],
      disconnect: ["Paused while disconnected", "The connection to the tutor was lost. This resumes when it returns."],
    }[reason];
    return {
      ...common,
      statusTone: transport.kind === "error" ? "error" : "paused",
      controls: reason === "button" ? ["resume", "end"] : ["end"],
      body: "paused",
      remarkPool: `pause_${reason}`,
      pauseTitle: info[0],
      pauseText: info[1],
    };
  }
  return {
    ...common,
    statusTone: transport.kind === "error" ? "error" : session.accepted ? "good" : "live",
    controls: ["pause", "end"],
    body: session.overrun_prompt ? "overrun" : "active",
    remarkPool: session.accepted ? "accepted" : session.overrun_prompt ? "overrun" : "active",
  };
}

function clock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  const h = Math.floor(value / 3600), m = Math.floor(value % 3600 / 60), s = value % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function timerText(activeSeconds: number, budgetMinutes: number): { elapsed: string; budget: string; over: boolean } {
  return { elapsed: clock(activeSeconds), budget: clock(budgetMinutes * 60), over: activeSeconds > budgetMinutes * 60 };
}

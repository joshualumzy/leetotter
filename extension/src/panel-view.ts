import type { ConnectionState } from "./connection";
import type { Difficulty, SessionState, SettingsState } from "./protocol";

export type Control = "pause" | "resume" | "end";
export type Body = "offline" | "first_run" | "start" | "active" | "paused" | "welcome_back" | "another_tab" | "time_up" | "debrief" | "ended";
export type RemarkPool = "offline" | "start" | "active" | "thinking" | "accepted" |
  "pause_button" | "pause_focus" | "pause_check" | "pause_check_page" | "pause_disconnect" | "pause_closed" | "pause_restart" | "time_up" | "debrief" | "ended" | "name_first" | "name_settings";
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

export function panelView(input: { transport: ConnectionState; session_state?: SessionState; settings?: SettingsState; readerError: string; sessionTabId?: number | null; panelTabId?: number | null; boundTabExists?: boolean; panelOnProblemPage?: boolean; problemOpen?: boolean }): PanelView {
  const { transport, session_state: session, settings, readerError, sessionTabId, panelTabId, boundTabExists, panelOnProblemPage, problemOpen = true } = input;
  // An ended session leaves no problem or timer in the header; "Session ended · time" below is its recap.
  const title = session?.state === "ended" || !session?.problem || !problemOpen ? "Leetotter" : session.problem.title;
  if (session && ["active", "paused"].includes(session.state) && sessionTabId != null && panelTabId != null && boundTabExists && sessionTabId !== panelTabId) {
    return { title, statusTone: "paused", controls: [], body: "another_tab", remarkPool: "active", alerts: [] };
  }
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
    const pageCheck = reason === "check" && session.check_message === "Open a problem and I'll pick the timer back up.";
    const resumeFromPageCheck = Boolean(pageCheck && session.problem && panelOnProblemPage === false);
    const noLiveBoundTab = sessionTabId == null || !boundTabExists;
    if (noLiveBoundTab || resumeFromPageCheck) {
      return { ...common, statusTone: "paused", controls: ["end"], body: "welcome_back", remarkPool: resumeFromPageCheck ? "pause_restart" : `pause_${reason}` as RemarkPool };
    }
    const info = {
      button: ["Paused", "The timer's stopped and nothing is watched until you resume."],
      focus: ["Paused while you're away", "The problem tab isn't active. This resumes on its own when you're back on it."],
      check: [pageCheck ? "Paused" : "Paused for a reader check", session.check_message || "The page reader's self-check failed. The session resumes when the check passes."],
      disconnect: ["Paused while disconnected", "The connection to the tutor was lost. This resumes when it returns."],
      closed: ["Paused", "The tab was closed."],
      restart: ["Paused", "The tutor restarted."],
    }[reason];
    return {
      ...common,
      statusTone: transport.kind === "error" ? "error" : "paused",
      controls: reason === "button" ? ["resume", "end"] : ["end"],
      body: "paused",
      remarkPool: pageCheck ? "pause_check_page" : `pause_${reason}`,
      pauseTitle: info[0],
      pauseText: info[1],
    };
  }
  const body: Body = session.mode === "debrief" ? "debrief" : session.time_up !== null ? "time_up" : "active";
  return {
    ...common,
    statusTone: transport.kind === "error" ? "error" : session.accepted ? "good" : "live",
    controls: ["pause", "end"],
    body,
    remarkPool: session.accepted ? "accepted" : body === "time_up" ? "time_up" : body === "debrief" ? "debrief" : "active",
  };
}

export function clock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  const h = Math.floor(value / 3600), m = Math.floor(value % 3600 / 60), s = value % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function timerText(activeSeconds: number): { elapsed: string } {
  return { elapsed: clock(activeSeconds) };
}

export function capLine(difficulty: Difficulty | null, capMinutes: number | null): string {
  return difficulty && capMinutes !== null ? `${difficulty}, ${capMinutes} min cap` : "";
}

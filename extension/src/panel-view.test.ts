import { describe, expect, it } from "vitest";
import { panelView, timerText } from "./panel-view";
import type { SessionState, SettingsState } from "./protocol";

const settings: SettingsState = { type: "settings_state", ts: "now", name: "Ada", theme: "match" };
const base: SessionState = { type: "session_state", ts: "now", state: "idle", mode: "idle", paused_reason: null, check_message: null, budget_minutes: null, active_seconds: 0, running: false, overrun_prompt: false, problem: null, accepted: false };
const view = (session: SessionState = base, readerError = "") => panelView({ transport: { kind: "connected" }, session_state: session, settings, readerError, sessionTabId: 7, panelTabId: 7, boundTabExists: true });

describe("panel state", () => {
  it("shows first run and a start card, then an ordinary start card after a name is set", () => {
    expect(panelView({ transport: { kind: "connected" }, session_state: base, settings: { ...settings, name: null }, readerError: "" })).toMatchObject({ title: "Leetotter", body: "first_run", controls: [], remarkPool: "start" });
    expect(panelView({ transport: { kind: "connected" }, session_state: base, readerError: "" }).body).toBe("first_run");
    expect(view()).toMatchObject({ body: "start", controls: [], remarkPool: "start" });
  });
  it("shows active, time-up, and ended states", () => {
    const active = { ...base, state: "active", mode: "watching", running: true, budget_minutes: 45, problem: { slug: "two-sum", title: "Two Sum" } } as const;
    expect(view(active)).toMatchObject({ title: "Two Sum", statusTone: "live", controls: ["pause", "end"], body: "active", remarkPool: "active" });
    expect(view({ ...active, overrun_prompt: true })).toMatchObject({ body: "overrun", remarkPool: "overrun" });
    expect(view({ ...active, accepted: true })).toMatchObject({ body: "active", statusTone: "good", remarkPool: "accepted" });
    expect(view({ ...active, accepted: true, overrun_prompt: true })).toMatchObject({ body: "overrun", statusTone: "good", remarkPool: "accepted" });
    expect(view({ ...active, state: "ended", mode: "idle", running: false })).toMatchObject({ body: "ended", controls: [], remarkPool: "ended" });
  });
  it.each([
    ["button", ["resume", "end"], "pause_button"],
    ["focus", ["end"], "pause_focus"],
    ["check", ["end"], "pause_check"],
    ["disconnect", ["end"], "pause_disconnect"],
  ] as const)("maps %s pause", (reason, controls, pool) => {
    expect(view({ ...base, state: "paused", mode: "paused", paused_reason: reason, budget_minutes: 45, check_message: "Editor missing" })).toMatchObject({ body: "paused", controls, remarkPool: pool, statusTone: "paused" });
  });
  it("uses the page instruction for a contents-page check and keeps real reader failures", () => {
    const paused = { ...base, state: "paused", mode: "paused", paused_reason: "check", check_message: "Open a problem and I'll pick the timer back up." } as SessionState;
    expect(view(paused)).toMatchObject({ pauseTitle: "Paused", pauseText: paused.check_message, remarkPool: "pause_check_page" });
    expect(view({ ...paused, check_message: "Can't read the editor on this page" })).toMatchObject({ pauseTitle: "Paused for a reader check", pauseText: "Can't read the editor on this page", remarkPool: "pause_check" });
  });
  it("welcomes back closed, restarted, and unbound disconnect pauses", () => {
    for (const reason of ["closed", "restart", "disconnect"] as const) {
      expect(panelView({ transport: { kind: "connected" }, session_state: { ...base, state: "paused", mode: "paused", paused_reason: reason }, settings, readerError: "", boundTabExists: false })).toMatchObject({ body: "welcome_back", controls: ["end"], remarkPool: `pause_${reason}` });
    }
  });
  it("does not claim another tab is running the session while the panel's own tab is unknown", () => {
    const view = panelView({ transport: { kind: "connected" }, session_state: { ...base, state: "active", mode: "watching", running: true, budget_minutes: 25 }, readerError: "", sessionTabId: 7, panelTabId: null, boundTabExists: true });
    expect(view.body).not.toBe("another_tab");
  });
  it("blocks offline and shows transport and reader errors as alerts", () => {
    expect(panelView({ transport: { kind: "offline" }, session_state: base, settings, readerError: "" })).toMatchObject({ body: "offline", statusTone: "error", remarkPool: "offline" });
    expect(panelView({ transport: { kind: "error", message: "Bad reply" }, session_state: base, settings, readerError: "" })).toMatchObject({ statusTone: "error", alerts: ["Bad reply"] });
    expect(view(base, "Editor missing").alerts).toContain("Editor missing");
  });
});

describe("timer", () => {
  it("counts active time, freezes paused time, and marks over budget", () => {
    expect(timerText(1394, 45)).toEqual({ elapsed: "23:14", budget: "45:00", over: false });
    expect(timerText(2701, 45)).toEqual({ elapsed: "45:01", budget: "45:00", over: true });
    expect(timerText(3601, 45)).toEqual({ elapsed: "1:00:01", budget: "45:00", over: true });
  });
});

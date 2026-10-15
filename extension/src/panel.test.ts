import { beforeEach, describe, expect, it, vi } from "vitest";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import type { SessionState } from "./protocol";

let listener: (message: any) => void;
let sendMessage: ReturnType<typeof vi.fn>;
let activeTab: number;
let activeUrl: string;
let activated: (info: { tabId: number; windowId: number }) => void;
let updatedTab: (id: number, change: { url?: string }) => void;
let tabUpdate: ReturnType<typeof vi.fn>;
let windowUpdate: ReturnType<typeof vi.fn>;
const session: SessionState = { type: "session_state", ts: "now", state: "idle", mode: "idle", paused_reason: null, check_message: null, budget_minutes: null, active_seconds: 0, running: false, overrun_prompt: false, problem: null, accepted: false };
const notify = (message: any) => listener(message);
const click = (id: string) => document.getElementById(id)?.click();

beforeEach(async () => {
  vi.resetModules();
  const html = readFileSync(new URL("../panel.html", import.meta.url), "utf8");
  const dom = new JSDOM(html);
  vi.stubGlobal("document", dom.window.document);
  vi.stubGlobal("window", dom.window);
  activeTab = 7;
  activeUrl = "https://neetcode.io/problems/duplicate-integer/";
  tabUpdate = vi.fn(async () => {});
  windowUpdate = vi.fn(async () => {});
  sendMessage = vi.fn(async (message: any) => {
    if (message.kind === "get_import_state") return { imported: false };
    if (message.kind === "get_status") return { kind: "connected" };
    if (message.kind === "get_reader_error") return "";
    if (message.kind === "get_session_state") return session;
    if (message.kind === "get_session_binding") return { tabId: null };
    if (message.kind === "get_settings_state") return { type: "settings_state", ts: "now", name: null, theme: "match" };
    if (message.kind === "import_history") return { pending: true };
    return { sent: true };
  });
  vi.stubGlobal("chrome", { runtime: { onMessage: { addListener: (fn: typeof listener) => { listener = fn; } }, sendMessage },
    tabs: { query: vi.fn(async () => [{ id: activeTab, url: activeUrl }]), get: vi.fn(async (id: number) => ({ id, windowId: 3, url: activeUrl })), update: tabUpdate,
      onActivated: { addListener: (fn: typeof activated) => { activated = fn; } }, onUpdated: { addListener: (fn: typeof updatedTab) => { updatedTab = fn; } } }, windows: { update: windowUpdate } });
  await import("./panel");
  await vi.waitFor(() => expect(document.getElementById("first-run")).toBeTruthy());
});

describe("panel actions", () => {
  it("shows chat, sends Enter, keeps Shift+Enter, and disables input while answering", async () => {
    const active = { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    expect(document.getElementById("help-request")).toBeTruthy();
    const input = document.getElementById("chat-input") as HTMLTextAreaElement;
    input.value = "What next?";
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true, cancelable: true }));
    expect(sendMessage).not.toHaveBeenCalledWith({ kind: "chat_message", text: "What next?" });
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    expect(sendMessage).toHaveBeenCalledWith({ kind: "chat_message", text: "What next?" });
    notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 2, request: "chat", press_index: null } });
    expect((document.getElementById("help-request") as HTMLButtonElement).disabled).toBe(true);
    expect(document.getElementById("composer-reason")?.textContent).toMatch(/still answering/i);
  });
  it("shows snapshot bubbles and a short refusal notice", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", streaming: null, turns: [
      { turn_id: 1, kind: "press", press_index: 1 },
      { turn_id: 1, kind: "reply", text: "Try a set", gap: "pattern_not_recognised", gap_note: null, help_kind: "question" },
    ] } });
    expect(document.querySelector(".chat-press")?.textContent).toBe("You asked for a hint");
    expect(document.querySelector(".chat-reply")?.textContent).toBe("Try a set");
    notify({ kind: "tutor", message: { type: "tutor_error", ts: "now", turn_id: null, message: "Wait a moment" } });
    expect(document.querySelector(".chat-notice")?.textContent).toBe("Wait a moment");
  });
  it("keeps the composer visible with a reason while paused and rejects over-limit dictation", () => {
    const active = { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    const input = document.getElementById("chat-input") as HTMLTextAreaElement;
    input.value = "x".repeat(4001);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    click("chat-send");
    expect(document.getElementById("composer-note")?.textContent).toMatch(/4000/);
    expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "chat_message" }));
    notify({ kind: "session_state", state: { ...active, state: "paused", mode: "paused", running: false, paused_reason: "button" } });
    expect((document.getElementById("help-request") as HTMLButtonElement).disabled).toBe(true);
    expect(document.getElementById("composer-reason")?.textContent).toMatch(/paused/i);
    expect(document.getElementById("chat-input")).toBeTruthy();
  });
  it("paces incoming words and keeps a reply visible when the session pauses", () => {
    vi.useFakeTimers();
    const active = { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 7, request: "help", press_index: 1 } });
    notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 7, attempt: 1, text: "Try a set " } });
    expect(document.querySelector(".chat-reply")?.textContent).toBe("");
    vi.advanceTimersByTime(90);
    expect(document.querySelector(".chat-reply")?.textContent).toBe("Try ");
    notify({ kind: "session_state", state: { ...active, state: "paused", mode: "paused", paused_reason: "button" } });
    expect(document.querySelector(".chat-reply")?.textContent).toBe("Try ");
    notify({ kind: "tutor", message: { type: "tutor_done", ts: "now", turn_id: 7, reply_text: "Try a set", gap: "pattern_not_recognised", gap_note: null, help_kind: "question", follow_up_question: null } });
    vi.advanceTimersByTime(500);
    expect(document.querySelector(".chat-reply")?.textContent).toBe("Try a set");
    vi.useRealTimers();
  });
  it("clears the old problem's chat while requesting the new problem's snapshot", () => {
    const active = { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", turns: [
      { turn_id: 1, kind: "chat", text: "Old question" },
    ], streaming: null } });
    expect(document.querySelector(".chat-you")?.textContent).toBe("Old question");
    sendMessage.mockClear();
    notify({ kind: "session_state", state: { ...active, problem: { slug: "two-sum", title: "Two Sum" } } });
    expect(document.querySelector(".chat-you")).toBeNull();
    expect(sendMessage).toHaveBeenCalledWith({ kind: "chat_get" });
  });
  it("disables Start off a problem and refreshes after navigation", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    activeUrl = "https://neetcode.io/practice/";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(true));
    expect(document.getElementById("session-body")?.textContent).toContain("Open a problem on NeetCode to start.");
    click("budget-30"); click("start-session");
    expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "session_command", command: "start" }));
    activeUrl = "https://neetcode.io/problems/two-sum/";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(false));
    expect(document.getElementById("session-body")?.textContent).not.toContain("Open a problem on NeetCode to start.");
  });
  it("acknowledges a saved first name for four seconds", async () => {
    vi.useFakeTimers();
    const input = document.getElementById("first-name") as HTMLInputElement;
    input.value = "Ada"; click("save-first-name");
    await Promise.resolve();
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    expect(document.getElementById("remark")?.textContent).toMatch(/Ada/);
    expect(document.getElementById("remark")?.textContent).toMatch(/meet|hello|glad/i);
    vi.advanceTimersByTime(4000);
    expect(document.getElementById("remark")?.textContent).not.toMatch(/meet|hello|glad/i);
    vi.useRealTimers();
  });
  it("does not acknowledge a skipped name", () => {
    click("skip-name");
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "", theme: "match" } });
    expect(document.getElementById("remark")?.textContent).not.toMatch(/meet|hello|glad|got it|noted|okay/i);
  });
  it("acknowledges a settings Save after confirmation", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    click("header-otter");
    const input = document.getElementById("settings-name") as HTMLInputElement;
    input.value = "Grace"; input.dispatchEvent(new window.Event("input", { bubbles: true })); click("save-settings-name");
    await Promise.resolve();
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Grace", theme: "match" } });
    expect(document.getElementById("remark")?.textContent).toMatch(/Grace/);
    expect(document.getElementById("remark")?.textContent).toMatch(/got it|noted|okay/i);
  });
  it("shows only a route back to the bound tab and follows it", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    notify({ kind: "session_binding", tabId: 8 });
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45 } });
    await vi.waitFor(() => expect(document.getElementById("go-to-session-tab")).toBeTruthy());
    expect(document.getElementById("session-body")?.textContent).toContain("A session is running in another tab.");
    expect(document.getElementById("session-body")?.querySelectorAll("button")).toHaveLength(1);
    click("go-to-session-tab");
    await vi.waitFor(() => expect(tabUpdate).toHaveBeenCalledWith(8, { active: true }));
    expect(windowUpdate).toHaveBeenCalledWith(3, { focused: true });
    activeTab = 8; activated({ tabId: 8, windowId: 3 });
    await vi.waitFor(() => expect(document.getElementById("pause-session")).toBeTruthy());
  });

  it("offers resume or end and start over after a closed tab, with a stopped timer", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "closed", budget_minutes: 45, active_seconds: 1394, problem: { slug: "daily-temperatures", title: "Daily Temperatures" } } });
    expect(document.getElementById("session-body")?.textContent).toContain("Pick up where you left off?");
    expect(document.getElementById("session-body")?.textContent).toContain("Daily Temperatures, 23 min of 45");
    expect(document.getElementById("timer")?.classList.contains("stopped")).toBe(true);
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7, problem_slug: "daily-temperatures" });
    click("end-and-start");
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "end" }));
    await vi.waitFor(() => expect(document.getElementById("start-session")).toBeTruthy());
    expect(document.getElementById("welcome-resume")).toBeNull();
  });

  it("offers the saved problem from the contents page and requests navigation on Resume", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    activeUrl = "https://neetcode.io/practice/";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(true));
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "check", check_message: "Open a problem and I'll pick the timer back up.", budget_minutes: 45, active_seconds: 1394, problem: { slug: "daily-temperatures", title: "Daily Temperatures" } } });
    expect(document.querySelector(".welcome-back")?.textContent).toContain("Daily Temperatures, 23 min of 45");
    expect(document.getElementById("session-body")?.textContent).not.toContain("Open a problem and I'll pick the timer back up.");
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7, problem_slug: "daily-temperatures" });
    expect(tabUpdate).not.toHaveBeenCalled();
  });

  it("resumes directly when the panel tab is already on the saved problem", () => {
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", budget_minutes: 45, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
  });

  it("recognizes the saved problem URL without a trailing slash", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    activeUrl = "https://neetcode.io/problems/duplicate-integer";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(false));
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
  });

  it("shows a failed saved-problem navigation in the alert row", async () => {
    sendMessage.mockImplementation(async (message: any) => message.kind === "session_command" ? { error: "Could not open the saved problem." } : { sent: true });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", problem: { slug: "two-sum", title: "Two Sum" } } });
    click("welcome-resume");
    await vi.waitFor(() => expect(document.querySelector('[role="alert"]')?.textContent).toBe("Could not open the saved problem."));
    expect(document.getElementById("welcome-resume")).toBeTruthy();
  });

  it("shows time without a problem on a restart pause", () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", budget_minutes: 45, active_seconds: 1394 } });
    expect(document.getElementById("session-body")?.textContent).toContain("23 min of 45");
    expect(document.getElementById("session-body")?.textContent).not.toContain("Daily Temperatures");
  });

  it("labels a quiet End session button and accessible pause/resume icons", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45 } });
    expect(document.getElementById("end-session")?.textContent).toBe("End session");
    expect(document.getElementById("end-session")?.classList.contains("end-session")).toBe(true);
    expect(document.getElementById("pause-session")?.getAttribute("aria-label")).toBe("Pause");
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button", budget_minutes: 45 } });
    expect(document.getElementById("resume-session")?.getAttribute("aria-label")).toBe("Resume");
    notify({ kind: "session_state", state: session });
    expect(document.getElementById("end-session")).toBeNull();
  });
  it("starts, pauses, resumes and ends through session commands", async () => {
    click("skip-name");
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "", theme: "match" } });
    expect((document.getElementById("budget-minutes") as HTMLInputElement).value).toBe("");
    expect(document.querySelectorAll(".budget-chip")).toHaveLength(4);
    expect(document.getElementById("budget-45")?.getAttribute("aria-pressed")).toBe("true");
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", budget_minutes: 45, tabId: 7 });
    click("budget-30");
    expect(document.getElementById("budget-30")?.getAttribute("aria-pressed")).toBe("true");
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", budget_minutes: 30, tabId: 7 });
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 30 } });
    click("pause-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "pause" });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button", budget_minutes: 30 } });
    click("resume-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
    click("end-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "end" });
  });
  it("uses valid custom minutes and clears them when a chip is pressed", () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "", theme: "match" } });
    const custom = document.getElementById("budget-minutes") as HTMLInputElement;
    const start = document.getElementById("start-session") as HTMLButtonElement;
    for (const value of ["", "0", "1.5", "1441"]) {
      custom.value = value;
      custom.dispatchEvent(new window.Event("input", { bubbles: true }));
      expect(start.disabled).toBe(true);
      expect(document.querySelector('.budget-chip[aria-pressed="true"]')).toBeNull();
    }
    custom.value = "1440";
    custom.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(start.disabled).toBe(false);
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", budget_minutes: 1440, tabId: 7 });
    custom.value = "1";
    custom.dispatchEvent(new window.Event("input", { bubbles: true }));
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", budget_minutes: 1, tabId: 7 });
    click("budget-60");
    expect(custom.value).toBe("");
    expect(document.getElementById("budget-60")?.getAttribute("aria-pressed")).toBe("true");
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", budget_minutes: 60, tabId: 7 });
  });
  it("perks before a run outcome and clears the reaction on pause", async () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45 } });
    const normal = document.getElementById("remark")?.textContent;
    notify({ kind: "reader_reaction", reaction: "run_fail" });
    expect(document.getElementById("remark")?.textContent).toMatch(/Oof|not quite|close/i);
    expect(document.querySelector("#header-otter .otter")?.classList.contains("react-perk")).toBe(true);
    await vi.waitFor(() => expect(document.querySelector("#header-otter .otter")?.classList.contains("react-oof")).toBe(true));
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button", budget_minutes: 45 } });
    expect(document.getElementById("remark")?.textContent).not.toBe(normal);
    expect(document.querySelector("#header-otter .otter")?.classList.contains("react-oof")).toBe(false);
  });
  it("asks for the first name and supports Enter, Skip and settings", () => {
    expect(document.getElementById("first-run")?.textContent).toContain("What should I call you?");
    const input = document.getElementById("first-name") as HTMLInputElement;
    input.value = " Ada ";
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(sendMessage).toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "Ada" });
    click("skip-name");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "skip_name" });
    click("header-otter");
    expect(document.getElementById("settings-card")).toBeTruthy();
    click("close-settings");
    expect(document.getElementById("settings-card")).toBeFalsy();
  });
  it("keeps first run open when a blank name is confirmed", () => {
    expect(document.getElementById("start-session")).toBeNull();
    const input = document.getElementById("first-name") as HTMLInputElement;
    input.value = "   ";
    click("save-first-name");
    expect(document.getElementById("name-hint")?.textContent).toBe("Type a name, or press Skip.");
    expect(sendMessage).not.toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "" });
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(sendMessage).not.toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "" });
    expect(document.getElementById("first-run")).toBeTruthy();
    expect(document.getElementById("start-session")).toBeNull();
  });
  it("rejects a blank settings name on Save or Enter and accepts a replacement", () => {
    click("header-otter");
    expect(document.querySelector("#settings-theme option[value=match]")?.textContent).toBe("Match my Mac");
    const input = document.getElementById("settings-name") as HTMLInputElement;
    const save = document.getElementById("save-settings-name") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    input.value = "   ";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(save.disabled).toBe(true);
    expect(sendMessage).not.toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "" });
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(document.getElementById("name-hint")?.textContent).toBe("Type a name to save it.");
    expect(document.getElementById("name-hint")?.hidden).toBe(false);
    expect(sendMessage).not.toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "" });
    input.value = " Ada ";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(save.disabled).toBe(false);
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(sendMessage).toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "Ada" });
  });
  it("keeps settings Save disabled until a saved name changes", () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    click("header-otter");
    const input = document.getElementById("settings-name") as HTMLInputElement;
    const save = document.getElementById("save-settings-name") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    input.value = " Ada ";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(save.disabled).toBe(true);
    input.value = "Grace";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(save.disabled).toBe(false);
    input.value = "Ada";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(save.disabled).toBe(true);
  });
  it("shows an accepted remark and caption while preserving the time-up card", () => {
    const active = { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45, problem: { slug: "two-sum", title: "Two Sum" } } as SessionState;
    notify({ kind: "session_state", state: active });
    expect(document.querySelector(".active-otter figcaption")?.textContent).toMatch(/here to help/i);
    notify({ kind: "session_state", state: { ...active, accepted: true } });
    expect(document.getElementById("status-dot")?.classList.contains("good")).toBe(true);
    expect(document.getElementById("remark")?.textContent).toMatch(/Accepted|Cracked|Shell/);
    expect(document.querySelector(".active-otter figcaption")?.textContent).toContain("Accepted! Keep polishing, or move on.");
    notify({ kind: "session_state", state: { ...active, accepted: true, overrun_prompt: true } });
    expect(document.getElementById("status-dot")?.classList.contains("good")).toBe(true);
    expect(document.getElementById("remark")?.textContent).toMatch(/Accepted|Cracked|Shell/);
    expect(document.querySelector(".card")?.textContent).toContain("Time's up");
    expect(document.querySelector(".active-otter")).toBeNull();
  });
  it("sends both overrun choices and hides auto-resume", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45, overrun_prompt: true } });
    click("overrun-finish"); click("overrun-stop");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "overrun_finish" });
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "overrun_stop" });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "focus", budget_minutes: 45 } });
    expect(document.getElementById("resume-session")).toBeFalsy();
  });
  it("keeps history import and shows errors", async () => {
    click("import-history");
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledWith({ kind: "import_history" }));
    notify({ kind: "import_state", imported: true, result: { already_imported: false, matched: 2, unmatched: ["unknown"] } });
    expect(document.getElementById("import-history")?.hidden).toBe(true);
    expect(document.getElementById("import-result")?.textContent).toContain("2");
    notify({ kind: "reader_error", message: "Editor missing" });
    expect(document.querySelector(".alert")?.textContent).toContain("Editor missing");
  });
  it("has no remote asset URLs", () => {
    const html = readFileSync(new URL("../panel.html", import.meta.url), "utf8");
    const css = readFileSync(new URL("../panel.css", import.meta.url), "utf8");
    expect(html + css).not.toMatch(/https?:\/\//);
  });
});

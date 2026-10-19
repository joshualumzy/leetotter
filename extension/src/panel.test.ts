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
const session: SessionState = { type: "session_state", ts: "now", state: "idle", mode: "idle", paused_reason: null, check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 0, running: false, problem: null, accepted: false };
const notify = (message: any) => listener(message);
const click = (id: string) => document.getElementById(id)?.click();
// A session whose bound tab is the panel's own, still open: the views for a paused session need one.
async function bindPanelTab() {
  notify({ kind: "session_binding", tabId: 7 });
  await new Promise(resolve => setTimeout(resolve, 0));
}

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
    if (message.kind === "get_suggestion" || message.kind === "get_recap_state" || message.kind === "get_reference_state") return undefined;
    if (message.kind === "import_history") return { pending: true };
    return { sent: true };
  });
  vi.stubGlobal("chrome", { runtime: { onMessage: { addListener: (fn: typeof listener) => { listener = fn; } }, sendMessage },
    tabs: { query: vi.fn(async () => [{ id: activeTab, url: activeUrl }]), get: vi.fn(async (id: number) => ({ id, windowId: 3, url: activeUrl })), update: tabUpdate, reload: vi.fn(),
      onActivated: { addListener: (fn: typeof activated) => { activated = fn; } }, onUpdated: { addListener: (fn: typeof updatedTab) => { updatedTab = fn; } } }, windows: { update: windowUpdate } });
  await import("./panel");
  await vi.waitFor(() => expect(document.getElementById("first-run")).toBeTruthy());
});

describe("panel actions", () => {
  it("shows chat, sends Enter, keeps Shift+Enter, and disables help while answering", async () => {
    const active = { ...session, state: "active", mode: "watching", running: true, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
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
  it("shows a waiting indicator from the ask until the first word of the reply", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 5, request: "help", press_index: 1 } });
    expect(document.querySelectorAll(".chat-waiting")).toHaveLength(1);
    notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 5, attempt: 0, text: "Try " } });
    expect(document.querySelector(".chat-waiting")).toBeNull();
  });
  it("scrolls the pause note into view when you were reading the end of the conversation", async () => {
    await bindPanelTab();
    const active = { ...session, state: "active", mode: "watching", running: true, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", streaming: null, turns: [{ turn_id: 1, kind: "press", press_index: 1 }, { turn_id: 1, kind: "reply", text: "Try a set", gap: "pattern_not_recognised", gap_note: null, help_kind: "question" }] } });
    const scroller = document.getElementById("session-body") as HTMLElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, get: () => 900 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, get: () => 400 });
    scroller.scrollTop = 500;
    notify({ kind: "session_state", state: { ...active, state: "paused", mode: "paused", running: false, paused_reason: "focus" } });
    expect(document.body.textContent).toContain("Paused while you're away");
    expect(scroller.scrollTop).toBe(900);
  });
  it("keeps the chat box enabled and focused after a send, and holds a message typed while the Tutor answers", async () => {
    await bindPanelTab();
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    const input = document.getElementById("chat-input") as HTMLTextAreaElement;
    input.focus();
    input.value = "First question";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    click("chat-send");
    notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 3, request: "chat", press_index: null } });
    expect(input.disabled).toBe(false);
    expect(document.activeElement).toBe(input);
    input.value = "Second question";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    sendMessage.mockClear();
    click("chat-send");
    expect(sendMessage).not.toHaveBeenCalled();
    expect(input.value).toBe("Second question");
    expect(document.getElementById("composer-reason")?.textContent).toMatch(/still answering/i);
  });
  it("clears the problem title and the timer from the header once the session has ended", () => {
    const active = { ...session, state: "active", mode: "watching", running: true, active_seconds: 52, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    expect(document.getElementById("problem-title")?.textContent).toContain("Duplicate Integer");
    notify({ kind: "session_state", state: { ...active, state: "ended", mode: "idle", running: false } });
    expect(document.body.textContent).not.toContain("Duplicate Integer");
    expect((document.getElementById("timer") as HTMLElement).hidden).toBe(true);
    expect(document.body.textContent).toContain("Session ended · 0:52");
  });
  it("shows one cap countdown in the header, and no problem means no clock", () => {
    const active = { ...session, state: "active", mode: "watching", running: true, cap_minutes: 20, cap_seconds_left: 1178, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    expect((document.getElementById("timer") as HTMLElement).hidden).toBe(false);
    expect(document.getElementById("clock")?.textContent).toBe("19:38");
    expect(document.getElementById("elapsed")).toBeNull();
    expect(document.getElementById("session-body")?.textContent).not.toContain("min cap ·");
    expect(document.getElementById("remark")?.textContent).not.toMatch(/min left/i);
    notify({ kind: "session_state", state: { ...active, problem: null } });
    expect((document.getElementById("timer") as HTMLElement).hidden).toBe(true);
  });
  it("keeps the header title only while the session's problem is open", async () => {
    const active = { ...session, state: "active", mode: "watching", running: true, cap_minutes: 20, cap_seconds_left: 1178, problem: { slug: "duplicate-integer", title: "Contains Duplicate" } } as SessionState;
    notify({ kind: "session_state", state: active });
    expect(document.getElementById("problem-title")?.textContent).toBe("Contains Duplicate");
    activeUrl = "https://neetcode.io/practice/";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect(document.getElementById("problem-title")?.textContent).toBe("Leetotter"));
    expect(document.getElementById("remark")).toBeTruthy();
  });
  it("offers a Reload tab button when the page reader is missing", async () => {
    await bindPanelTab();
    notify({ kind: "reader_error", message: "Leetotter can't see this NeetCode page. Reload the tab." });
    activeTab = 8; activated({ tabId: 8, windowId: 3 });   // looking at another tab: the session's tab (7) is the one to reload
    await new Promise(resolve => setTimeout(resolve, 0));
    click("reload-tab");
    expect(chrome.tabs.reload).toHaveBeenCalledWith(7);
  });
  it("refuses a second send in the moment before the companion confirms the first, and keeps the draft", async () => {
    await bindPanelTab();
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    const input = document.getElementById("chat-input") as HTMLTextAreaElement;
    input.value = "A";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    click("chat-send");   // sent; no tutor_start yet
    sendMessage.mockClear();
    input.value = "B";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    click("chat-send");
    expect(sendMessage).not.toHaveBeenCalled();
    expect(input.value).toBe("B");
    expect(document.querySelectorAll(".chat-you")).toHaveLength(1);
  });
  it("keeps the waiting indicator while a withdrawn first attempt is retried, and shows the longer-wait line after 10 s", () => {
    vi.useFakeTimers();
    try {
      notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
      notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 6, request: "help", press_index: 1 } });
      expect(document.querySelector(".chat-waiting")).not.toBeNull();
      expect((document.querySelector(".wait-more") as HTMLElement).hidden).toBe(true);
      vi.advanceTimersByTime(10_500);
      expect((document.querySelector(".wait-more") as HTMLElement).hidden).toBe(false);
      notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 6, attempt: 0, text: "Wrong " } });
      vi.advanceTimersByTime(500);
      notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 6, attempt: 1, text: "" } });
      vi.advanceTimersByTime(500);
      expect(document.querySelector(".chat-reply")?.textContent || "").not.toContain("Wrong");
      expect(document.querySelector(".chat-waiting")).not.toBeNull();
    } finally { vi.useRealTimers(); }
  });
  it("keeps a single composer, and the same text box, across the redraws the timer causes", () => {
    const active = { ...session, state: "active", mode: "watching", running: true, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    const input = document.getElementById("chat-input") as HTMLTextAreaElement;
    input.value = "half-typed";
    for (let seconds = 1; seconds <= 5; seconds++) notify({ kind: "session_state", state: { ...active, active_seconds: seconds } });
    expect(document.querySelectorAll("#help-request")).toHaveLength(1);
    expect(document.querySelectorAll("#chat-input")).toHaveLength(1);
    expect(document.getElementById("chat-input")).toBe(input);
    expect(document.querySelectorAll("#composer-reason, #composer-note")).toHaveLength(2);
  });
  it("shows snapshot bubbles and a short refusal notice", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", streaming: null, turns: [
      { turn_id: 1, kind: "press", press_index: 1 },
      { turn_id: 1, kind: "reply", text: "Try a set", gap: "pattern_not_recognised", gap_note: null, help_kind: "question" },
    ] } });
    expect(document.querySelector(".chat-press")?.textContent).toBe("You asked for a hint");
    expect(document.querySelector(".chat-reply")?.textContent).toContain("Try a set");
    notify({ kind: "tutor", message: { type: "tutor_error", ts: "now", turn_id: null, message: "Wait a moment" } });
    expect(document.querySelector(".chat-notice")?.textContent).toBe("Wait a moment");
  });
  it("keeps the composer visible with a reason while paused and rejects over-limit dictation", async () => {
    await bindPanelTab();
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
  it("paces incoming words and keeps a reply visible when the session pauses", async () => {
    await bindPanelTab();
    vi.useFakeTimers();
    const active = { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    notify({ kind: "session_state", state: active });
    notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 7, request: "help", press_index: 1 } });
    notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 7, attempt: 1, text: "Try a set " } });
    expect(document.querySelector(".chat-reply")?.textContent).not.toContain("Try");
    vi.advanceTimersByTime(90);
    expect(document.querySelector(".chat-reply")?.textContent).toContain("Try ");
    notify({ kind: "session_state", state: { ...active, state: "paused", mode: "paused", paused_reason: "button" } });
    expect(document.querySelector(".chat-reply")?.textContent).toContain("Try ");
    notify({ kind: "tutor", message: { type: "tutor_done", ts: "now", turn_id: 7, reply_text: "Try a set", gap: "pattern_not_recognised", gap_note: null, help_kind: "question", follow_up_question: null } });
    vi.advanceTimersByTime(500);
    expect(document.querySelector(".chat-reply")?.textContent).toContain("Try a set");
    vi.useRealTimers();
  });
  describe("formatted replies", () => {
    const reply = "Use a `dict` here:\n\n```python\nclass TrieNode:\n    def __init__(self):\n        self.children = {}\n```\n\n- then walk it";
    const active = { ...session, state: "active", mode: "watching", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } as SessionState;
    const streamReply = (text: string) => {
      notify({ kind: "session_state", state: active });
      notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 7, request: "help", press_index: 1 } });
      for (let at = 0; at < text.length; at += 7) notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 7, attempt: 1, text: text.slice(at, at + 7) } });
      notify({ kind: "tutor", message: { type: "tutor_done", ts: "now", turn_id: 7, reply_text: text, gap: "none", gap_note: null, help_kind: "none", follow_up_question: null } });
    };
    const replyHtml = (node: Element) => {
      const clone = node.cloneNode(true) as HTMLElement;
      clone.querySelector(".chat-flag")?.remove();
      return clone.innerHTML;
    };
    const restored = () => {
      notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", streaming: null, turns: [{ turn_id: 1, kind: "reply", text: reply, gap: "none", gap_note: null, help_kind: "none" }] } });
      return replyHtml(document.querySelector(".chat-reply")!);
    };
    it("shows inline code, a code block with its indentation, and a list for a reloaded reply", () => {
      notify({ kind: "session_state", state: active });
      restored();
      const bubble = document.querySelector(".chat-reply")!;
      expect(bubble.querySelector("p code")?.textContent).toBe("dict");
      expect(bubble.querySelector("pre code")?.textContent).toBe("class TrieNode:\n    def __init__(self):\n        self.children = {}");
      expect(bubble.querySelector("li")?.textContent).toBe("then walk it");
      expect(bubble.textContent).not.toContain("`");
      expect(bubble.querySelector(".stream-word")).toBeNull();
    });
    it("fades words in while streaming and ends identical to a reloaded reply, with no fade left", () => {
      vi.useFakeTimers();
      streamReply(reply);
      vi.advanceTimersByTime(400);
      expect(document.querySelector(".chat-reply .stream-word")).toBeTruthy();
      expect((document.querySelector(".chat-reply .stream-word") as HTMLElement).style.animationDelay).toMatch(/^-?\d+ms$/);
      vi.advanceTimersByTime(10000);
      const streamed = replyHtml(document.querySelector(".chat-reply")!);
      expect(streamed).not.toContain("stream-word");
      expect(streamed).toBe(restored());
      vi.useRealTimers();
    });
    it("shows the open code block while the fence is still unclosed", () => {
      vi.useFakeTimers();
      notify({ kind: "session_state", state: active });
      notify({ kind: "tutor", message: { type: "tutor_start", ts: "now", turn_id: 7, request: "help", press_index: 1 } });
      notify({ kind: "tutor", message: { type: "tutor_chunk", ts: "now", turn_id: 7, attempt: 1, text: "Try:\n```python\nx = 1\n  y = 2\n" } });
      vi.advanceTimersByTime(2000);
      expect(document.querySelector(".chat-reply pre code")?.textContent).toBe("x = 1\n  y = 2");
      vi.useRealTimers();
    });
    it("uses no fade at all under reduced motion, with the same pacing and result", () => {
      vi.useFakeTimers();
      (window as any).matchMedia = () => ({ matches: true });
      streamReply(reply);
      for (let step = 0; step < 40; step++) {
        vi.advanceTimersByTime(100);
        expect(document.querySelector(".chat-reply .stream-word")).toBeNull();
      }
      expect(replyHtml(document.querySelector(".chat-reply")!)).toBe(restored());
      vi.useRealTimers();
    });
    it("does not rebuild an unchanged reply when the panel redraws", () => {
      notify({ kind: "session_state", state: active });
      restored();
      const code = document.querySelector(".chat-reply pre");
      notify({ kind: "session_state", state: { ...active, active_seconds: 5 } });
      expect(document.querySelector(".chat-reply pre")).toBe(code);
    });
    it("shows a learner's own newlines", () => {
      notify({ kind: "session_state", state: active });
      notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", streaming: null, turns: [{ turn_id: 1, kind: "chat", text: "a\n  b" }] } });
      expect(document.querySelector(".chat-you")?.textContent).toBe("a\n  b");
      expect(readFileSync(new URL("../panel.css", import.meta.url), "utf8")).toMatch(/\.chat-you\s*\{\s*white-space:\s*pre-wrap/);
    });
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
  it("allows Start anywhere on NeetCode and disables it only on other sites", async () => {
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
    activeUrl = "https://neetcode.io/practice/";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(false));
    expect(document.getElementById("session-body")?.textContent).not.toContain("Open NeetCode to start.");
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", tabId: 7 });
    sendMessage.mockClear();
    activeUrl = "https://example.com/";
    updatedTab(7, { url: activeUrl });
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(true));
    expect(document.getElementById("session-body")?.textContent).toContain("Open NeetCode to start.");
    click("start-session");
    expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "session_command", command: "start" }));
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
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
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
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "closed", active_seconds: 1394, cap_minutes: 35, cap_seconds_left: 600, problem: { slug: "daily-temperatures", title: "Daily Temperatures" } } });
    expect(document.getElementById("session-body")?.textContent).toContain("Pick up where you left off?");
    expect(document.getElementById("session-body")?.textContent).toContain("Daily Temperatures, 23 min");
    expect(document.getElementById("timer")?.classList.contains("stopped")).toBe(true);
    expect(document.getElementById("clock")?.textContent).toBe("10:00");
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
    await vi.waitFor(() => expect((document.getElementById("start-session") as HTMLButtonElement).disabled).toBe(false));
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "check", check_message: "Open a problem and I'll pick the timer back up.", active_seconds: 1394, problem: { slug: "daily-temperatures", title: "Daily Temperatures" } } });
    expect(document.querySelector(".welcome-back")?.textContent).toContain("Daily Temperatures, 23 min");
    expect(document.getElementById("session-body")?.textContent).not.toContain("Open a problem and I'll pick the timer back up.");
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7, problem_slug: "daily-temperatures" });
    expect(tabUpdate).not.toHaveBeenCalled();
  });

  it("resumes directly when the panel tab is already on the saved problem", () => {
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
  });

  it.each(["check", "focus"] as const)("offers the welcome-back card for a %s pause whose session tab is lost, and Resume binds the panel tab", reason => {
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: reason, check_message: "Can't read the editor", active_seconds: 600, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    expect(document.querySelector(".welcome-back")?.textContent).toContain("Duplicate Integer, 10 min");
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
  });

  it.each(["question", "solution/", "submissions?tab=1", "discuss#top"])("resumes directly when the panel tab is on the saved problem\'s %s tab", async tab => {
    activeUrl = `https://neetcode.io/problems/duplicate-integer/${tab}`;
    updatedTab(7, { url: activeUrl });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    await vi.waitFor(() => expect(document.getElementById("welcome-resume")).toBeTruthy());
    click("welcome-resume");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
  });

  it("enables the composer for an active session whatever the panel tab shows", async () => {
    activeUrl = "https://neetcode.io/practice/";
    updatedTab(7, { url: activeUrl });
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, problem: { slug: "duplicate-integer", title: "Duplicate Integer" } } });
    await vi.waitFor(() => expect(document.getElementById("help-request")).toBeTruthy());
    expect((document.getElementById("help-request") as HTMLButtonElement).disabled).toBe(false);
    expect((document.getElementById("chat-input") as HTMLTextAreaElement).disabled).toBe(false);
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
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "restart", active_seconds: 1394 } });
    expect(document.getElementById("session-body")?.textContent).toContain("23 min");
    expect(document.getElementById("session-body")?.textContent).not.toContain("Daily Temperatures");
  });

  it("labels a quiet End session button and accessible pause/resume icons", async () => {
    await bindPanelTab();
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
    expect(document.getElementById("end-session")?.textContent).toBe("End session");
    expect(document.getElementById("end-session")?.classList.contains("end-session")).toBe(true);
    expect(document.getElementById("pause-session")?.getAttribute("aria-label")).toBe("Pause");
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button" } });
    expect(document.getElementById("resume-session")?.getAttribute("aria-label")).toBe("Resume");
    notify({ kind: "session_state", state: session });
    expect(document.getElementById("end-session")).toBeNull();
  });
  it("starts without a budget, then pauses, resumes and ends through session commands", async () => {
    await bindPanelTab();
    click("skip-name");
    notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "", theme: "match" } });
    expect(document.getElementById("budget-minutes")).toBeNull();
    expect(document.querySelectorAll(".budget-chip")).toHaveLength(0);
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", tabId: 7 });
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
    click("pause-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "pause" });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button" } });
    click("resume-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume", tabId: 7 });
    click("end-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "end" });
  });
  it("perks before a run outcome and clears the reaction on pause", async () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
    const normal = document.getElementById("remark")?.textContent;
    notify({ kind: "reader_reaction", reaction: "run_fail" });
    expect(document.getElementById("remark")?.textContent).toMatch(/Oof|not quite|close/i);
    expect(document.querySelector("#header-otter .otter")?.classList.contains("react-perk")).toBe(true);
    await vi.waitFor(() => expect(document.querySelector("#header-otter .otter")?.classList.contains("react-oof")).toBe(true));
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button" } });
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
  it("shows an accepted remark and caption, and a time-up card when the cap is up", () => {
    const active = { ...session, state: "active", mode: "watching", running: true, cap_minutes: 35, problem: { slug: "two-sum", title: "Two Sum" } } as SessionState;
    notify({ kind: "session_state", state: active });
    expect(document.querySelector(".active-otter figcaption")?.textContent).toMatch(/here to help/i);
    notify({ kind: "session_state", state: { ...active, accepted: true } });
    expect(document.getElementById("status-dot")?.classList.contains("good")).toBe(true);
    expect(document.getElementById("remark")?.textContent).toMatch(/Accepted|Cracked|Shell/);
    expect(document.querySelector(".active-otter figcaption")?.textContent).toContain("Accepted! Keep polishing, or move on.");
    notify({ kind: "session_state", state: { ...active, time_up: { can_extend: true } } });
    expect(document.querySelector(".time-up-card")?.textContent).toContain("Time's up");
    expect(document.querySelector(".active-otter")).toBeNull();
  });
  it("sends the time-up choices and Skip, and hides auto-resume", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, time_up: { can_extend: true } } });
    click("time-up-extend");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "time_up_extend" });
    click("time-up-debrief");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "time_up_debrief" });
    click("time-up-skip");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "skip" });
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, time_up: { can_extend: false } } });
    expect(document.getElementById("time-up-extend")).toBeNull();
    expect(document.getElementById("time-up-debrief")).toBeTruthy();
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "focus" } });
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
  describe("Planner, recap, cap and flags", () => {
    const suggestion = { type: "suggestion" as const, ts: "now", slug: "house-robber", title: "House Robber", difficulty: "Medium" as const, cap_minutes: 35, steer: "more dynamic programming", message: "A dynamic programming problem next.", source: "planner" as const };
    const showStart = () => {
      notify({ kind: "settings_state", settings: { type: "settings_state", ts: "now", name: "Ada", theme: "match" } });
      notify({ kind: "session_state", state: session });
    };
    const startSession = () => notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
    it("shows a suggestion by title only, with its cap, steer and what the Planner understood", () => {
      showStart();
      startSession();
      notify({ kind: "suggestion", message: suggestion });
      const card = document.querySelector(".suggestion-card")!;
      expect(card.textContent).toContain("House Robber");
      expect(card.textContent).toContain("Medium, 35 min cap");
      expect(card.textContent).toContain("Steering: more dynamic programming");
      expect(card.textContent).toContain("A dynamic programming problem next.");
      expect(card.textContent).not.toMatch(/category|pattern|Stack/i);
      expect(document.getElementById("planner-go")).toBeTruthy();
      expect(document.getElementById("planner-swap")).toBeTruthy();
      expect(document.getElementById("planner-clear")).toBeTruthy();
    });
    it("shows the otter choosing a problem, with the composer open for a steer, until the suggestion arrives", () => {
      showStart();
      startSession();
      const card = document.querySelector(".planner-waiting-card")!;
      expect(card.textContent).toContain("Choosing a problem for you...");
      expect(card.querySelector(".dots")).toBeTruthy();
      expect(card.querySelector("svg.otter")).toBeTruthy();
      expect((document.getElementById("composer") as HTMLElement).hidden).toBe(false);
      notify({ kind: "suggestion", message: suggestion });
      expect(document.querySelector(".planner-waiting-card")).toBeNull();
      expect(document.querySelector(".suggestion-card")?.textContent).toContain("House Robber");
      expect((document.getElementById("composer") as HTMLElement).hidden).toBe(false);
    });
    it("sends a steer typed while the first problem is being chosen and shows it above the wait", async () => {
      showStart();
      startSession();
      const input = document.getElementById("chat-input") as HTMLTextAreaElement;
      input.value = "more dynamic programming";
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      click("chat-send");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_chat", text: "more dynamic programming" });
      await vi.waitFor(() => expect(document.querySelector(".chat-you")?.textContent).toBe("more dynamic programming"));
      expect(document.querySelector(".planner-waiting-card")).toBeTruthy();
      notify({ kind: "suggestion", message: suggestion });
      expect(document.querySelector(".chat-you")?.textContent).toBe("more dynamic programming");
    });
    it("keeps a past card, the steer and the wait in order until the next suggestion replaces the wait", async () => {
      showStart();
      startSession();
      notify({ kind: "suggestion", message: suggestion });
      const input = document.getElementById("chat-input") as HTMLTextAreaElement;
      input.value = "fewer arrays";
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      click("chat-send");
      await vi.waitFor(() => expect(document.querySelector(".chat-you")?.textContent).toBe("fewer arrays"));
      const order = () => [...document.getElementById("session-body")!.children].flatMap(child => {
        if (child.classList.contains("past-card")) return ["past"];
        if (child.classList.contains("chat-you")) return ["you"];
        if (child.classList.contains("planner-waiting-card")) return ["waiting"];
        if (child.classList.contains("suggestion-card")) return ["live"];
        return [];
      });
      expect(order()).toEqual(["past", "you", "waiting"]);
      const past = document.querySelector(".past-card")!;
      expect(past.textContent).toContain("House Robber");
      expect(past.querySelector("button")).toBeNull();
      expect(past.querySelector(".steer-line")).toBeNull();
      notify({ kind: "suggestion", message: { ...suggestion, slug: "two-sum", title: "Two Sum" } });
      expect(order()).toEqual(["past", "you", "live"]);
      expect(document.querySelector(".suggestion-card")?.textContent).toContain("Two Sum");
    });
    it("replaces the wait with an alert after 200 seconds and sends planner_get from Try again", () => {
      vi.useFakeTimers();
      try {
        showStart();
        startSession();
        vi.advanceTimersByTime(199_999);
        expect(document.querySelector(".planner-timeout-card")).toBeNull();
        vi.advanceTimersByTime(1);
        const card = document.querySelector(".planner-timeout-card")!;
        expect(card.getAttribute("role")).toBe("alert");
        expect(card.textContent).toContain("The Planner is taking too long.");
        expect((document.getElementById("composer") as HTMLElement).hidden).toBe(true);
        sendMessage.mockClear();
        click("planner-retry");
        expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_get" });
        expect(document.querySelector(".planner-waiting-card")).toBeTruthy();
        expect(document.querySelector(".planner-timeout-card")).toBeNull();
        vi.advanceTimersByTime(200_000);
        expect(document.querySelector(".planner-timeout-card")).toBeTruthy();
      } finally { vi.useRealTimers(); }
    });
    it("keeps the old card as a past card while Swap chooses, then shows the next suggestion below the wait", () => {
      vi.useFakeTimers();
      try {
        showStart();
        startSession();
        notify({ kind: "suggestion", message: suggestion });
        sendMessage.mockClear();
        click("planner-swap");
        expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_swap" });
        expect(document.querySelector(".suggestion-card")).toBeNull();
        expect(document.querySelector(".past-card")?.textContent).toContain("House Robber");
        expect(document.querySelector(".planner-waiting-card")?.textContent).toContain("Choosing a problem for you...");
        expect((document.getElementById("composer") as HTMLElement).hidden).toBe(false);
        notify({ kind: "suggestion", message: { ...suggestion, slug: "two-sum", title: "Two Sum" } });
        expect(document.querySelector(".planner-waiting-card")).toBeNull();
        expect(document.querySelector(".past-card")?.textContent).toContain("House Robber");
        expect(document.querySelector(".suggestion-card")?.textContent).toContain("Two Sum");
        expect((document.getElementById("planner-go") as HTMLButtonElement).disabled).toBe(false);
        vi.advanceTimersByTime(200_000);
        expect(document.querySelector(".planner-timeout-card")).toBeNull();
      } finally { vi.useRealTimers(); }
    });
    it("keeps the old card as a past card while Clear chooses and times out from that request", () => {
      vi.useFakeTimers();
      try {
        showStart();
        startSession();
        notify({ kind: "suggestion", message: suggestion });
        sendMessage.mockClear();
        click("planner-clear");
        expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_clear" });
        expect(document.querySelector(".suggestion-card")).toBeNull();
        expect(document.querySelector(".past-card")?.textContent).toContain("House Robber");
        expect(document.querySelector(".planner-waiting-card")?.textContent).toContain("Choosing a problem for you...");
        vi.advanceTimersByTime(199_999);
        expect(document.querySelector(".planner-timeout-card")).toBeNull();
        vi.advanceTimersByTime(1);
        expect(document.querySelector(".suggestion-card")).toBeNull();
        expect(document.querySelector(".past-card")?.textContent).toContain("House Robber");
        expect(document.querySelector(".planner-timeout-card")?.textContent).toContain("The Planner is taking too long.");
      } finally { vi.useRealTimers(); }
    });
    it("clears the Planner wait when a problem opens, on pause, and on end", () => {
      vi.useFakeTimers();
      try {
        showStart();
        startSession();
        notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, problem: { slug: "two-sum", title: "Two Sum" } } });
        vi.advanceTimersByTime(200_000);
        expect(document.querySelector(".planner-timeout-card")).toBeNull();

        notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
        expect(document.querySelector(".planner-waiting-card")).toBeTruthy();
        vi.advanceTimersByTime(50_000);
        notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button" } });
        vi.advanceTimersByTime(200_000);
        expect(document.querySelector(".planner-timeout-card")).toBeNull();

        notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
        notify({ kind: "session_state", state: { ...session, state: "ended", mode: "idle", running: false } });
        vi.advanceTimersByTime(200_000);
        expect(document.querySelector(".planner-timeout-card")).toBeNull();
        expect(document.getElementById("start-session")).toBeTruthy();
      } finally { vi.useRealTimers(); }
    });
    it("shows only Start while idle, holding a suggestion until the session starts", () => {
      showStart();
      notify({ kind: "suggestion", message: suggestion });
      expect(document.getElementById("start-session")).toBeTruthy();
      expect(document.querySelector(".suggestion-card")).toBeNull();
      expect((document.getElementById("composer") as HTMLElement).hidden).toBe(true);
      notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true } });
      expect(document.getElementById("start-session")).toBeNull();
      expect(document.querySelector(".suggestion-card")?.textContent).toContain("House Robber");
      expect((document.getElementById("composer") as HTMLElement).hidden).toBe(false);
      expect((document.getElementById("chat-input") as HTMLTextAreaElement).placeholder).toBe("Tell the Planner what to focus on…");
    });
    it.each([
      ["preparing", [], undefined],
      ["ready", ["Dropped: visited marking."], undefined],
      ["failed", [], "Session review is missing: offline"],
    ] as const)("shows a %s recap above the Start card and nothing else", (status, bullets, message) => {
      showStart();
      notify({ kind: "suggestion", message: suggestion });
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status, bullets: [...bullets], ...(message ? { message } : {}) } });
      const cards = [...document.getElementById("session-body")!.children].filter(child => child.classList.contains("card"));
      expect(cards[0]?.classList.contains("recap-card")).toBe(true);
      expect(cards[1]?.querySelector("#start-session")).toBeTruthy();
      expect(document.querySelector(".suggestion-card")).toBeNull();
      expect((document.getElementById("composer") as HTMLElement).hidden).toBe(true);
    });
    it("keeps a closed recap closed for that session, but shows the next session's recap", () => {
      showStart();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "ready", bullets: ["Dropped: visited marking."] } });
      click("recap-close");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "recap_seen", session_id: 12 });
      expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "recap_feedback" }));
      expect(document.querySelector(".recap-card")).toBeNull();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "ready", bullets: ["Dropped: visited marking."] } });
      expect(document.querySelector(".recap-card")).toBeNull();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 13, status: "ready", bullets: ["Kept: two pointers."] } });
      expect(document.querySelector(".recap-card")?.textContent).toContain("Kept: two pointers.");
    });
    it("shows the Tutor composer and no suggestion card while a problem is open", () => {
      showStart();
      notify({ kind: "suggestion", message: suggestion });
      notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, problem: { slug: "two-sum", title: "Two Sum" } } });
      expect(document.getElementById("start-session")).toBeNull();
      expect(document.querySelector(".suggestion-card")).toBeNull();
      expect((document.getElementById("composer") as HTMLElement).hidden).toBe(false);
      expect((document.getElementById("chat-input") as HTMLTextAreaElement).placeholder).toBe("Message the Tutor…");
      notify({ kind: "suggestion", message: suggestion });
      expect(document.querySelector(".suggestion-card")).toBeNull();
      expect((document.getElementById("chat-input") as HTMLTextAreaElement).placeholder).toBe("Message the Tutor…");
    });
    it("sends Go, Swap, Clear and planner chat from the suggestion card", () => {
      showStart();
      startSession();
      notify({ kind: "suggestion", message: suggestion });
      click("planner-swap");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_swap" });
      notify({ kind: "suggestion", message: suggestion });
      click("planner-clear");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_clear" });
      notify({ kind: "suggestion", message: suggestion });
      const input = document.getElementById("chat-input") as HTMLTextAreaElement;
      input.value = "fewer arrays";
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      click("chat-send");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_chat", text: "fewer arrays" });
      notify({ kind: "suggestion", message: suggestion });
      click("planner-go");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "planner_go", tabId: 7 });
      expect(document.querySelector(".suggestion-card")).toBeTruthy();
      notify({ kind: "planner_open", message: { type: "planner_open", ts: "now", slug: "house-robber", title: "House Robber" } });
      expect(document.querySelector(".suggestion-card")).toBeNull();
    });
    it("says the Tutor takes steering at the next suggestion mid-problem and offers Skip", () => {
      notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, problem: { slug: "two-sum", title: "Two Sum" } } });
      expect(document.getElementById("composer-note")?.textContent).toContain("next suggestion");
      click("skip-problem");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "skip" });
    });
    it("shows the reference status line until the reference is checked", () => {
      const active = { ...session, state: "active", mode: "watching", running: true, problem: { slug: "two-sum", title: "Two Sum" }, reference_status: "not_checked_yet" as const, reference_message: "Reference not checked yet" };
      notify({ kind: "session_state", state: active });
      expect(document.querySelector(".reference-status")?.textContent).toBe("Reference not checked yet");
      notify({ kind: "session_state", state: { ...active, reference_status: "checked" as const, reference_message: undefined } });
      expect(document.querySelector(".reference-status")).toBeNull();
    });
    it("shows the otter preparing notes, then the recap bullets and feedback box", () => {
      showStart();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "preparing", bullets: [] } });
      expect(document.querySelector(".recap-card")?.textContent).toContain("Notes on your last session");
      expect(document.getElementById("recap-refresh")).toBeNull();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "ready", bullets: ["Dropped: visited marking."] } });
      expect(document.querySelector(".recap-card")?.textContent).toContain("Dropped: visited marking.");
      expect(document.getElementById("recap-feedback")).toBeTruthy();
      const input = document.getElementById("recap-feedback") as HTMLInputElement;
      input.value = "That drop was wrong";
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      click("recap-close");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "recap_feedback", session_id: 12, text: "That drop was wrong" });
      notify({ kind: "recap_noted", message: { type: "recap_noted", session_id: 12, note_id: 9, message: "Noted. For a grade correction, use Progress." } });
      expect(document.querySelector(".recap-noted")?.textContent).toContain("Noted");
      sendMessage.mockClear();
      click("recap-close");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "recap_seen", session_id: 12 });
      expect(document.querySelector(".recap-card")).toBeNull();
    });
    it("shows no recap card when a ready recap has nothing to report", () => {
      showStart();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "ready", bullets: [] } });
      expect(document.querySelector(".recap-card")).toBeNull();
      expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "recap_feedback" }));
    });
    it("closes the recap as seen without sending an empty feedback note", () => {
      showStart();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "ready", bullets: ["Dropped: visited marking."] } });
      click("recap-close");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "recap_seen", session_id: 12 });
      expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "recap_feedback" }));
      expect(document.querySelector(".recap-card")).toBeNull();
    });
    it("shows a failed recap and a failed feedback review loudly", () => {
      showStart();
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "failed", bullets: [], message: "Session review is missing: offline" } });
      expect(document.querySelector(".recap-card")?.getAttribute("role")).toBe("alert");
      expect(document.querySelector(".recap-card")?.textContent).toContain("Session review is missing: offline");
      notify({ kind: "recap_state", message: { type: "recap_state", session_id: 12, status: "feedback_failed", bullets: ["Dropped: visited marking."], message: "Feedback review failed; your note was saved." } });
      expect(document.querySelector(".recap-card [role=alert]")?.textContent).toContain("your note was saved");
    });
    it("shows the time-up card, debrief actions and Skip", () => {
      const active = { ...session, state: "active", mode: "watching", running: true, cap_minutes: 35, problem: { slug: "two-sum", title: "Two Sum" }, time_up: { can_extend: true } };
      notify({ kind: "session_state", state: active });
      expect(document.getElementById("time-up-extend")).toBeTruthy();
      expect(document.getElementById("time-up-debrief")).toBeTruthy();
      expect(document.getElementById("composer")?.hidden).toBe(true);
      notify({ kind: "session_state", state: { ...active, mode: "debrief", time_up: null } });
      click("debrief-done");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "done" });
      click("debrief-skip");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "skip" });
    });
    it("offers a quiet flag icon on each reply and turns it into Noted after sending", async () => {
      notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, problem: { slug: "two-sum", title: "Two Sum" } } });
      notify({ kind: "chat_state", message: { type: "chat_state", ts: "now", streaming: null, turns: [{ turn_id: 7, kind: "reply", text: "Try a set", gap: "none", gap_note: null, help_kind: "hint" }] } });
      const flag = document.getElementById("seems-wrong-7");
      expect(flag?.getAttribute("aria-label")).toBe("Seems wrong");
      expect(flag?.title).toBe("Seems wrong");
      expect(flag?.querySelector("svg")).toBeTruthy();
      expect(document.querySelector(".flag-row")).toBeNull();
      click("seems-wrong-7");
      const input = document.querySelector(".flag-line") as HTMLInputElement;
      expect(input.placeholder).toBe("What looks off? (optional)");
      input.value = "This misses duplicates";
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      click("flag-send-7");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "reference_flag", message_id: 7, line: "This misses duplicates" });
      await vi.waitFor(() => expect(document.querySelector(".flag-noted")?.textContent).toContain("Noted"));
      expect(document.getElementById("seems-wrong-7")).toBeNull();
      expect(document.querySelector(".flag-row")).toBeNull();
      notify({ kind: "reference_flagged", message: { type: "reference_flagged", ts: "now", message_id: 7, note_id: 9 } });
      expect(document.querySelector(".flag-noted")).toBeTruthy();
    });
    it("lists disputed references in settings and applies a pasted verdict", () => {
      notify({ kind: "reference_state", state: { type: "reference_state", ts: "now", count: 1, references: [{ slug: "top-k-elements-in-list", title: "Top K Frequent Elements", uncertain: ["heap.complexity"], prompt: "Settle this reference" }] } });
      click("header-otter");
      expect(document.getElementById("settings-card")?.textContent).toContain("Top K Frequent Elements");
      const input = document.getElementById("verdict-top-k-elements-in-list") as HTMLTextAreaElement;
      input.value = '{"verdict":"reference is fine"}';
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      click("reference-apply-top-k-elements-in-list");
      expect(sendMessage).toHaveBeenCalledWith({ kind: "reference_apply", slug: "top-k-elements-in-list", reply: '{"verdict":"reference is fine"}' });
      notify({ kind: "reference_verdict", message: { type: "reference_verdict", ts: "now", slug: "top-k-elements-in-list", accepted: false, reason: "Invalid verdict: garbled" } });
      expect(document.getElementById("settings-card")?.textContent).toContain("Verdict refused: Invalid verdict: garbled");
    });
    it("shows a companion error loudly", () => {
      notify({ kind: "error", message: "The Planner failed and no eligible problems are available to suggest." });
      expect(document.querySelector(".alert")?.textContent).toContain("The Planner failed");
    });
  });
  it("has no remote asset URLs", () => {
    const html = readFileSync(new URL("../panel.html", import.meta.url), "utf8");
    const css = readFileSync(new URL("../panel.css", import.meta.url), "utf8");
    expect(html + css).not.toMatch(/https?:\/\//);
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

type Listener = (...args: any[]) => any;
const messages: Listener[] = [];
const activated: Listener[] = [];
const updated: Listener[] = [];
const created: Listener[] = [];
const removed: Listener[] = [];
const installed: Listener[] = [];
const focused: Listener[] = [];
const sent: { tab: number; message: any }[] = [];
let saved: Record<string, any> = {};
let socket: FakeSocket;
let historyReply: any;

class FakeSocket {
  readyState = 0;
  onopen: Listener | null = null;
  onclose: Listener | null = null;
  onerror: Listener | null = null;
  onmessage: Listener | null = null;
  sent: any[] = [];
  constructor(_url: string) { socket = this; }
  send(raw: string) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.onclose?.(); }
}

async function dispatch(message: any, tab?: any): Promise<any> {
  return new Promise(resolve => {
    const returned = messages.at(-1)?.(message, { tab }, resolve);
    if (returned !== true) resolve(undefined);
  });
}
async function flush() { await vi.waitFor(() => { expect(messages.length).toBeGreaterThan(0); }); }
function pushSession(mode: "idle" | "watching" | "paused") {
  socket.onmessage?.({ data: JSON.stringify({ type: "session_state", ts: "now", state: mode === "watching" ? "active" : mode, mode, paused_reason: mode === "paused" ? "button" : null, check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 0, running: mode === "watching", problem: null, accepted: false }) });
}
// Watching a session reports where its tab is straight away; tests about other messages start after that.
async function settleFocus() {
  await vi.waitFor(() => expect(socket.sent).toContainEqual(expect.objectContaining({ type: "focus_changed" })));
  socket.sent.length = 0;
}
async function start() {
  vi.resetModules();
  await import("./worker");
  await flush();
  await dispatch({ kind: "get_mode" });
}

beforeEach(() => {
  vi.useRealTimers();
  messages.length = activated.length = updated.length = created.length = removed.length = installed.length = focused.length = sent.length = 0;
  saved = { readerState: { readerError: "", readerActivity: "No reader events yet.", focusedWindow: 3, sessionTabId: 7, boundFocus: { windowId: 3, active: true } } };
  historyReply = { urls: ["https://leetcode.com/problems/contains-duplicate/"] };
  vi.stubGlobal("__LEETOTTER_PORT__", 8765);
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("chrome", {
    storage: { session: { get: vi.fn(async () => ({ readerState: saved.readerState })), set: vi.fn(async (value: any) => { saved = { ...saved, ...value }; }) } },
    runtime: { onMessage: { addListener: (fn: Listener) => messages.push(fn) }, onInstalled: { addListener: (fn: Listener) => installed.push(fn) }, sendMessage: vi.fn(async () => {}) },
    sidePanel: { setPanelBehavior: vi.fn(async () => {}), setOptions: vi.fn(async () => {}) },
    tabs: {
      query: vi.fn(async () => [{ id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" }]),
      get: vi.fn(async (id: number) => ({ id, windowId: 3, active: id === 7, url: id === 7 ? "https://neetcode.io/problems/duplicate-integer/" : "https://example.com/" })),
      update: vi.fn(async (id: number, change: chrome.tabs.UpdateProperties) => ({ id, url: change.url })),
      sendMessage: vi.fn(async (tab: number, message: any) => { sent.push({ tab, message }); return message.kind === "read_history" ? historyReply : undefined; }),
      onActivated: { addListener: (fn: Listener) => activated.push(fn) },
      onUpdated: { addListener: (fn: Listener) => updated.push(fn) },
      onCreated: { addListener: (fn: Listener) => created.push(fn) },
      onRemoved: { addListener: (fn: Listener) => removed.push(fn) },
    },
    windows: { WINDOW_ID_NONE: -1, getLastFocused: vi.fn(async () => ({ id: 3, focused: true })), onFocusChanged: { addListener: (fn: Listener) => focused.push(fn) } },
  });
});

describe("side panel availability", () => {
  it("sets toolbar behavior, disables the default panel, and enables existing NeetCode tabs on worker start", async () => {
    vi.mocked(chrome.tabs.query).mockImplementation(async (query: any) => query.url
      ? [{ id: 7, url: "https://neetcode.io/problems/duplicate-integer/" } as chrome.tabs.Tab]
      : [
          { id: 7, url: "https://neetcode.io/practice/" } as chrome.tabs.Tab,
          { id: 8, url: "https://example.com/" } as chrome.tabs.Tab,
        ]);
    await start();
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 8, enabled: false }));
    expect(chrome.sidePanel.setPanelBehavior).toHaveBeenCalledWith({ openPanelOnActionClick: true });
    expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ enabled: false });
    expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 7, path: "panel.html", enabled: true });
  });
  it("updates availability when a tab is activated, navigated, or created", async () => {
    await start();
    vi.mocked(chrome.sidePanel.setOptions).mockClear();
    activated.at(-1)?.({ tabId: 8, windowId: 3 });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 8, enabled: false }));
    activated.at(-1)?.({ tabId: 7, windowId: 3 });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 7, path: "panel.html", enabled: true }));
    updated.at(-1)?.(8, { url: "https://neetcode.io/practice/" }, { id: 8 });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 8, path: "panel.html", enabled: true }));
    updated.at(-1)?.(8, { url: "https://example.com/" }, { id: 8 });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 8, enabled: false }));
    created.at(-1)?.({ id: 9, url: "https://neetcode.io/" });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 9, path: "panel.html", enabled: true }));
    created.at(-1)?.({ id: 10, url: "https://other.example/" });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 10, enabled: false }));
    created.at(-1)?.({ id: 11, url: "https://neetcode.io.evil.example/" });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 11, enabled: false }));
    expect(await dispatch({ kind: "get_mode" })).toBe("idle");
  });
  it("sets toolbar behavior again on install", async () => {
    await start();
    vi.mocked(chrome.sidePanel.setPanelBehavior).mockClear();
    installed.at(-1)?.();
    await vi.waitFor(() => expect(chrome.sidePanel.setPanelBehavior).toHaveBeenCalledWith({ openPanelOnActionClick: true }));
  });
});

describe("Tutor relay", () => {
  it("requests chat on connection and forwards Tutor events and snapshots", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "status", ts: "now", state: "connected" }) });
    expect(socket.sent).toContainEqual(expect.objectContaining({ type: "chat_get" }));
    const chunk = { type: "tutor_chunk", ts: "now", turn_id: 4, attempt: 1, text: "Try " };
    const snapshot = { type: "chat_state", ts: "now", turns: [], streaming: null };
    socket.onmessage?.({ data: JSON.stringify(chunk) });
    socket.onmessage?.({ data: JSON.stringify(snapshot) });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "tutor", message: chunk });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "chat_state", message: snapshot });
    const sentBeforeStart = socket.sent.length;
    socket.onmessage?.({ data: JSON.stringify({ type: "tutor_start", ts: "now", turn_id: 5, request: "chat", press_index: null }) });
    expect(socket.sent).toHaveLength(sentBeforeStart + 1);
    expect(socket.sent.at(-1)).toMatchObject({ type: "chat_get" });
  });
  it("sends panel commands and refuses them plainly when disconnected", async () => {
    await start();
    for (const command of [{ kind: "help_request" }, { kind: "chat_message", text: "Why?" }, { kind: "chat_get" }]) {
      expect(await dispatch(command)).toEqual({ error: "Tutor service isn't connected." });
    }
    socket.readyState = 1;
    expect(await dispatch({ kind: "help_request" })).toEqual({ sent: true });
    expect(await dispatch({ kind: "chat_message", text: "Why?" })).toEqual({ sent: true });
    expect(await dispatch({ kind: "chat_get" })).toEqual({ sent: true });
    expect(socket.sent.slice(-3)).toMatchObject([{ type: "help_request" }, { type: "chat_message", payload: { text: "Why?" } }, { type: "chat_get" }]);
    expect(await dispatch({ kind: "chat_message", text: " " })).toEqual({ error: expect.any(String) });
  });
});

describe("worker reader delivery", () => {
  it("forwards a validated problem statement without a panel reaction", async () => {
    await start(); socket.readyState = 1; pushSession("watching"); await dispatch({ kind: "get_mode" }); await settleFocus();
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    const payload = { slug: "duplicate-integer", statement: "Given an integer array nums...", truncated: true, source: "question-tab" };
    const before = vi.mocked(chrome.runtime.sendMessage).mock.calls.length;
    expect(await dispatch({ kind: "reader_event", event: { type: "problem_statement", payload } }, tab)).toEqual({ accepted: true, mode: "watching" });
    expect(socket.sent.at(-1)).toMatchObject({ type: "problem_statement", payload });
    expect(vi.mocked(chrome.runtime.sendMessage).mock.calls.slice(before).some(([message]) => {
      const value: unknown = message;
      return typeof value === "object" && value !== null && "kind" in value &&
        (value.kind === "reader_reaction" || value.kind === "reader_error");
    })).toBe(false);
  });
  it("shows rate-limit notices without forwarding or pausing, then clears them on a result and new session", async () => {
    await start(); socket.readyState = 1; pushSession("watching"); await dispatch({ kind: "get_mode" }); await settleFocus();
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    const notice = "NeetCode is rate-limiting your runs. Wait a moment and try again.";
    const before = socket.sent.length;
    expect(await dispatch({ kind: "reader_event", event: { type: "reader_notice", payload: { message: notice } } }, tab)).toEqual({ accepted: true, mode: "watching" });
    expect(socket.sent).toHaveLength(before);
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "reader_error", message: notice });
    expect(await dispatch({ kind: "get_reader_error" })).toBe(notice);
    expect(await dispatch({ kind: "get_mode" })).toBe("watching");
    const run = { type: "run_result", payload: { slug: "duplicate-integer", cases: [{ passed: true, error_type: null, error_message: "", runtime: "", failing_case: null }] } };
    await dispatch({ kind: "reader_event", event: run }, tab);
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
    await dispatch({ kind: "reader_event", event: { type: "reader_notice", payload: { message: notice } } }, tab);
    const submit = { type: "submit_result", payload: { slug: "duplicate-integer", accepted: true, error_type: null, error_message: "", runtime: "", memory: 0, passed: 1, total: 1, failing_case: null } };
    await dispatch({ kind: "reader_event", event: submit }, tab);
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
    await dispatch({ kind: "reader_event", event: { type: "reader_notice", payload: { message: notice } } }, tab);
    pushSession("idle"); await dispatch({ kind: "get_mode" });
    pushSession("watching"); await dispatch({ kind: "get_mode" });
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
  });
  it("relays reactions only for delivered events while watching", async () => {
    await start(); socket.readyState = 1; pushSession("watching"); await dispatch({ kind: "get_mode" });
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    const run = { type: "run_result", payload: { slug: "duplicate-integer", cases: [{ passed: false, error_type: null, error_message: "", runtime: "", failing_case: null }] } };
    await dispatch({ kind: "reader_event", event: run }, tab);
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "reader_reaction", reaction: "run_fail" });
    vi.mocked(chrome.runtime.sendMessage).mockClear();
    pushSession("paused"); await dispatch({ kind: "get_mode" });
    await dispatch({ kind: "reader_event", event: { type: "submit_result", payload: { slug: "duplicate-integer", accepted: false, error_type: null, error_message: "", runtime: "", memory: 0, passed: 0, total: 1, failing_case: null } } }, tab);
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "reader_reaction" }));
    pushSession("idle"); await dispatch({ kind: "get_mode" });
    await dispatch({ kind: "reader_event", event: run }, tab);
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "reader_reaction" }));
  });
  it("requests settings on connect, forwards updates, and relays panel changes", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "status", ts: "now", state: "connected" }) });
    expect(socket.sent.map(item => item.type)).toContain("settings_get");
    const settings = { type: "settings_state", ts: "now", name: null, theme: "match" };
    socket.onmessage?.({ data: JSON.stringify(settings) });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "settings_state", settings });
    expect(await dispatch({ kind: "get_settings_state" })).toEqual(settings);
    expect(await dispatch({ kind: "settings_set", key: "name", value: "Ada" })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "settings_set", payload: { key: "name", value: "Ada" } });
  });
  it("rejects blank name updates while allowing first-run Skip", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "settings_state", ts: "now", name: null, theme: "match" }) });
    const before = socket.sent.length;
    expect(await dispatch({ kind: "settings_set", key: "name", value: "   " })).toEqual({ error: "Invalid setting." });
    expect(socket.sent).toHaveLength(before);
    expect(await dispatch({ kind: "skip_name" })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "settings_set", payload: { key: "name", value: "" } });
    socket.onmessage?.({ data: JSON.stringify({ type: "settings_state", ts: "now", name: "Ada", theme: "match" }) });
    const after = socket.sent.length;
    expect(await dispatch({ kind: "skip_name" })).toEqual({ error: "Invalid setting." });
    expect(socket.sent).toHaveLength(after);
  });
  it("takes reader mode from session_state and delivers the full state to the panel", async () => {
    await start(); socket.readyState = 1;
    const session = { type: "session_state", ts: "now", state: "active", mode: "watching", paused_reason: null,
      check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 0, running: true, problem: null, accepted: false };
    socket.onmessage?.({ data: JSON.stringify(session) });
    await vi.waitFor(() => expect(sent).toContainEqual({ tab: 7, message: { kind: "watch_mode", mode: "watching" } }));
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "session_state", state: session });
    expect(await dispatch({ kind: "set_mode", mode: "idle" })).toBeUndefined();
    expect(await dispatch({ kind: "get_mode" })).toBe("watching");
  });

  it("pauses through a reader check when an existing problem tab cannot hear watch mode, once until recovery", async () => {
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.sendMessage).mockImplementation(async (_tab: number, message: any) => {
      if (message.kind === "watch_mode" && message.mode === "watching") throw new Error("Receiving end does not exist");
    });
    const error = "Leetotter can't see this NeetCode page. Reload the tab.";
    pushSession("watching");
    await vi.waitFor(() => expect(socket.sent.filter(item => item.type === "reader_check")).toHaveLength(1));
    expect(socket.sent.at(-1)).toMatchObject({ type: "reader_check", payload: { ok: false, message: error } });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "reader_error", message: error });
    expect(await dispatch({ kind: "get_reader_error" })).toBe(error);
    pushSession("paused");
    pushSession("watching");
    await vi.waitFor(() => expect(chrome.tabs.query).toHaveBeenCalledTimes(3));
    expect(socket.sent.filter(item => item.type === "reader_check")).toHaveLength(1);

    vi.mocked(chrome.tabs.sendMessage).mockResolvedValue(undefined);
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    expect((await dispatch({ kind: "reader_event", event: { type: "reader_check", payload: { ok: true } } }, tab)).accepted).toBe(true);
    expect(socket.sent.at(-1)).toMatchObject({ type: "reader_check", payload: { ok: true } });
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
    pushSession("watching");
    expect(await dispatch({ kind: "get_mode" })).toBe("watching");
  });

  it("does not fail a reader check without a problem tab or while idle", async () => {
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.sendMessage).mockRejectedValue(new Error("Receiving end does not exist"));
    vi.mocked(chrome.tabs.query).mockResolvedValueOnce([]);
    pushSession("watching");
    await vi.waitFor(() => expect(chrome.tabs.query).toHaveBeenCalledTimes(2));
    expect(socket.sent.filter(item => item.type === "reader_check")).toHaveLength(0);
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
    const newTab = { id: 8, windowId: 3, active: true, url: "https://neetcode.io/problems/two-sum/" };
    expect((await dispatch({ kind: "reader_event", event: { type: "reader_check", payload: { ok: true } } }, newTab)).accepted).toBe(false);
    pushSession("idle");
    await vi.waitFor(async () => expect(await dispatch({ kind: "get_mode" })).toBe("idle"));
    expect(socket.sent.filter(item => item.type === "reader_check")).toHaveLength(0);
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
  });

  it("ignores failed watch mode delivery to a tab outside problem pages", async () => {
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.query).mockResolvedValueOnce([{ id: 9, url: "https://neetcode.io/practice/" } as chrome.tabs.Tab]);
    vi.mocked(chrome.tabs.sendMessage).mockRejectedValue(new Error("Receiving end does not exist"));
    pushSession("watching");
    await vi.waitFor(() => expect(chrome.tabs.query).toHaveBeenCalledTimes(2));
    expect(socket.sent.filter(item => item.type === "reader_check")).toHaveLength(0);
    expect(await dispatch({ kind: "get_reader_error" })).toBe("");
  });

  it("forwards a reader check and panel session commands", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "session_state", ts: "now", state: "active", mode: "watching", paused_reason: null, check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 0, running: true, problem: null, accepted: false }) });
    await dispatch({ kind: "get_mode" }); await settleFocus();
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    expect((await dispatch({ kind: "reader_event", event: { type: "reader_check", payload: { ok: false, message: "Editor missing" } } }, tab)).accepted).toBe(true);
    expect(socket.sent.at(-1)).toMatchObject({ type: "reader_check", payload: { ok: false, message: "Editor missing" } });
    expect(await dispatch({ kind: "session_command", command: "start", tabId: 7 })).toEqual({ sent: true });
    const started = socket.sent.filter(item => item.type === "session_start").at(-1);
    expect(started).toMatchObject({ type: "session_start" });
    expect(started).not.toHaveProperty("payload");
    for (const [command, type, payload] of [["pause", "session_pause", undefined], ["resume", "session_resume", undefined], ["end", "session_end", undefined], ["time_up_extend", "session_time_up", { choice: "extend" }], ["time_up_debrief", "session_time_up", { choice: "debrief" }], ["done", "session_done", undefined], ["skip", "session_skip", undefined]] as const) {
      expect(await dispatch({ kind: "session_command", command, ...(command === "resume" ? { tabId: 7 } : {}) })).toEqual({ sent: true });
      expect(socket.sent.at(-1)).toMatchObject({ type, ...(payload ? { payload } : {}) });
    }
  });
  it("only starts on the requested tab's current problem URL, while resume accepts another NeetCode page", async () => {
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.get).mockResolvedValue({ id: 7, url: "https://neetcode.io/practice/" } as chrome.tabs.Tab);
    expect(await dispatch({ kind: "session_command", command: "start", tabId: 7 })).toEqual({ error: "Open a problem on NeetCode to start a session." });
    expect(socket.sent.some(item => item.type === "session_start")).toBe(false);
    expect(await dispatch({ kind: "session_command", command: "resume", tabId: 7 })).toEqual({ sent: true });
  });
  it("binds the panel tab and opens the saved problem before resuming", async () => {
    saved.readerState.sessionTabId = null;
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.get).mockResolvedValue({ id: 7, url: "https://neetcode.io/practice/" } as chrome.tabs.Tab);
    vi.mocked(chrome.tabs.update).mockImplementation(async (id, change) => {
      expect(await dispatch({ kind: "get_session_binding" })).toEqual({ tabId: 7 });
      expect(socket.sent.some(item => item.type === "session_resume")).toBe(false);
      return { id, url: change.url } as chrome.tabs.Tab;
    });
    expect(await dispatch({ kind: "session_command", command: "resume", tabId: 7, problem_slug: "two-sum" })).toEqual({ sent: true });
    expect(chrome.tabs.update).toHaveBeenCalledWith(7, { url: "https://neetcode.io/problems/two-sum/" });
    expect(socket.sent.at(-1)).toMatchObject({ type: "session_resume" });
  });
  it("keeps a session paused and reports a rejected saved-problem navigation", async () => {
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.get).mockResolvedValue({ id: 7, url: "https://neetcode.io/practice/" } as chrome.tabs.Tab);
    vi.mocked(chrome.tabs.update).mockRejectedValue(new Error("No tab with id: 7"));
    expect(await dispatch({ kind: "session_command", command: "resume", tabId: 7, problem_slug: "two-sum" })).toEqual({ error: "Could not open the saved problem: No tab with id: 7" });
    expect(socket.sent.some(item => item.type === "session_resume")).toBe(false);
  });
  it("binds a session to one tab, routes watch mode, and refuses another tab", async () => {
    await start(); socket.readyState = 1;
    vi.mocked(chrome.tabs.query).mockResolvedValue([
      { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/two-sum/" },
      { id: 8, windowId: 3, active: false, url: "https://neetcode.io/problems/two-sum/" },
    ] as chrome.tabs.Tab[]);
    expect(await dispatch({ kind: "session_command", command: "start", tabId: 7 })).toEqual({ sent: true });
    pushSession("watching");
    await vi.waitFor(() => expect(sent).toContainEqual({ tab: 8, message: { kind: "watch_mode", mode: "idle" } }));
    expect(await dispatch({ kind: "get_session_binding" })).toEqual({ tabId: 7 });
    expect(await dispatch({ kind: "get_mode" }, { id: 8 })).toBe("idle");
    const before = socket.sent.length;
    expect(await dispatch({ kind: "session_command", command: "resume", tabId: 8 })).toEqual({ error: "A session is running in another tab." });
    expect(socket.sent).toHaveLength(before);
    expect(await dispatch({ kind: "reader_event", event: { type: "reader_check", payload: { ok: true } } }, { id: 8, url: "https://neetcode.io/problems/two-sum/" })).toEqual({ accepted: false, mode: "watching" });
    expect(socket.sent).toHaveLength(before);
  });

  it("pauses on bound tab removal and only once for repeated active states without a tab", async () => {
    await start(); socket.readyState = 1; pushSession("watching");
    removed.at(-1)?.(7);
    await vi.waitFor(() => expect(socket.sent.filter(item => item.type === "tab_closed")).toHaveLength(1));
    expect(await dispatch({ kind: "get_session_binding" })).toEqual({ tabId: null });
    pushSession("watching"); pushSession("watching");
    await dispatch({ kind: "get_mode" });
    expect(socket.sent.filter(item => item.type === "tab_closed")).toHaveLength(1);
  });

  it("resumes a disconnect pause once when its bound tab survives", async () => {
    await start(); socket.readyState = 1;
    const paused = { type: "session_state", ts: "now", state: "paused", mode: "paused", paused_reason: "disconnect", check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 60, running: false, problem: null, accepted: false };
    socket.onmessage?.({ data: JSON.stringify(paused) });
    socket.onmessage?.({ data: JSON.stringify(paused) });
    await vi.waitFor(() => expect(socket.sent.filter(item => item.type === "session_resume")).toHaveLength(1));
  });
  it("keeps the binding after a worker restart", async () => {
    await start(); socket.readyState = 1;
    expect(await dispatch({ kind: "session_command", command: "start", tabId: 7 })).toEqual({ sent: true });
    await start();
    expect(await dispatch({ kind: "get_session_binding" })).toEqual({ tabId: 7 });
  });
  it("pauses an orphaned active session once and does not auto-resume an unbound disconnect", async () => {
    saved.readerState.sessionTabId = null;
    await start(); socket.readyState = 1;
    pushSession("watching"); pushSession("watching");
    await vi.waitFor(() => expect(socket.sent.filter(item => item.type === "tab_closed")).toHaveLength(1));
    const paused = { type: "session_state", ts: "now", state: "paused", mode: "paused", paused_reason: "disconnect", check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 60, running: false, problem: null, accepted: false };
    socket.onmessage?.({ data: JSON.stringify(paused) });
    await dispatch({ kind: "get_mode" });
    expect(socket.sent.filter(item => item.type === "session_resume")).toHaveLength(0);
  });
  it("asks an open NeetCode tab for history and sends typed URLs to the companion", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "status", ts: "now", state: "connected" }) });
    expect(await dispatch({ kind: "import_history" })).toEqual({ pending: true });
    expect(sent).toContainEqual({ tab: 7, message: { kind: "read_history" } });
    expect(socket.sent.at(-1)).toMatchObject({ type: "import_history", payload: { urls: ["https://leetcode.com/problems/contains-duplicate/"] } });
    socket.onmessage?.({ data: JSON.stringify({ type: "import_result", ts: "now", result: { already_imported: false, matched: 1, unmatched: [] } }) });
    expect(await dispatch({ kind: "get_import_state" })).toMatchObject({ imported: true, result: { matched: 1 } });
  });

  it("names a missing tab and malformed cache response", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "status", ts: "now", state: "connected" }) });
    vi.mocked(chrome.tabs.query).mockResolvedValueOnce([]);
    expect(await dispatch({ kind: "import_history" })).toEqual({ error: "Open a neetcode.io tab to import history." });
    historyReply = { error: "synced-progress-cache is missing" };
    expect(await dispatch({ kind: "import_history" })).toEqual({ error: "synced-progress-cache is missing" });
  });
  it("waits for the companion mode after a simulated worker restart", async () => {
    await start();
    pushSession("watching"); await dispatch({ kind: "get_mode" });
    messages.length = 0;
    await start();
    expect(await dispatch({ kind: "get_mode" })).toBe("idle");
    pushSession("watching"); await dispatch({ kind: "get_mode" });
    expect(await dispatch({ kind: "get_mode" })).toBe("watching");
    expect(sent).toContainEqual({ tab: 7, message: { kind: "watch_mode", mode: "watching" } });
  });

  it("surfaces a dropped send and records activity only after delivery", async () => {
    await start(); pushSession("watching"); await dispatch({ kind: "get_mode" });
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    const event = { kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } };
    expect((await dispatch(event, tab)).accepted).toBe(false);
    expect(await dispatch({ kind: "get_reader_activity" })).toBe("No reader events yet.");
    expect(await dispatch({ kind: "get_reader_error" })).toContain("companion");
    socket.readyState = 1;
    expect((await dispatch(event, tab)).accepted).toBe(true);
    expect(await dispatch({ kind: "get_reader_activity" })).toBe("Last reader event: problem_opened");
    expect(socket.sent.map(item => item.type)).toEqual(["problem_opened"]);
  });

  it("keeps reporting focus after the session tab navigates within NeetCode", async () => {
    await start(); pushSession("watching"); await dispatch({ kind: "get_mode" });
    socket.readyState = 1;
    updated.at(-1)?.(7, { url: "https://neetcode.io/practice" });
    await vi.waitFor(() => expect(chrome.sidePanel.setOptions).toHaveBeenCalledWith({ tabId: 7, path: "panel.html", enabled: true }));
    focused.at(-1)?.(-1);
    await vi.waitFor(() => expect(socket.sent.at(-1)).toMatchObject({ type: "focus_changed", payload: { tab_active: true, window_focused: false } }));
  });

  it("accepts the new problem after tab navigation and the sender URL lags", async () => {
    await start(); pushSession("watching"); await dispatch({ kind: "get_mode" });
    socket.readyState = 1;
    const first = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    await dispatch({ kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } }, first);
    updated.at(-1)?.(7, { url: "https://neetcode.io/practice/" }, { ...first, url: "https://neetcode.io/practice/" });
    vi.mocked(chrome.tabs.get).mockResolvedValue({ ...first, url: "https://neetcode.io/problems/two-sum/" } as chrome.tabs.Tab);
    const next = { kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "two-sum", title: "Two Sum" } } };
    expect(await dispatch(next, { ...first, url: "https://neetcode.io/practice/" })).toEqual({ accepted: true, mode: "watching" });
    expect(socket.sent.at(-1)).toMatchObject({ type: "problem_opened", payload: { slug: "two-sum", title: "Two Sum" } });
  });

  it("forwards the contents-page self-check while the bound tab remains on NeetCode", async () => {
    await start(); pushSession("watching"); await dispatch({ kind: "get_mode" });
    socket.readyState = 1;
    updated.at(-1)?.(7, { url: "https://neetcode.io/practice/" }, { id: 7, url: "https://neetcode.io/practice/" });
    vi.mocked(chrome.tabs.get).mockResolvedValue({ id: 7, url: "https://neetcode.io/practice/" } as chrome.tabs.Tab);
    const message = "Open a problem and I'll pick the timer back up.";
    expect(await dispatch({ kind: "reader_event", event: { type: "reader_check", payload: { ok: false, message } } }, { id: 7, url: "https://neetcode.io/practice/" })).toEqual({ accepted: true, mode: "watching" });
    expect(socket.sent.at(-1)).toMatchObject({ type: "reader_check", payload: { ok: false, message } });
  });

  it("sends a focus change that was dropped while disconnected once it reconnects", async () => {
    await start(); pushSession("watching"); await dispatch({ kind: "get_mode" });
    socket.readyState = 1;
    socket.readyState = 0;
    focused.at(-1)?.(-1);
    await vi.waitFor(() => expect(chrome.storage.session.set).toHaveBeenCalled());
    const before = socket.sent.length;
    socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "status", ts: "now", state: "connected" }) });
    await vi.waitFor(() => expect(socket.sent.slice(before).map(item => item.type)).toContain("focus_changed"));
    expect(socket.sent.at(-1).payload).toEqual({ tab_active: true, window_focused: false });
  });

  it("reports focus only for the bound tab", async () => {
    await start(); socket.readyState = 1; pushSession("watching"); await dispatch({ kind: "get_mode" });
    await settleFocus();
    activated.at(-1)?.({ tabId: 8, windowId: 3 });
    await vi.waitFor(() => expect(socket.sent).toHaveLength(1));
    expect(socket.sent.at(-1).payload).toEqual({ tab_active: false, window_focused: true });
    activated.at(-1)?.({ tabId: 8, windowId: 3 });
    await dispatch({ kind: "get_mode" });
    expect(socket.sent).toHaveLength(1);
    activated.at(-1)?.({ tabId: 7, windowId: 3 });
    await vi.waitFor(() => expect(socket.sent).toHaveLength(2));
    expect(socket.sent.at(-1).payload).toEqual({ tab_active: true, window_focused: true });
    activated.at(-1)?.({ tabId: 9, windowId: 4 });
    await dispatch({ kind: "get_mode" });
    expect(socket.sent).toHaveLength(2);
  });

  describe("a focus pause while the session tab navigates to another problem", () => {
    const paused = { type: "session_state", ts: "now", state: "paused", mode: "paused", paused_reason: "focus", check_message: null, cap_minutes: null, cap_seconds_left: null, time_up: null, active_seconds: 0, running: false, problem: null, accepted: false };
    async function leaveAndReturn() {
      socket.readyState = 1;
      socket.onmessage?.({ data: JSON.stringify(paused) });
      activated.at(-1)?.({ tabId: 8, windowId: 3 });
      await vi.waitFor(() => expect(socket.sent.at(-1)).toMatchObject({ type: "focus_changed", payload: { tab_active: false, window_focused: true } }));
      updated.at(-1)?.(7, { url: "https://neetcode.io/problems/two-sum/" }, { id: 7, url: "https://neetcode.io/problems/two-sum/" });
      await dispatch({ kind: "get_mode" });
    }
    it("reports focus again when the user comes back, so the pause can lift", async () => {
      await start(); socket.readyState = 1;
      await leaveAndReturn();
      activated.at(-1)?.({ tabId: 7, windowId: 3 });
      await vi.waitFor(() => expect(socket.sent.at(-1)).toMatchObject({ type: "focus_changed", payload: { tab_active: true, window_focused: true } }));
    });
    it("does the same after the worker restarts", async () => {
      await start(); socket.readyState = 1;
      await leaveAndReturn();
      vi.mocked(chrome.tabs.get).mockResolvedValue({ id: 7, windowId: 3, active: false, url: "https://neetcode.io/problems/two-sum/" } as chrome.tabs.Tab);
      await start(); socket.readyState = 1;
      socket.onmessage?.({ data: JSON.stringify(paused) });
      await vi.waitFor(() => expect(socket.sent.at(-1)).toMatchObject({ type: "focus_changed", payload: { tab_active: false, window_focused: true } }));
      activated.at(-1)?.({ tabId: 7, windowId: 3 });
      await vi.waitFor(() => expect(socket.sent.at(-1)).toMatchObject({ type: "focus_changed", payload: { tab_active: true, window_focused: true } }));
    });
  });

  it("pauses the session when the session tab navigates away from NeetCode, but not within NeetCode", async () => {
    await start(); pushSession("watching"); await dispatch({ kind: "get_mode" });
    socket.readyState = 1;
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    await dispatch({ kind: "session_command", command: "start", tabId: 7 });
    await dispatch({ kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } }, tab);
    const before = socket.sent.length;
    updated.at(-1)?.(7, { url: "https://neetcode.io/problems/two-integer-sum/" }, { id: 7, url: "https://neetcode.io/problems/two-integer-sum/" });
    await dispatch({ kind: "get_mode" });
    expect(socket.sent.slice(before).map(item => item.type)).not.toContain("tab_closed");
    updated.at(-1)?.(7, { url: "https://example.com/" }, { id: 7, url: "https://example.com/" });
    await vi.waitFor(() => expect(socket.sent.map(item => item.type)).toContain("tab_closed"));
  });
describe("Planner, recap, cap and reference relay", () => {
  it("asks for a suggestion and the reference review on connection", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "status", ts: "now", state: "connected" }) });
    expect(socket.sent.map(item => item.type)).toEqual(expect.arrayContaining(["planner_get", "reference_get"]));
  });
  it("forwards the suggestion and recap states, including the companion's ts-less recap", async () => {
    await start(); socket.readyState = 1;
    const suggestion = { type: "suggestion", ts: "now", slug: "house-robber", title: "House Robber", difficulty: "Medium", cap_minutes: 35, steer: null, message: null, source: "fallback" };
    socket.onmessage?.({ data: JSON.stringify(suggestion) });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "suggestion", message: suggestion });
    expect(await dispatch({ kind: "get_suggestion" })).toEqual(suggestion);
    const recap = { type: "recap_state", session_id: 12, status: "ready", bullets: ["Dropped: visited marking."] };
    socket.onmessage?.({ data: JSON.stringify(recap) });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "recap_state", message: recap });
    expect(await dispatch({ kind: "get_recap_state" })).toEqual(recap);
    const noted = { type: "recap_noted", session_id: 12, note_id: 9, message: "Noted." };
    socket.onmessage?.({ data: JSON.stringify(noted) });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "recap_noted", message: noted });
  });
  it("navigates the session tab when a suggestion is opened and clears the card", async () => {
    await start(); socket.readyState = 1;
    expect(await dispatch({ kind: "planner_go", tabId: 7 })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "planner_go" });
    expect(socket.sent.at(-1)).not.toHaveProperty("payload");
    socket.onmessage?.({ data: JSON.stringify({ type: "planner_open", ts: "now", slug: "house-robber", title: "House Robber" }) });
    await vi.waitFor(() => expect(chrome.tabs.update).toHaveBeenCalledWith(7, { url: "https://neetcode.io/problems/house-robber/" }));
    expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({ kind: "planner_open", message: { type: "planner_open", ts: "now", slug: "house-robber", title: "House Robber" } });
  });
  it("forwards Planner chat, recap feedback and reference actions", async () => {
    await start(); socket.readyState = 1;
    expect(await dispatch({ kind: "planner_chat", text: "more trees" })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "planner_chat", payload: { text: "more trees" } });
    expect(await dispatch({ kind: "recap_get", session_id: 12 })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "recap_get", payload: { session_id: 12 } });
    expect(await dispatch({ kind: "recap_feedback", session_id: 12, text: "That drop was wrong" })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "recap_feedback", payload: { session_id: 12, text: "That drop was wrong" } });
    expect(await dispatch({ kind: "reference_flag", message_id: 4, line: "This misses duplicates" })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "reference_flag", payload: { message_id: 4, line: "This misses duplicates" } });
    expect(await dispatch({ kind: "reference_apply", slug: "top-k-elements-in-list", reply: '{"verdict":"reference is fine"}' })).toEqual({ sent: true });
    expect(socket.sent.at(-1)).toMatchObject({ type: "reference_apply", payload: { slug: "top-k-elements-in-list" } });
    expect(await dispatch({ kind: "reference_flag", message_id: 4, line: "two\nlines" })).toEqual({ error: "Invalid flag." });
  });
  it("renders planner and reference errors loudly through the panel", async () => {
    await start(); socket.readyState = 1;
    socket.onmessage?.({ data: JSON.stringify({ type: "error", ts: "now", message: "The Planner failed and no eligible problems are available to suggest." }) });
    expect(await dispatch({ kind: "get_status" })).toEqual({ kind: "error", message: "The Planner failed and no eligible problems are available to suggest." });
  });
});

});

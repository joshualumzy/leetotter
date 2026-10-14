import { CompanionConnection, type ConnectionState } from "./connection";
import { readerTypes, validateReaderPayload, validateImportUrls, type ReaderMessageType } from "./protocol";

declare const __LEETOTTER_PORT__: number;
type Mode = "idle" | "watching" | "paused";
type ProblemTab = { windowId: number; active: boolean; lastFocus?: string };
type Saved = { mode: Mode; pausedByCheck: boolean; readerError: string; readerActivity: string; focusedWindow: number; problemTabs: [number, ProblemTab][] };
let state: ConnectionState = { kind: "offline" };
let mode: Mode = "idle";
let readerError = "";
let readerActivity = "No reader events yet.";
let imported = false;
let importResult: { already_imported: boolean; matched: number; unmatched: string[] } | undefined;
let importPending = false;
let pausedByCheck = false;
let focusedWindow: number = chrome.windows.WINDOW_ID_NONE;
const problemTabs = new Map<number, ProblemTab>();
function panel(message: unknown): void { chrome.runtime.sendMessage(message).catch(() => {}); }
let savedInOrder: Promise<void> = Promise.resolve();
function save(): Promise<void> {
  const readerState: Saved = { mode, pausedByCheck, readerError, readerActivity, focusedWindow, problemTabs: [...problemTabs] };
  savedInOrder = savedInOrder.then(() => chrome.storage.session.set({ readerState }));
  return savedInOrder;
}
function problemUrl(url?: string): boolean { return Boolean(url && /^https:\/\/neetcode\.io\/problems\/[^/]+/.test(url)); }
const ready = (async () => {
  const saved = (await chrome.storage.session.get("readerState")).readerState as Saved | undefined;
  if (saved) {
    mode = saved.mode; pausedByCheck = saved.pausedByCheck; readerError = saved.readerError;
    readerActivity = saved.readerActivity; focusedWindow = saved.focusedWindow;
    for (const [id, tab] of saved.problemTabs) problemTabs.set(id, tab);
  } else {
    const window = await chrome.windows.getLastFocused();
    focusedWindow = window.focused ? window.id! : chrome.windows.WINDOW_ID_NONE;
  }
  const tabs = await chrome.tabs.query({ url: "https://neetcode.io/problems/*" });
  const live = new Set(tabs.map(tab => tab.id));
  for (const id of problemTabs.keys()) if (!live.has(id)) problemTabs.delete(id);
  await Promise.all(tabs.map(tab => tab.id === undefined ? Promise.resolve() : chrome.tabs.sendMessage(tab.id, { kind: "watch_mode", mode }).catch(() => {})));
  await save();
})();
const connection = new CompanionConnection(
  "ws://127.0.0.1:" + __LEETOTTER_PORT__,
  url => new WebSocket(url),
  next => {
    state = next; panel({ kind: "transport", state: next });
    if (next.kind === "connected") connection.send({ type: "get_import_status", ts: new Date().toISOString() });
    if (next.kind === "error" && importPending) { importPending = false; panel({ kind: "import_error", message: next.message }); }
  },
  message => {
    if (message.type === "import_status") imported = message.imported;
    if (message.type === "import_result") { importResult = message.result; imported = true; importPending = false; }
    panel({ kind: "import_state", imported, result: importResult });
  },
);
function send(type: ReaderMessageType, payload: Record<string, unknown>): boolean {
  const delivered = connection.send({ type, ts: new Date().toISOString(), payload });
  if (delivered) {
    readerActivity = "Last reader event: " + type;
    panel({ kind: "reader_activity", message: readerActivity });
    if (readerError === "Reader events are not reaching the companion.") { readerError = ""; panel({ kind: "reader_error", message: "" }); }
  } else {
    readerError = "Reader events are not reaching the companion.";
    panel({ kind: "reader_error", message: readerError });
  }
  return delivered;
}
function sendFocus(id: number): void {
  if (mode === "idle") return;
  const tab = problemTabs.get(id);
  if (!tab) return;
  const payload = { tab_active: tab.active, window_focused: tab.windowId === focusedWindow };
  const key = JSON.stringify(payload);
  if (key === tab.lastFocus) return;
  if (send("focus_changed", payload)) tab.lastFocus = key;
}
async function setMode(next: Mode): Promise<void> {
  mode = next; await save(); panel({ kind: "watch_mode", mode });
  const tabs = await chrome.tabs.query({ url: "https://neetcode.io/problems/*" });
  await Promise.all(tabs.map(tab => tab.id === undefined ? Promise.resolve() : chrome.tabs.sendMessage(tab.id, { kind: "watch_mode", mode }).catch(() => {})));
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  void (async () => {
    await ready;
    if (message?.kind === "get_status") return state;
    if (message?.kind === "get_mode") return mode;
    if (message?.kind === "get_reader_error") return readerError;
    if (message?.kind === "get_reader_activity") return readerActivity;
    if (message?.kind === "get_import_state") return { imported, result: importResult };
    if (message?.kind === "import_history") {
      if (imported) return { error: "NeetCode history was already imported." };
      if (importPending) return { error: "History import is already in progress." };
      if (state.kind !== "connected") return { error: "Tutor service isn't connected." };
      const tabs = await chrome.tabs.query({ url: "https://neetcode.io/*" });
      const tab = tabs.find(item => item.id !== undefined);
      if (!tab?.id) return { error: "Open a neetcode.io tab to import history." };
      try {
        const reply = await chrome.tabs.sendMessage(tab.id, { kind: "read_history" });
        if (reply?.error) return { error: String(reply.error) };
        const urls = validateImportUrls(reply?.urls);
        if (!connection.send({ type: "import_history", ts: new Date().toISOString(), payload: { urls } })) return { error: "Tutor service isn't connected." };
        importPending = true;
        return { pending: true };
      } catch (error) {
        return { error: `Can't read NeetCode history: ${(error as Error).message}` };
      }
    }
    if (message?.kind === "set_mode" && ["idle", "watching", "paused"].includes(message.mode)) {
      pausedByCheck = false; await setMode(message.mode); return { mode };
    }
    if (message?.kind !== "reader_event") return undefined;
    const event = message.event, tab = sender.tab;
    if (tab?.id === undefined || !problemUrl(tab.url) || !event || typeof event.type !== "string") return { accepted: false, mode };
    if (event.type === "reader_check") {
      if (mode === "idle") return { accepted: false, mode };
      if (event.payload?.ok === false) {
        readerError = String(event.payload.message || "Reader check failed");
        panel({ kind: "reader_error", message: readerError });
        if (mode === "watching") { pausedByCheck = true; await setMode("paused"); }
      } else if (event.payload?.ok === true) {
        readerError = ""; panel({ kind: "reader_error", message: "" });
        if (pausedByCheck) { pausedByCheck = false; await setMode("watching"); }
      }
      await save(); return { accepted: true, mode };
    }
    if (!readerTypes.has(event.type as ReaderMessageType)) {
      readerError = "Unknown reader event: " + event.type;
      panel({ kind: "reader_error", message: readerError });
      await save(); return { accepted: false, mode };
    }
    if (mode === "idle" || (mode === "paused" && !["focus_changed", "submit_result"].includes(event.type))) return { accepted: false, mode };
    try {
      if (event.type === "focus_changed") return { accepted: false, mode };
      const payload = validateReaderPayload(event.type as ReaderMessageType, event.payload);
      const delivered = send(event.type as ReaderMessageType, payload);
      if (delivered && event.type === "problem_opened") {
        problemTabs.set(tab.id, { windowId: tab.windowId, active: Boolean(tab.active) });
        sendFocus(tab.id);
      }
      await save(); return { accepted: delivered, mode };
    } catch (error) {
      readerError = (error as Error).message;
      panel({ kind: "reader_error", message: readerError });
      await save(); return { accepted: false, mode };
    }
  })().then(respond, error => { readerError = String(error); panel({ kind: "reader_error", message: readerError }); respond({ accepted: false, mode }); });
  return true;
});
chrome.tabs.onActivated.addListener(info => {
  void ready.then(async () => {
    for (const [id, tab] of problemTabs) if (tab.windowId === info.windowId) { tab.active = id === info.tabId; sendFocus(id); }
    await save();
  });
});
chrome.tabs.onUpdated.addListener((id, change) => {
  if (change.url) void ready.then(async () => { problemTabs.delete(id); await save(); });
});
chrome.tabs.onRemoved.addListener(id => { void ready.then(async () => { problemTabs.delete(id); await save(); }); });
chrome.windows.onFocusChanged.addListener(id => {
  void ready.then(async () => { focusedWindow = id; for (const tabId of problemTabs.keys()) sendFocus(tabId); await save(); });
});
connection.start();

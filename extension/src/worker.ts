import { CompanionConnection, type ConnectionState } from "./connection";
import { readerTypes, validateReaderPayload, validateImportUrls, type ReaderMessageType, type SessionState, type SettingsState } from "./protocol";
import { reactionForReaderEvent } from "./reader-reaction";

declare const __LEETOTTER_PORT__: number;
type Mode = "idle" | "watching" | "paused";
type ProblemTab = { windowId: number; active: boolean; lastFocus?: string };
type Saved = { readerError: string; readerActivity: string; focusedWindow: number; problemTabs: [number, ProblemTab][]; sessionTabId?: number | null };
let state: ConnectionState = { kind: "offline" };
let mode: Mode = "idle";
let readerError = "";
let notice = "";
let readerActivity = "No reader events yet.";
let imported = false;
let importResult: { already_imported: boolean; matched: number; unmatched: string[] } | undefined;
let importPending = false;
let sessionState: SessionState | undefined;
let settingsState: SettingsState | undefined;
let sessionTabId: number | null = null;
let orphanReported = false;
let disconnectResumeSent = false;
let focusedWindow: number = chrome.windows.WINDOW_ID_NONE;
const problemTabs = new Map<number, ProblemTab>();
const failedModeTabs = new Set<number>();
const missingReaderMessage = "Leetotter can't see this NeetCode page. Reload the tab.";
function panel(message: unknown): void { chrome.runtime.sendMessage(message).catch(() => {}); }
function showReaderAlert(): void { panel({ kind: "reader_error", message: readerError || notice }); }
let savedInOrder: Promise<void> = Promise.resolve();
function save(): Promise<void> {
  const readerState: Saved = { readerError, readerActivity, focusedWindow, problemTabs: [...problemTabs], sessionTabId };
  savedInOrder = savedInOrder.then(() => chrome.storage.session.set({ readerState }));
  return savedInOrder;
}
function problemUrl(url?: string): boolean { return Boolean(url && /^https:\/\/neetcode\.io\/problems\/[^/]+/.test(url)); }
function neetcodeUrl(url?: string): boolean { return Boolean(url && /^https:\/\/neetcode\.io(?:\/|$)/.test(url)); }
async function tabExists(id: number | null): Promise<boolean> {
  if (id === null) return false;
  try { await chrome.tabs.get(id); return true; } catch { return false; }
}
async function bindTab(id: number | null): Promise<void> {
  if (sessionTabId === id) return;
  sessionTabId = id;
  panel({ kind: "session_binding", tabId: id });
  await save();
  await setMode(mode);
}
function setPanelForTab(tab: chrome.tabs.Tab): Promise<void> {
  if (tab.id === undefined) return Promise.resolve();
  return chrome.sidePanel.setOptions(neetcodeUrl(tab.url)
    ? { tabId: tab.id, path: "panel.html", enabled: true }
    : { tabId: tab.id, enabled: false });
}
async function configureSidePanel(): Promise<chrome.tabs.Tab[]> {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  await chrome.sidePanel.setOptions({ enabled: false });
  const tabs = await chrome.tabs.query({});
  await Promise.all(tabs.map(setPanelForTab));
  return tabs;
}
const ready = (async () => {
  const saved = (await chrome.storage.session.get("readerState")).readerState as Saved | undefined;
  if (saved) {
    readerError = saved.readerError;
    readerActivity = saved.readerActivity; focusedWindow = saved.focusedWindow;
    for (const [id, tab] of saved.problemTabs) problemTabs.set(id, tab);
    sessionTabId = saved.sessionTabId ?? null;
  } else {
    const window = await chrome.windows.getLastFocused();
    focusedWindow = window.focused ? window.id! : chrome.windows.WINDOW_ID_NONE;
  }
  const tabs = (await configureSidePanel()).filter(tab => problemUrl(tab.url));
  const live = new Set(tabs.map(tab => tab.id));
  for (const id of problemTabs.keys()) if (!live.has(id)) problemTabs.delete(id);
  await Promise.all(tabs.map(tab => tab.id === undefined ? Promise.resolve() : chrome.tabs.sendMessage(tab.id, { kind: "watch_mode", mode: tab.id === sessionTabId ? mode : "idle" }).catch(() => {})));
  await save();
})();
const connection = new CompanionConnection(
  "ws://127.0.0.1:" + __LEETOTTER_PORT__,
  url => new WebSocket(url),
  next => {
    state = next; panel({ kind: "transport", state: next });
    if (next.kind === "connected") {
      connection.send({ type: "get_import_status", ts: new Date().toISOString() });
      connection.send({ type: "session_get_state", ts: new Date().toISOString() });
      connection.send({ type: "settings_get", ts: new Date().toISOString() });
      resyncFocus();
    }
    if (next.kind === "error" && importPending) { importPending = false; panel({ kind: "import_error", message: next.message }); }
  },
  message => {
    if (message.type === "settings_state") { settingsState = message; panel({ kind: "settings_state", settings: message }); return; }
    if (message.type === "session_state") {
      if (notice && message.mode === "watching" && (sessionState?.state === "idle" || sessionState?.state === "ended")) { notice = ""; showReaderAlert(); }
      sessionState = message;
      if (message.state !== "paused" || message.paused_reason !== "disconnect") disconnectResumeSent = false;
      if (message.state !== "active") orphanReported = false;
      void (async () => {
        if (sessionState !== message) return;
        if ((message.state === "idle" || message.state === "ended") && sessionTabId !== null) await bindTab(null);
        const boundExists = await tabExists(sessionTabId);
        if (sessionState !== message) return;
        if (message.state === "active" && !boundExists && !orphanReported) {
          orphanReported = connection.send({ type: "tab_closed", ts: new Date().toISOString() });
        }
        if (message.state === "paused" && message.paused_reason === "disconnect" && boundExists && !disconnectResumeSent) {
          disconnectResumeSent = connection.send({ type: "session_resume", ts: new Date().toISOString() });
        }
        await setMode(message.mode);
      })();
      panel({ kind: "session_state", state: message });
      return;
    }
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
  if (mode === "idle" || id !== sessionTabId) return;
  const tab = problemTabs.get(id);
  if (!tab) return;
  const payload = { tab_active: tab.active, window_focused: tab.windowId === focusedWindow };
  const key = JSON.stringify(payload);
  if (key === tab.lastFocus) return;
  if (send("focus_changed", payload)) tab.lastFocus = key;
}
// A focus change that could not be delivered, or happened while the session was not watching, is sent again.
function resyncFocus(): void {
  for (const [id, tab] of problemTabs) { tab.lastFocus = undefined; sendFocus(id); }
}
async function setMode(next: Mode): Promise<void> {
  const changed = next !== mode;
  mode = next; await save();
  if (mode === "idle") failedModeTabs.clear();
  if (changed) resyncFocus();
  const tabs = await chrome.tabs.query({ url: "https://neetcode.io/problems/*" });
  await Promise.all(tabs.map(async tab => {
    if (tab.id === undefined) return;
    try {
      await chrome.tabs.sendMessage(tab.id, { kind: "watch_mode", mode: tab.id === sessionTabId ? mode : "idle" });
    } catch {
      if (mode !== "watching" || tab.id !== sessionTabId || !problemUrl(tab.url) || failedModeTabs.has(tab.id)) return;
      failedModeTabs.add(tab.id);
      send("reader_check", { ok: false, message: missingReaderMessage });
      readerError = missingReaderMessage;
      showReaderAlert();
      await save();
    }
  }));
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  void (async () => {
    await ready;
    if (message?.kind === "get_status") return state;
    if (message?.kind === "get_mode") return sender.tab?.id === undefined || sender.tab.id === sessionTabId ? mode : "idle";
    if (message?.kind === "get_reader_error") return readerError || notice;
    if (message?.kind === "get_reader_activity") return readerActivity;
    if (message?.kind === "get_import_state") return { imported, result: importResult };
    if (message?.kind === "get_session_state") return sessionState;
    if (message?.kind === "get_session_binding") return { tabId: sessionTabId };
    if (message?.kind === "get_settings_state") return settingsState;
    if (message?.kind === "settings_get") return connection.send({ type: "settings_get", ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
    if (message?.kind === "skip_name") {
      if (settingsState?.name !== null) return { error: "Invalid setting." };
      return connection.send({ type: "settings_set", ts: new Date().toISOString(), payload: { key: "name", value: "" } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "settings_set") {
      const { key, value } = message;
      if (typeof value !== "string" || (key !== "name" && key !== "theme") ||
          (key === "name" && (!value.trim() || value.trim().length > 40)) ||
          (key === "theme" && !["match", "light", "dark"].includes(value))) return { error: "Invalid setting." };
      return connection.send({ type: "settings_set", ts: new Date().toISOString(), payload: { key, value } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "session_command") {
      const command = message.command;
      const types = { start: "session_start", pause: "session_pause", resume: "session_resume", end: "session_end", overrun_finish: "session_overrun", overrun_stop: "session_overrun" } as const;
      if (!(command in types)) return { error: "Unknown session command." };
      if (command === "start" && (!Number.isInteger(message.budget_minutes) || message.budget_minutes < 1 || message.budget_minutes > 1440)) return { error: "Invalid session budget." };
      if (command === "start" || command === "resume") {
        if (sessionState && ["active", "paused"].includes(sessionState.state) && sessionTabId !== message.tabId && await tabExists(sessionTabId)) return { error: "A session is running in another tab." };
        if (!Number.isInteger(message.tabId) || !await tabExists(message.tabId) || !neetcodeUrl((await chrome.tabs.get(message.tabId)).url)) return { error: "Open a NeetCode tab to start or resume." };
      }
      const type = types[command as keyof typeof types];
      const payload = command === "start" ? { budget_minutes: message.budget_minutes } : command === "overrun_finish" ? { choice: "finish" } : command === "overrun_stop" ? { choice: "stop" } : undefined;
      if (!connection.send({ type, ts: new Date().toISOString(), ...(payload ? { payload } : {}) })) return { error: "Tutor service isn't connected." };
      if (command === "start" || command === "resume") { orphanReported = false; disconnectResumeSent = false; await bindTab(message.tabId); }
      if (command === "end") await bindTab(null);
      return { sent: true };
    }
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
    if (message?.kind !== "reader_event") return undefined;
    const event = message.event, tab = sender.tab;
    if (tab?.id === undefined || !problemUrl(tab.url) || !event || typeof event.type !== "string") return { accepted: false, mode };
    if (tab.id !== sessionTabId) return { accepted: false, mode };
    if (event.type === "reader_notice") {
      if (mode === "idle" || typeof event.payload?.message !== "string" || !event.payload.message) return { accepted: false, mode };
      notice = event.payload.message;
      showReaderAlert();
      return { accepted: true, mode };
    }
    if (event.type === "reader_check") {
      if (mode === "idle") return { accepted: false, mode };
      try {
        const payload = validateReaderPayload("reader_check", event.payload);
        if (payload.ok === false) {
        readerError = String(payload.message || "Reader check failed");
        showReaderAlert();
        } else {
        failedModeTabs.delete(tab.id);
        readerError = ""; showReaderAlert();
        }
        const delivered = send("reader_check", payload);
        await save(); return { accepted: delivered, mode };
      } catch (error) { readerError = (error as Error).message; panel({ kind: "reader_error", message: readerError }); await save(); return { accepted: false, mode }; }
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
      if (notice && (event.type === "run_result" || event.type === "submit_result")) { notice = ""; showReaderAlert(); }
      if (delivered && mode === "watching") {
        const reaction = reactionForReaderEvent(event.type as ReaderMessageType, payload);
        if (reaction) panel({ kind: "reader_reaction", reaction });
      }
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
    const tab = sessionTabId === null ? undefined : problemTabs.get(sessionTabId);
    if (tab && tab.windowId === info.windowId) { tab.active = info.tabId === sessionTabId; sendFocus(sessionTabId!); }
    await save();
    await setPanelForTab(await chrome.tabs.get(info.tabId));
  });
});
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (change.url) void ready.then(async () => {
    problemTabs.delete(id); failedModeTabs.delete(id);
    if (id === sessionTabId && !change.url!.startsWith("https://neetcode.io/")) {
      // The session's tab left NeetCode: nothing is watching any more, so it pauses like a closed tab.
      if (!orphanReported) orphanReported = connection.send({ type: "tab_closed", ts: new Date().toISOString() });
      await bindTab(null);
    } else await save();
    await setPanelForTab({ ...tab, id, url: change.url });
  });
});
chrome.tabs.onCreated.addListener(tab => { void ready.then(() => setPanelForTab(tab)); });
chrome.tabs.onRemoved.addListener(id => { void ready.then(async () => {
  problemTabs.delete(id); failedModeTabs.delete(id);
  if (id === sessionTabId) {
    if (!orphanReported) orphanReported = connection.send({ type: "tab_closed", ts: new Date().toISOString() });
    await bindTab(null);
  } else await save();
}); });
chrome.runtime.onInstalled.addListener(() => { void ready.then(() => configureSidePanel()); });
chrome.windows.onFocusChanged.addListener(id => {
  void ready.then(async () => { focusedWindow = id; for (const tabId of problemTabs.keys()) sendFocus(tabId); await save(); });
});
connection.start();

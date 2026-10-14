import { CompanionConnection, type ConnectionState } from "./connection";
import { readerTypes, validateReaderPayload, type ReaderMessageType } from "./protocol";

declare const __LEETOTTER_PORT__: number;

let state: ConnectionState = { kind: "offline" };
let mode: "idle" | "watching" | "paused" = "idle";
let readerError = "";
let readerActivity = "No reader events yet.";
let pausedByCheck = false;
const problemTabs = new Map<number, { windowId: number; active: boolean }>();
let focusedWindow: number = chrome.windows.WINDOW_ID_NONE;
chrome.windows.getLastFocused().then(window => { focusedWindow = window.focused ? window.id! : chrome.windows.WINDOW_ID_NONE; });
function panel(message: unknown): void { chrome.runtime.sendMessage(message).catch(() => {}); }
function sendFocus(tabId: number): void {
  if (mode === "idle") return;
  const tab = problemTabs.get(tabId);
  if (!tab) return;
  connection.send({ type: "focus_changed", ts: new Date().toISOString(), payload: { tab_active: tab.active, window_focused: tab.windowId === focusedWindow } });
}
function setMode(next: typeof mode): void {
  mode = next;
  panel({ kind: "watch_mode", mode });
  chrome.tabs.query({ url: "https://neetcode.io/*" }).then(tabs => {
    for (const tab of tabs) if (tab.id !== undefined) chrome.tabs.sendMessage(tab.id, { kind: "watch_mode", mode }).catch(() => {});
  });
}
const connection = new CompanionConnection(
  `ws://127.0.0.1:${__LEETOTTER_PORT__}`,
  (url) => new WebSocket(url),
  (next) => {
    state = next;
    panel({ kind: "transport", state: next });
  },
);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.kind === "get_status") sendResponse(state);
  if (message?.kind === "get_mode") sendResponse(mode);
  if (message?.kind === "get_reader_error") sendResponse(readerError);
  if (message?.kind === "get_reader_activity") sendResponse(readerActivity);
  if (message?.kind === "set_mode" && ["idle", "watching", "paused"].includes(message.mode)) { pausedByCheck = false; setMode(message.mode); }
  if (message?.kind === "reader_event") {
    const event = message.event;
    if (_sender.tab?.id === undefined || !event || typeof event.type !== "string") return;
    if (event.type === "reader_check") {
      if (event.payload?.ok === false) {
        if (mode === "watching") { pausedByCheck = true; setMode("paused"); }
        readerError = String(event.payload.message || "Reader check failed");
        panel({ kind: "reader_error", message: readerError });
      } else if (event.payload?.ok === true) {
        readerError = "";
        panel({ kind: "reader_error", message: "" });
        if (pausedByCheck) { pausedByCheck = false; setMode("watching"); }
      }
      return;
    }
    if (!readerTypes.has(event.type as ReaderMessageType)) { panel({ kind: "reader_error", message: `Unknown reader event: ${event.type}` }); return; }
    if (mode === "idle" || (mode === "paused" && !["focus_changed", "submit_result"].includes(event.type))) return;
    try {
      let payload = event.payload as Record<string, unknown>;
      if (event.type === "focus_changed") {
        const tab = _sender.tab;
        problemTabs.set(tab.id!, { windowId: tab.windowId, active: Boolean(payload.tab_active) });
        payload = { ...payload, window_focused: tab.windowId === focusedWindow };
      }
      payload = validateReaderPayload(event.type as ReaderMessageType, payload);
      connection.send({ type: event.type, ts: new Date().toISOString(), payload });
      readerActivity = `Last reader event: ${event.type}`;
      panel({ kind: "reader_activity", message: readerActivity });
    } catch (error) { panel({ kind: "reader_error", message: (error as Error).message }); }
  }
});

chrome.tabs.onActivated.addListener(info => {
  for (const [id, tab] of problemTabs) if (tab.windowId === info.windowId) { tab.active = id === info.tabId; sendFocus(id); }
});
chrome.tabs.onRemoved.addListener(id => problemTabs.delete(id));
chrome.windows.onFocusChanged.addListener(id => {
  focusedWindow = id;
  for (const tabId of problemTabs.keys()) sendFocus(tabId);
});

connection.start();

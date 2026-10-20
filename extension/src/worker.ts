import { CompanionConnection, type ConnectionState } from "./connection";
import { MISSING_READER_MESSAGE, readerTypes, validateReaderPayload, validateImportUrls, type ProgressCategoryState, type ProgressState, type ReaderMessageType, type RecapState, type RecapsState, type ReferenceState, type SessionState, type SettingsState, type Suggestion } from "./protocol";
import { reactionForReaderEvent } from "./reader-reaction";

declare const __LEETOTTER_PORT__: number;
type Mode = "idle" | "watching" | "paused";
// Where the session's bound tab is, tracked from the tab itself: a navigation inside NeetCode or a pause must not lose it.
type BoundFocus = { windowId: number; active: boolean; lastFocus?: string };
type Saved = { readerError: string; readerActivity: string; focusedWindow: number; sessionTabId?: number | null; boundFocus?: BoundFocus };
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
let suggestion: Suggestion | undefined;
let recapState: RecapState | undefined;
let recapsState: RecapsState | undefined;
let progressState: ProgressState | undefined;
let progressCategoryState: ProgressCategoryState | undefined;
let referenceState: ReferenceState | undefined;
let plannerTabId: number | null = null;
let sessionTabId: number | null = null;
let orphanReported = false;
let disconnectResumeSent = false;
let focusedWindow: number = chrome.windows.WINDOW_ID_NONE;
let boundFocus: BoundFocus | undefined;
const failedModeTabs = new Set<number>();
function panel(message: unknown): void { chrome.runtime.sendMessage(message).catch(() => {}); }
function showReaderAlert(): void { panel({ kind: "reader_error", message: readerError || notice }); }
let savedInOrder: Promise<void> = Promise.resolve();
function save(): Promise<void> {
  const readerState: Saved = { readerError, readerActivity, focusedWindow, sessionTabId, boundFocus };
  savedInOrder = savedInOrder
    .then(() => chrome.storage.session.set({ readerState }))
    .catch(error => { console.error("Leetotter could not save the session state:", error); });
  return savedInOrder;
}
function problemUrl(url?: string): boolean { return Boolean(url && /^https:\/\/neetcode\.io\/problems\/[^/]+/.test(url)); }
function neetcodeUrl(url?: string): boolean { return Boolean(url && /^https:\/\/neetcode\.io(?:\/|$)/.test(url)); }
async function tabExists(id: number | null): Promise<boolean> {
  if (id === null) return false;
  try { await chrome.tabs.get(id); return true; } catch { return false; }
}
async function trackBoundTab(): Promise<void> {
  if (sessionTabId === null) return;
  try {
    const tab = await chrome.tabs.get(sessionTabId);
    boundFocus = { windowId: tab.windowId, active: tab.active };
  } catch { /* The tab is gone; its removal unbinds the session. */ }
}
async function bindTab(id: number | null): Promise<void> {
  if (sessionTabId === id) return;
  sessionTabId = id;
  boundFocus = undefined;
  await trackBoundTab();
  panel({ kind: "session_binding", tabId: id });
  await save();
  await setMode(mode);
}
function setPanelForTab(tab: chrome.tabs.Tab): Promise<void> {
  if (tab.id === undefined) return Promise.resolve();
  return chrome.sidePanel.setOptions(neetcodeUrl(tab.url)
    ? { tabId: tab.id, path: "panel.html", enabled: true }
    : { tabId: tab.id, enabled: false })
    .catch(error => { console.error("Leetotter could not set the panel for a tab:", error); });
}
async function configureSidePanel(): Promise<chrome.tabs.Tab[]> {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  await chrome.sidePanel.setOptions({ enabled: false });
  const tabs = await chrome.tabs.query({});
  await Promise.all(tabs.map(setPanelForTab));
  return tabs;
}
const ready = (async () => {
  try {
    const saved = (await chrome.storage.session.get("readerState")).readerState as Saved | undefined;
    if (saved) {
      readerError = saved.readerError;
      readerActivity = saved.readerActivity; focusedWindow = saved.focusedWindow;
      sessionTabId = saved.sessionTabId ?? null;
      boundFocus = saved.boundFocus;
    } else {
      const window = await chrome.windows.getLastFocused();
      focusedWindow = window.focused ? window.id! : chrome.windows.WINDOW_ID_NONE;
    }
    const tabs = (await configureSidePanel()).filter(tab => problemUrl(tab.url));
    if (sessionTabId !== null) await trackBoundTab();
    await Promise.all(tabs.map(tab => tab.id === undefined ? Promise.resolve() : chrome.tabs.sendMessage(tab.id, { kind: "watch_mode", mode: tab.id === sessionTabId ? mode : "idle" }).catch(() => {})));
    await save();
  } catch (error) {
    console.error("Leetotter could not finish starting:", error);
    panel({ kind: "error", message: `Leetotter could not finish starting: ${error instanceof Error ? error.message : String(error)}` });
  }
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
      connection.send({ type: "chat_get", ts: new Date().toISOString() });
      connection.send({ type: "planner_get", ts: new Date().toISOString() });
      connection.send({ type: "recaps_get", ts: new Date().toISOString() });
      connection.send({ type: "reference_get", ts: new Date().toISOString() });
      resyncFocus();
    }
    if (next.kind === "error" && importPending) { importPending = false; panel({ kind: "import_error", message: next.message }); }
  },
  message => {
    if (message.type === "chat_state") { panel({ kind: "chat_state", message }); return; }
    if (message.type.startsWith("tutor_")) {
      panel({ kind: "tutor", message });
      if (message.type === "tutor_start") connection.send({ type: "chat_get", ts: new Date().toISOString() });
      return;
    }
    if (message.type === "settings_state") { settingsState = message; panel({ kind: "settings_state", settings: message }); return; }
    if (message.type === "suggestion") { suggestion = message; panel({ kind: "suggestion", message }); return; }
    if (message.type === "planner_open") {
      suggestion = undefined;
      void openPlannerProblem(message.slug, message.title);
      panel({ kind: "planner_open", message });
      return;
    }
    if (message.type === "recap_state") { recapState = message; panel({ kind: "recap_state", message }); return; }
    if (message.type === "recap_noted") { panel({ kind: "recap_noted", message }); return; }
    if (message.type === "recaps_state") { recapsState = message; panel({ kind: "recaps_state", state: message }); return; }
    if (message.type === "progress_state") { progressState = message; panel({ kind: "progress_state", state: message }); return; }
    if (message.type === "progress_category_state") { progressCategoryState = message; panel({ kind: "progress_category_state", state: message }); return; }
    if (message.type === "reset_done") {
      recapState = undefined; recapsState = undefined; progressState = undefined; progressCategoryState = undefined;
      sessionState = undefined; suggestion = undefined;
      imported = false; importResult = undefined;
      mode = "idle"; sessionTabId = null; boundFocus = undefined; plannerTabId = null;
      orphanReported = false; disconnectResumeSent = false;
      panel({ kind: "reset_done" });
      panel({ kind: "session_binding", tabId: null });
      void ready.then(async () => { await save(); await setMode("idle"); });
      return;
    }
    if (message.type === "reference_state") { referenceState = message; panel({ kind: "reference_state", state: message }); return; }
    if (message.type === "reference_flagged") { panel({ kind: "reference_flagged", message }); connection.send({ type: "reference_get", ts: new Date().toISOString() }); return; }
    if (message.type === "reference_verdict") { panel({ kind: "reference_verdict", message }); connection.send({ type: "reference_get", ts: new Date().toISOString() }); return; }
    if (message.type === "session_state") {
      if (notice && message.mode === "watching" && (sessionState?.state === "idle" || sessionState?.state === "ended")) { notice = ""; showReaderAlert(); }
      if (message.state === "active" && message.problem && suggestion) {
        suggestion = undefined;
        panel({ kind: "suggestion", message: null });
      }
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
        await setMode(message.mode === "debrief" ? "watching" : message.mode);
      })();
      panel({ kind: "session_state", state: message });
      return;
    }
    if (message.type === "import_status") imported = message.imported;
    if (message.type === "import_result") { importResult = message.result; imported = true; importPending = false; }
    panel({ kind: "import_state", imported, result: importResult });
  },
);
async function openPlannerProblem(slug: string, title: string): Promise<void> {
  try {
    let tabId = sessionTabId;
    if (tabId === null) {
      if (plannerTabId !== null && await tabExists(plannerTabId)) tabId = plannerTabId;
      else tabId = (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id ?? null;
    }
    if (tabId === null) throw new Error("Open a NeetCode tab to go to a suggestion.");
    const tab = await chrome.tabs.get(tabId);
    if (!neetcodeUrl(tab.url)) throw new Error("Open a NeetCode tab to go to a suggestion.");
    await chrome.tabs.update(tabId, { url: `https://neetcode.io/problems/${slug}/` });
    plannerTabId = tabId;
  } catch (error) {
    panel({ kind: "error", message: `Could not open ${title}: ${error instanceof Error ? error.message : String(error)}` });
  }
}
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
function sendFocus(): void {
  if (mode === "idle" || !boundFocus) return;
  const payload = { tab_active: boundFocus.active, window_focused: boundFocus.windowId === focusedWindow };
  const key = JSON.stringify(payload);
  if (key === boundFocus.lastFocus) return;
  if (send("focus_changed", payload)) boundFocus.lastFocus = key;
}
// A focus change that could not be delivered, or happened while the session was not watching, is sent again.
function resyncFocus(): void {
  if (boundFocus) { boundFocus.lastFocus = undefined; sendFocus(); }
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
      send("reader_check", { ok: false, message: MISSING_READER_MESSAGE });
      readerError = MISSING_READER_MESSAGE;
      showReaderAlert();
      await save();
    }
  }));
}
// Reader events are handled one at a time, in the order the page sent them: problem_opened awaits a tab lookup,
// and the code_snapshot sent right behind it must not overtake it, or the companion files the code before the problem.
let readerQueue: Promise<unknown> = Promise.resolve();
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const handle = async () => {
    await ready;
    if (message?.kind === "get_status") return state;
    if (message?.kind === "get_mode") return sender.tab?.id === undefined || (sender.tab.id === sessionTabId && problemUrl(sender.tab.url)) ? mode : "idle";
    if (message?.kind === "get_reader_error") return readerError || notice;
    if (message?.kind === "get_reader_activity") return readerActivity;
    if (message?.kind === "get_import_state") return { imported, result: importResult };
    if (message?.kind === "get_session_state") return sessionState;
    if (message?.kind === "get_session_binding") return { tabId: sessionTabId };
    if (message?.kind === "get_settings_state") return settingsState;
    if (message?.kind === "get_suggestion") return suggestion;
    if (message?.kind === "get_recap_state") return recapState;
    if (message?.kind === "get_recaps_state") return recapsState;
    if (message?.kind === "get_reference_state") return referenceState;
    if (["help_request", "chat_message", "chat_get"].includes(message?.kind)) {
      if (message.kind === "chat_message" && (typeof message.text !== "string" || !message.text.trim() || [...message.text.trim()].length > 4000)) return { error: "Message must be 1 to 4000 characters." };
      const outgoing = { type: message.kind, ts: new Date().toISOString(), ...(message.kind === "chat_message" ? { payload: { text: message.text.trim() } } : {}) };
      return connection.send(outgoing) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "planner_get" || message?.kind === "planner_swap" || message?.kind === "planner_clear") {
      return connection.send({ type: message.kind, ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "planner_go") {
      plannerTabId = Number.isInteger(message.tabId) ? message.tabId as number : plannerTabId;
      return connection.send({ type: "planner_go", ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "planner_chat") {
      if (typeof message.text !== "string" || !message.text.trim() || [...message.text.trim()].length > 4000) return { error: "Message must be 1 to 4000 characters." };
      return connection.send({ type: "planner_chat", ts: new Date().toISOString(), payload: { text: message.text.trim() } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "recap_get") {
      if (!Number.isInteger(message.session_id) || message.session_id <= 0) return { error: "Invalid recap request." };
      return connection.send({ type: "recap_get", ts: new Date().toISOString(), payload: { session_id: message.session_id } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "recap_feedback") {
      if (!Number.isInteger(message.session_id) || message.session_id <= 0 || typeof message.text !== "string" || !message.text.trim()) return { error: "Feedback text is required." };
      return connection.send({ type: "recap_feedback", ts: new Date().toISOString(), payload: { session_id: message.session_id, text: message.text.trim() } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "recap_seen") {
      if (!Number.isInteger(message.session_id) || message.session_id <= 0) return { error: "Invalid recap request." };
      const sent = connection.send({ type: "recap_seen", ts: new Date().toISOString(), payload: { session_id: message.session_id } });
      if (sent && recapState?.session_id === message.session_id) recapState = undefined;
      return sent ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "recaps_get") {
      return connection.send({ type: "recaps_get", ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "progress_get") {
      return connection.send({ type: "progress_get", ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "progress_category") {
      if (typeof message.category !== "string" || !message.category) return { error: "Invalid category." };
      return connection.send({ type: "progress_category", ts: new Date().toISOString(), payload: { category: message.category } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "grade_correct") {
      const byReview = Number.isInteger(message.review_id) && message.review_id > 0 && message.problem === undefined && message.attempt === undefined;
      const byAttempt = typeof message.problem === "string" && message.problem.length > 0 && Number.isInteger(message.attempt) && message.attempt > 0 && message.review_id === undefined;
      if (!(byReview || byAttempt) || !["good", "hard", "again", "solid", "shaky", "missed"].includes(message.grade)) return { error: "Invalid grade correction." };
      const payload = byReview ? { review_id: message.review_id, grade: message.grade } : { problem: message.problem, attempt: message.attempt, grade: message.grade };
      return connection.send({ type: "grade_correct", ts: new Date().toISOString(), payload }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "session_exclude") {
      if (!Number.isInteger(message.session_id) || message.session_id <= 0) return { error: "Invalid session." };
      return connection.send({ type: "session_exclude", ts: new Date().toISOString(), payload: { session_id: message.session_id } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "reset_progress") {
      if (message.confirm !== "RESET") return { error: "Type RESET to confirm." };
      return connection.send({ type: "reset_progress", ts: new Date().toISOString(), payload: { confirm: "RESET" } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "reference_get") {
      return connection.send({ type: "reference_get", ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "reference_flag") {
      const line = typeof message.line === "string" ? message.line : "";
      if (!Number.isInteger(message.message_id) || message.message_id <= 0 || line.includes("\n") || line.length > 500) return { error: "Invalid flag." };
      return connection.send({ type: "reference_flag", ts: new Date().toISOString(), payload: { message_id: message.message_id, line } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "reference_apply") {
      if (typeof message.slug !== "string" || !message.slug || typeof message.reply !== "string" || !message.reply.trim()) return { error: "Paste the verdict first." };
      return connection.send({ type: "reference_apply", ts: new Date().toISOString(), payload: { slug: message.slug, reply: message.reply } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
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
    if (message?.kind === "followup_skip") {
      if (!Number.isInteger(message.question_id) || message.question_id <= 0) return { error: "Invalid follow-up question." };
      return connection.send({ type: "followup_skip", ts: new Date().toISOString(), payload: { question_id: message.question_id } }) ? { sent: true } : { error: "Tutor service isn't connected." };
    }
    if (message?.kind === "session_command") {
      const command = message.command;
      const types = { start: "session_start", pause: "session_pause", resume: "session_resume", end: "session_end", time_up_extend: "session_time_up", time_up_debrief: "session_time_up", done: "session_done", skip: "session_skip" } as const;
      if (!(command in types)) return { error: "Unknown session command." };
      if (command === "start" || command === "resume") {
        if (sessionState && ["active", "paused"].includes(sessionState.state) && sessionTabId !== message.tabId && await tabExists(sessionTabId)) return { error: "A session is running in another tab." };
        let tab: chrome.tabs.Tab;
        try { tab = await chrome.tabs.get(message.tabId); } catch { return { error: "Open a NeetCode tab to start or resume." }; }
        if (!Number.isInteger(message.tabId) || !neetcodeUrl(tab.url)) return { error: "Open a NeetCode tab to start or resume." };
      }
      const type = types[command as keyof typeof types];
      const payload = command === "time_up_extend" ? { choice: "extend" } : command === "time_up_debrief" ? { choice: "debrief" } : undefined;
      if (command === "resume" && message.problem_slug !== undefined) {
        if (typeof message.problem_slug !== "string" || !/^[a-z0-9-]+$/.test(message.problem_slug)) return { error: "Invalid saved problem." };
        await bindTab(message.tabId);
        try {
          await chrome.tabs.update(message.tabId, { url: `https://neetcode.io/problems/${message.problem_slug}/` });
        } catch (error) {
          return { error: `Could not open the saved problem: ${error instanceof Error ? error.message : String(error)}` };
        }
        orphanReported = false; disconnectResumeSent = false;
        return connection.send({ type, ts: new Date().toISOString() }) ? { sent: true } : { error: "Tutor service isn't connected." };
      }
      if (!connection.send({ type, ts: new Date().toISOString(), ...(payload ? { payload } : {}) })) return { error: "Tutor service isn't connected." };
      if (command === "start" || command === "resume") {
        orphanReported = false; disconnectResumeSent = false; await bindTab(message.tabId);
        plannerTabId = message.tabId;
        if (command === "start") connection.send({ type: "planner_get", ts: new Date().toISOString() });
      }
      if (command === "end") { suggestion = undefined; panel({ kind: "suggestion", message: null }); await bindTab(null); }
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
    if (tab?.id === undefined || !event || typeof event.type !== "string") return { accepted: false, mode };
    if (tab.id !== sessionTabId) return { accepted: false, mode };
    let currentUrl = tab.url;
    if (!problemUrl(currentUrl) || event.type === "problem_opened") {
      try { currentUrl = (await chrome.tabs.get(tab.id)).url; } catch { return { accepted: false, mode }; }
    }
    if (event.type === "reader_check") {
      if (!neetcodeUrl(currentUrl)) return { accepted: false, mode };
    } else if (!problemUrl(currentUrl)) return { accepted: false, mode };
    if (event.type === "problem_opened" && new URL(currentUrl!).pathname.split("/")[2] !== event.payload?.slug) return { accepted: false, mode };
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
      await save(); return { accepted: delivered, mode };
    } catch (error) {
      readerError = (error as Error).message;
      panel({ kind: "reader_error", message: readerError });
      await save(); return { accepted: false, mode };
    }
  };
  const result = message?.kind === "reader_event" ? (readerQueue = readerQueue.then(handle, handle)) : handle();
  result.then(respond, error => { readerError = String(error); panel({ kind: "reader_error", message: readerError }); respond({ accepted: false, mode }); });
  return true;
});
chrome.tabs.onActivated.addListener(info => {
  void ready.then(async () => {
    if (boundFocus) {
      if (info.tabId === sessionTabId) { boundFocus.windowId = info.windowId; boundFocus.active = true; }
      else if (boundFocus.windowId === info.windowId) boundFocus.active = false;
      sendFocus();
    }
    await save();
    await setPanelForTab(await chrome.tabs.get(info.tabId));
  });
});
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (change.url) void ready.then(async () => {
    failedModeTabs.delete(id);
    if (id === sessionTabId && !change.url!.startsWith("https://neetcode.io/")) {
      // The session's tab left NeetCode: nothing is watching any more, so it pauses like a closed tab.
      if (!orphanReported) orphanReported = connection.send({ type: "tab_closed", ts: new Date().toISOString() });
      await bindTab(null);
    } else {
      if (id === sessionTabId) {
        // A NeetCode page that is not a problem keeps its reader idle; opening a problem picks the mode back up.
        const modeForPage = problemUrl(change.url) ? mode : "idle";
        try { await chrome.tabs.sendMessage(id, { kind: "watch_mode", mode: modeForPage }); } catch { /* The new page may not have its reader yet. */ }
      }
      await save();
    }
    await setPanelForTab({ ...tab, id, url: change.url });
  });
});
chrome.tabs.onCreated.addListener(tab => { void ready.then(() => setPanelForTab(tab)); });
chrome.tabs.onRemoved.addListener(id => { void ready.then(async () => {
  failedModeTabs.delete(id);
  if (id === sessionTabId) {
    if (!orphanReported) orphanReported = connection.send({ type: "tab_closed", ts: new Date().toISOString() });
    await bindTab(null);
  } else await save();
}); });
chrome.runtime.onInstalled.addListener(() => { void ready.then(() => configureSidePanel()); });
chrome.windows.onFocusChanged.addListener(id => {
  void ready.then(async () => { focusedWindow = id; sendFocus(); await save(); });
});
void ready.then(() => connection.start());

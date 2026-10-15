import type { ConnectionState } from "./connection";
import type { SessionState, SettingsState } from "./protocol";
import { panelView, timerText, type PanelView } from "./panel-view";
import { remark } from "./remarks";
import { otter, touchForDate } from "./otter";
import type { ReaderReaction } from "./reader-reaction";

const root = document.getElementById("panel");
const body = document.getElementById("session-body");
const headerOtter = document.getElementById("header-otter");
const title = document.getElementById("problem-title");
const dot = document.getElementById("status-dot");
const remarkNode = document.getElementById("remark");
const timer = document.getElementById("timer");
const elapsed = document.getElementById("elapsed");
const budget = document.getElementById("budget");
const controls = document.getElementById("controls");
if (!root || !body || !headerOtter || !title || !dot || !remarkNode || !timer || !elapsed || !budget || !controls) throw new Error("Panel elements are missing");

let transport: ConnectionState = { kind: "offline" };
let session: SessionState | undefined;
let sessionTabId: number | null = null;
let panelTabId: number | null = null;
let boundTabExists = false;
let startAfterEnd = false;
let settings: SettingsState | undefined;
let readerError = "";
let imported = false;
let importResult = "";
let actionError = "";
let settingsOpen = false;
let receivedAt = Date.now();
let currentRemark = "";
let remarkKey = "";
let selectedBudget = 45;
let customBudget = "";
let reactionCaption: string | null = null;
let reactionClass = "";
let reactionTimeout: number | undefined;
let perkTimeout: number | undefined;
let lastTypingReaction = 0;
let pointer: { x: number; y: number } | null = null;
const reactionCaptions = {
  typing: ["I'm here."],
  run_pass: ["Ooh, all green!", "Look at that."],
  run_fail: ["Oof. Close, though.", "Hmm, not quite yet."],
  submit_pass: ["Ooh, all green!", "A shell for that one!"],
  submit_fail: ["Oof. Close, though.", "Hmm, not quite yet."],
} satisfies Record<ReaderReaction, string[]>;

function budgetValue(): number | null {
  if (customBudget !== "") {
    const value = Number(customBudget);
    return /^\d+$/.test(customBudget) && Number.isInteger(value) && value >= 1 && value <= 1440 ? value : null;
  }
  return selectedBudget || null;
}

function positionEyes(): void {
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  for (const svg of root!.querySelectorAll<SVGSVGElement>("svg.otter")) {
    const rect = svg.getBoundingClientRect();
    const vx = pointer ? pointer.x - (rect.left + rect.width / 2) : -1;
    const vy = pointer ? pointer.y - (rect.top + rect.height * .48) : 0;
    const distance = Math.hypot(vx, vy) || 1;
    const scale = pointer ? Math.min(1, distance / 120) : 1;
    const dx = still ? 0 : pointer ? vx / distance * 1.8 * scale : -1.6;
    const dy = still ? 0 : pointer ? vy / distance * 1.4 * scale : .4;
    for (const eyes of svg.querySelectorAll<SVGGElement>(".look")) eyes.style.transform = `translate(${dx}px, ${dy}px)`;
  }
}

function decorateOtters(): void {
  const touch = touchForDate(new Date());
  for (const svg of root!.querySelectorAll<SVGSVGElement>("svg.otter")) svg.setAttribute("data-touch", touch);
  const active = root!.querySelectorAll<SVGSVGElement>("#header-otter svg.otter, .active-otter svg.otter");
  for (const svg of active) if (reactionClass) svg.classList.add(reactionClass);
  positionEyes();
}

function clearReaction(): void {
  window.clearTimeout(reactionTimeout);
  window.clearTimeout(perkTimeout);
  reactionCaption = null;
  reactionClass = "";
}

function playReaction(reaction: ReaderReaction): void {
  if (session?.mode !== "watching") return;
  const now = Date.now();
  if (reaction === "typing" && now - lastTypingReaction < 3000) return;
  if (reaction === "typing") lastTypingReaction = now;
  window.clearTimeout(reactionTimeout);
  window.clearTimeout(perkTimeout);
  const captions = reactionCaptions[reaction];
  reactionCaption = captions[Math.floor(Math.random() * captions.length)];
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const outcomeClass = reaction === "typing" ? "react-nod" : reaction.endsWith("pass") ? "react-yay" : "react-oof";
  reactionClass = still ? "" : reaction.startsWith("run_") ? "react-perk" : outcomeClass;
  const duration = reaction === "typing" ? 450 : reaction.endsWith("pass") ? 1000 : 1200;
  render();
  if (!still && reaction.startsWith("run_")) {
    perkTimeout = window.setTimeout(() => {
      reactionClass = outcomeClass;
      render();
    }, 350);
  }
  reactionTimeout = window.setTimeout(() => { clearReaction(); render(); }, duration + (reaction.startsWith("run_") && !still ? 350 : 0));
}

function element(tag: string, className = "", text = ""): HTMLElement {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function button(id: string, label: string, className = "btn"): HTMLButtonElement {
  const node = element("button", className, label) as HTMLButtonElement;
  node.id = id; node.type = "button";
  return node;
}
function appendText(parent: HTMLElement, tag: string, text: string, className = ""): HTMLElement {
  const node = element(tag, className, text); parent.append(node); return node;
}
function currentSeconds(): number {
  return Math.max(0, (session?.active_seconds || 0) + (session?.running ? Math.floor((Date.now() - receivedAt) / 1000) : 0));
}
function updateTimer(): void {
  if (!session || session.budget_minutes === null || transport.kind === "offline") { timer!.hidden = true; return; }
  timer!.hidden = false;
  const shown = timerText(currentSeconds(), session.budget_minutes);
  elapsed!.textContent = shown.elapsed;
  budget!.textContent = `of ${shown.budget}`;
  timer!.className = `timer${session.running ? "" : " stopped"}${shown.over ? " over" : ""}`;
}
async function refreshPanelTab(): Promise<void> {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  panelTabId = tabs[0]?.id ?? null;
  render();
}
async function refreshBinding(id: number | null): Promise<void> {
  sessionTabId = id;
  boundTabExists = false;
  if (id !== null) {
    try { await chrome.tabs.get(id); boundTabExists = sessionTabId === id; } catch { /* The tab was closed. */ }
  }
  render();
}
function newRemark(view: PanelView): void {
  if (transport.kind === "error") { remarkNode!.textContent = `Tutor service error: ${transport.message}`; return; }
  const key = `${view.remarkPool}:${session?.state || "idle"}:${session?.paused_reason || ""}:${session?.overrun_prompt || false}:${session?.problem?.slug || ""}`;
  if (key !== remarkKey) {
    const minutesLeft = session?.budget_minutes == null ? null : Math.ceil((session.budget_minutes * 60 - currentSeconds()) / 60);
    currentRemark = remark(view.remarkPool, { name: settings?.name || null, minutesLeft, now: new Date() });
    remarkKey = key;
  }
  remarkNode!.textContent = reactionCaption || currentRemark;
}
function renderHeader(view: PanelView): void {
  root!.dataset.theme = settings?.theme || "match";
  headerOtter!.innerHTML = otter(view.body === "paused" ? "doze" : view.body === "offline" ? "confused" : session?.accepted ? "shell" : "float");
  (headerOtter as HTMLButtonElement).disabled = view.body === "offline";
  headerOtter!.setAttribute("aria-expanded", String(settingsOpen && view.body !== "offline"));
  title!.textContent = view.title;
  dot!.className = `dot ${view.statusTone}`;
  dot!.parentElement!.className = `status ${view.statusTone}`;
  newRemark(view);
  updateTimer();
  controls!.replaceChildren();
  for (const control of view.controls) {
    const node = button(`${control}-session`, { pause: "⏸", resume: "▶", end: "End session" }[control], control === "resume" ? "resume" : control === "end" ? "end-session" : "");
    node.title = { pause: "Pause", resume: "Resume", end: "End session" }[control];
    node.setAttribute("aria-label", node.title);
    controls!.append(node);
  }
}
function startCard(): HTMLElement {
  const card = element("section", "card");
  appendText(card, "h4", "New session");
  appendText(card, "p", "How long have you got? It's a soft budget; you can always finish the problem you're on.");
  const choices = element("div", "budget-choices");
  choices.setAttribute("role", "group");
  choices.setAttribute("aria-label", "Session length");
  for (const minutes of [30, 45, 60, 90]) {
    const chip = button(`budget-${minutes}`, `${minutes} min`, "budget-chip");
    chip.setAttribute("aria-pressed", String(selectedBudget === minutes));
    choices.append(chip);
  }
  const custom = element("input", "field budget-custom") as HTMLInputElement;
  custom.id = "budget-minutes";
  custom.type = "number";
  custom.min = "1";
  custom.max = "1440";
  custom.step = "1";
  custom.placeholder = "minutes";
  custom.setAttribute("aria-label", "Or type minutes");
  custom.value = customBudget;
  const label = element("label", "budget-other", "or type minutes");
  label.append(custom);
  choices.append(label);
  const start = button("start-session", "Start", "btn primary");
  start.disabled = budgetValue() === null;
  card.append(choices, start);
  return card;
}
function firstRun(): HTMLElement {
  const block = element("section", "block"); block.id = "first-run";
  block.innerHTML = otter("float");
  appendText(block, "h3", "Hi, I'm Leetotter!");
  appendText(block, "p", "I'll float nearby while you practise. What should I call you?");
  const row = element("div", "hello");
  const input = element("input", "field") as HTMLInputElement;
  input.id = "first-name"; input.placeholder = "Your name"; input.maxLength = 40; input.autocomplete = "off";
  row.append(input, button("save-first-name", "That's me", "btn primary")); block.append(row);
  const hint = appendText(block, "p", "Type a name, or press Skip.", "name-hint");
  hint.id = "name-hint";
  hint.hidden = true;
  block.append(button("skip-name", "Skip", "link"));
  appendText(block, "p", "Tap me anytime to change it.", "fine");
  return block;
}
function settingsCard(): HTMLElement {
  const card = element("section", "card settings"); card.id = "settings-card";
  const head = element("div", "row");
  appendText(head, "h4", "Settings"); head.append(button("close-settings", "✕", "icon")); card.append(head);
  const nameLabel = element("label"); appendText(nameLabel, "span", "What should I call you?");
  const input = element("input", "field") as HTMLInputElement;
  input.id = "settings-name"; input.value = settings?.name || ""; input.maxLength = 40; input.autocomplete = "off";
  const nameRow = element("div", "settings-name-row");
  const save = button("save-settings-name", "Save", "btn");
  save.disabled = true;
  nameRow.append(input, save);
  nameLabel.append(nameRow); card.append(nameLabel);
  const hint = appendText(card, "p", "Type a name to save it.", "name-hint");
  hint.id = "name-hint"; hint.hidden = true;
  const themeLabel = element("label"); appendText(themeLabel, "span", "Theme");
  const select = element("select", "field") as HTMLSelectElement; select.id = "settings-theme";
  for (const [value, label] of [["match", "Match my Mac"], ["light", "Light"], ["dark", "Dark"]]) {
    const option = document.createElement("option"); option.value = value; option.textContent = label; select.append(option);
  }
  select.value = settings?.theme || "match"; themeLabel.append(select); card.append(themeLabel);
  appendText(card, "p", "Your name stays on this Mac and is only used for the otter's remarks.", "fine");
  return card;
}
function block(pose: "doze" | "confused", heading: string, explanation: string): HTMLElement {
  const node = element("section", "block"); node.innerHTML = otter(pose);
  appendText(node, "h3", heading); appendText(node, "p", explanation);
  return node;
}
function historyRow(): HTMLElement {
  const row = element("div", "history quiet");
  const importButton = button("import-history", "Import history", "link"); importButton.hidden = imported;
  row.append(importButton); appendText(row, "p", importResult).id = "import-result";
  return row;
}
function render(): void {
  const view = panelView({ transport, session_state: session, settings, readerError, sessionTabId, panelTabId, boundTabExists });
  renderHeader(view);
  body!.replaceChildren();
  if (view.body === "another_tab") {
    const card = element("section", "card");
    appendText(card, "p", "A session is running in another tab.");
    card.append(button("go-to-session-tab", "Go to that tab", "btn primary"));
    body!.append(card);
    decorateOtters(); return;
  }
  if (view.body === "offline") {
    const offline = block("confused", "Tutor service isn't running", "Start it with:");
    appendText(offline, "code", "uv run python -m companion", "cmd");
    appendText(offline, "p", "The panel checks again every few seconds.", "quiet");
    body!.append(offline); decorateOtters(); return;
  }
  if (settingsOpen) body!.append(settingsCard());
  for (const message of [...view.alerts, actionError]) if (message) appendText(body!, "div", message, "alert").setAttribute("role", "alert");
  if (!settingsOpen) {
    if (view.body === "first_run") body!.append(firstRun());
    if (view.body === "ended") appendText(body!, "p", `Session ended · ${timerText(session?.active_seconds || 0, session?.budget_minutes || 0).elapsed}`, "quiet");
    if (["start", "ended"].includes(view.body)) body!.append(startCard());
    if (view.body === "active") {
      const node = element("figure", "active-otter");
      node.innerHTML = otter(session?.accepted ? "shell" : "float");
      appendText(node, "figcaption", session?.accepted ? "Accepted! Keep polishing, or move on." : currentRemark);
      body!.append(node);
    }
    if (view.body === "paused") body!.append(block("doze", view.pauseTitle || "Paused", view.pauseText || ""));
    if (view.body === "welcome_back") {
      const card = element("section", "card welcome-back");
      appendText(card, "h4", "Pick up where you left off?");
      const minutes = Math.floor((session?.active_seconds || 0) / 60);
      const duration = `${minutes} min of ${session?.budget_minutes ?? 0}`;
      appendText(card, "p", session?.problem?.title ? `${session.problem.title}, ${duration}` : duration);
      card.append(button("welcome-resume", "Resume", "btn primary"), button("end-and-start", "End and start a new one", "link"));
      body!.append(card);
    }
    if (view.body === "overrun") {
      const card = element("section", "card"); appendText(card, "h4", "Time's up. Finish this one or stop?");
      const row = element("div", "row"); row.append(button("overrun-finish", "Finish this one", "btn primary"), button("overrun-stop", "Stop here")); card.append(row); body!.append(card);
    }
  }
  body!.append(historyRow());
  decorateOtters();
}
async function send(message: object): Promise<boolean> {
  try {
    const result = await chrome.runtime.sendMessage(message);
    if (result?.error) { actionError = String(result.error); render(); return false; }
    else if (actionError) { actionError = ""; render(); }
    return true;
  } catch (error) { actionError = error instanceof Error ? error.message : String(error); render(); return false; }
}
function saveName(id: string): void {
  const input = document.getElementById(id) as HTMLInputElement | null;
  if (!input) return;
  const value = input.value.trim();
  if (!value) {
    document.getElementById("name-hint")!.hidden = false;
    return;
  }
  if (id === "settings-name" && value === (settings?.name || "")) return;
  void send({ kind: "settings_set", key: "name", value });
}
headerOtter.addEventListener("click", () => { settingsOpen = !settingsOpen; render(); });
body.addEventListener("click", event => {
  const id = (event.target as HTMLElement).closest("button")?.id;
  if (id === "close-settings") { settingsOpen = false; render(); }
  if (id === "save-first-name") saveName("first-name");
  if (id === "save-settings-name") saveName("settings-name");
  if (id === "skip-name") void send({ kind: "skip_name" });
  if (id?.startsWith("budget-")) {
    selectedBudget = Number(id.slice("budget-".length));
    customBudget = "";
    const custom = document.getElementById("budget-minutes") as HTMLInputElement;
    custom.value = "";
    (document.getElementById("start-session") as HTMLButtonElement).disabled = false;
    for (const chip of body!.querySelectorAll<HTMLButtonElement>(".budget-chip")) chip.setAttribute("aria-pressed", String(chip.id === id));
  }
  if (id === "start-session") {
    const value = budgetValue();
    if (value !== null) void send({ kind: "session_command", command: "start", budget_minutes: value, tabId: panelTabId });
  }
  if (id === "go-to-session-tab" && sessionTabId !== null) void chrome.tabs.get(sessionTabId).then(async tab => {
    await chrome.tabs.update(tab.id!, { active: true });
    if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, { focused: true });
  }).catch(error => { actionError = String(error); render(); });
  if (id === "welcome-resume") void send({ kind: "session_command", command: "resume", tabId: panelTabId });
  if (id === "end-and-start") void send({ kind: "session_command", command: "end" }).then(ok => {
    if (!ok || !session) return;
    startAfterEnd = true;
    session = { ...session, state: "idle", mode: "idle", paused_reason: null, running: false };
    sessionTabId = null; boundTabExists = false;
    render();
  });
  const command = { "pause-session": "pause", "resume-session": "resume", "end-session": "end", "overrun-finish": "overrun_finish", "overrun-stop": "overrun_stop" }[id || ""];
  if (command) void send({ kind: "session_command", command, ...(command === "resume" ? { tabId: panelTabId } : {}) });
  if (id === "import-history") {
    const importButton = document.getElementById("import-history") as HTMLButtonElement;
    importButton.disabled = true;
    void chrome.runtime.sendMessage({ kind: "import_history" }).then(result => { importResult = result?.error || "Importing NeetCode history…"; render(); }, error => { importResult = `History import failed: ${String(error)}`; render(); });
  }
});
body.addEventListener("input", event => {
  const target = event.target as HTMLInputElement;
  if (target.id === "settings-name") {
    (document.getElementById("save-settings-name") as HTMLButtonElement).disabled = !target.value.trim() || target.value.trim() === (settings?.name || "");
    return;
  }
  if (target.id !== "budget-minutes") return;
  customBudget = target.value;
  selectedBudget = 0;
  for (const chip of body!.querySelectorAll<HTMLButtonElement>(".budget-chip")) chip.setAttribute("aria-pressed", "false");
  (document.getElementById("start-session") as HTMLButtonElement).disabled = budgetValue() === null;
});
root.addEventListener("pointermove", event => {
  pointer = { x: event.clientX, y: event.clientY };
  positionEyes();
});
root.addEventListener("pointerleave", () => { pointer = null; positionEyes(); });
controls.addEventListener("click", event => {
  const id = (event.target as HTMLElement).closest("button")?.id;
  const command = { "pause-session": "pause", "resume-session": "resume", "end-session": "end" }[id || ""];
  if (command) void send({ kind: "session_command", command, ...(command === "resume" ? { tabId: panelTabId } : {}) });
});
body.addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.target as HTMLElement).id === "first-name") { event.preventDefault(); saveName("first-name"); }
  if (event.key === "Enter" && (event.target as HTMLElement).id === "settings-name") { event.preventDefault(); saveName("settings-name"); }
});
body.addEventListener("change", event => {
  const target = event.target as HTMLInputElement | HTMLSelectElement;
  if (target.id === "settings-theme") void send({ kind: "settings_set", key: "theme", value: target.value });
});
chrome.runtime.onMessage.addListener(message => {
  if (message?.kind === "transport") {
    transport = message.state;
    if (transport.kind === "offline") { settingsOpen = false; clearReaction(); }
    render();
  }
  if (message?.kind === "session_state") {
    session = message.state;
    if (startAfterEnd && session?.state === "ended") session = { ...session, state: "idle", mode: "idle" };
    if (session?.state === "active") startAfterEnd = false;
    if (session?.mode !== "watching") clearReaction();
    receivedAt = Date.now(); render();
  }
  if (message?.kind === "session_binding") void refreshBinding(message.tabId);
  if (message?.kind === "reader_reaction") playReaction(message.reaction);
  if (message?.kind === "settings_state") { settings = message.settings; render(); }
  if (message?.kind === "reader_error") { readerError = message.message || ""; render(); }
  if (message?.kind === "import_state") {
    imported = message.imported;
    if (message.result) importResult = message.result.already_imported ? "NeetCode history was already imported." : `Imported ${message.result.matched} solved problems.${message.result.unmatched.length ? ` Unmatched URLs: ${message.result.unmatched.join(", ")}` : ""}`;
    render();
  }
  if (message?.kind === "import_error") { importResult = message.message; render(); }
});
render();
chrome.tabs.onActivated.addListener(() => { void refreshPanelTab(); });
void refreshPanelTab();
void Promise.all([
  chrome.runtime.sendMessage({ kind: "get_status" }),
  chrome.runtime.sendMessage({ kind: "get_session_state" }),
  chrome.runtime.sendMessage({ kind: "get_settings_state" }),
  chrome.runtime.sendMessage({ kind: "get_reader_error" }),
  chrome.runtime.sendMessage({ kind: "get_import_state" }),
  chrome.runtime.sendMessage({ kind: "get_session_binding" }),
]).then(([status, currentSession, currentSettings, error, importState, binding]) => {
  transport = status; session = currentSession; settings = currentSettings; readerError = error || "";
  imported = importState?.imported || false; if (importState?.result) importResult = importState.result.already_imported ? "NeetCode history was already imported." : `Imported ${importState.result.matched} solved problems.`;
  receivedAt = Date.now(); void refreshBinding(binding?.tabId ?? null);
}).catch(error => { transport = { kind: "error", message: String(error) }; render(); });
window.setInterval(updateTimer, 1000);
window.setInterval(decorateOtters, 10 * 60 * 1000);

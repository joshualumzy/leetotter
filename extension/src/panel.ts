import type { ConnectionState } from "./connection";
import type { SessionState, SettingsState } from "./protocol";
import { panelView, timerText, type PanelView } from "./panel-view";
import { remark } from "./remarks";
import { otter } from "./otter";

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
let settings: SettingsState | undefined;
let readerError = "";
let imported = false;
let importResult = "";
let actionError = "";
let settingsOpen = false;
let receivedAt = Date.now();
let currentRemark = "";
let remarkKey = "";

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
function newRemark(view: PanelView): void {
  if (transport.kind === "error") { remarkNode!.textContent = `Tutor service error: ${transport.message}`; return; }
  const key = `${view.remarkPool}:${session?.state || "idle"}:${session?.paused_reason || ""}:${session?.overrun_prompt || false}:${session?.problem?.slug || ""}`;
  if (key !== remarkKey) {
    const minutesLeft = session?.budget_minutes == null ? null : Math.ceil((session.budget_minutes * 60 - currentSeconds()) / 60);
    currentRemark = remark(view.remarkPool, { name: settings?.name || null, minutesLeft, now: new Date() });
    remarkKey = key;
  }
  remarkNode!.textContent = currentRemark;
}
function renderHeader(view: PanelView): void {
  root!.dataset.theme = settings?.theme || "match";
  headerOtter!.innerHTML = otter(view.body === "paused" ? "doze" : view.body === "offline" ? "confused" : "float");
  (headerOtter as HTMLButtonElement).disabled = view.body === "offline";
  headerOtter!.setAttribute("aria-expanded", String(settingsOpen && view.body !== "offline"));
  title!.textContent = view.title;
  dot!.className = `dot ${view.statusTone}`;
  newRemark(view);
  updateTimer();
  controls!.replaceChildren();
  for (const control of view.controls) {
    const node = button(`${control}-session`, { pause: "⏸", resume: "▶", end: "⏹" }[control], control === "resume" ? "resume" : "");
    node.title = { pause: "Pause", resume: "Resume", end: "End session" }[control];
    node.setAttribute("aria-label", node.title);
    controls!.append(node);
  }
}
function startCard(): HTMLElement {
  const card = element("section", "card");
  appendText(card, "h4", "Set the timer");
  appendText(card, "p", "It's a soft budget; you can always finish the problem you're on.");
  const row = element("div", "row");
  const input = element("input", "field minutes") as HTMLInputElement;
  input.id = "budget-minutes"; input.type = "number"; input.min = "1"; input.max = "1440"; input.value = "45";
  input.setAttribute("aria-label", "Minutes");
  row.append(input, appendText(element("span"), "span", "minutes"), button("start-session", "Go", "btn primary"));
  card.append(row);
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
  nameLabel.append(input); card.append(nameLabel);
  const themeLabel = element("label"); appendText(themeLabel, "span", "Theme");
  const select = element("select", "field") as HTMLSelectElement; select.id = "settings-theme";
  for (const [value, label] of [["match", "Match NeetCode"], ["light", "Light"], ["dark", "Dark"]]) {
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
  const view = panelView({ transport, session_state: session, settings, readerError });
  renderHeader(view);
  body!.replaceChildren();
  if (view.body === "offline") {
    const offline = block("confused", "Tutor service isn't running", "Start it with:");
    appendText(offline, "code", "uv run python -m companion", "cmd");
    appendText(offline, "p", "The panel checks again every few seconds.", "quiet");
    body!.append(offline); return;
  }
  if (settingsOpen) body!.append(settingsCard());
  for (const message of [...view.alerts, actionError]) if (message) appendText(body!, "div", message, "alert").setAttribute("role", "alert");
  if (!settingsOpen) {
    if (view.body === "first_run") body!.append(firstRun());
    if (view.body === "ended") appendText(body!, "p", `Session ended · ${timerText(session?.active_seconds || 0, session?.budget_minutes || 0).elapsed}`, "quiet");
    if (["first_run", "start", "ended"].includes(view.body)) body!.append(startCard());
    if (view.body === "active") { const node = element("div", "active-otter"); node.innerHTML = otter("float"); body!.append(node); }
    if (view.body === "paused") body!.append(block("doze", view.pauseTitle || "Paused", view.pauseText || ""));
    if (view.body === "overrun") {
      const card = element("section", "card"); appendText(card, "h4", "Time's up. Finish this one or stop?");
      const row = element("div", "row"); row.append(button("overrun-finish", "Finish this one", "btn primary"), button("overrun-stop", "Stop here")); card.append(row); body!.append(card);
    }
  }
  body!.append(historyRow());
}
async function send(message: object): Promise<void> {
  try {
    const result = await chrome.runtime.sendMessage(message);
    if (result?.error) { actionError = String(result.error); render(); }
    else if (actionError) { actionError = ""; render(); }
  } catch (error) { actionError = error instanceof Error ? error.message : String(error); render(); }
}
function saveName(id: string): void {
  const input = document.getElementById(id) as HTMLInputElement | null;
  if (input) void send({ kind: "settings_set", key: "name", value: input.value.trim() });
}
headerOtter.addEventListener("click", () => { settingsOpen = !settingsOpen; render(); });
body.addEventListener("click", event => {
  const id = (event.target as HTMLElement).closest("button")?.id;
  if (id === "close-settings") { settingsOpen = false; render(); }
  if (id === "save-first-name") saveName("first-name");
  if (id === "skip-name") void send({ kind: "settings_set", key: "name", value: "" });
  if (id === "start-session") {
    const value = Number((document.getElementById("budget-minutes") as HTMLInputElement).value);
    if (!Number.isInteger(value) || value < 1 || value > 1440) { actionError = "Enter a timer from 1 to 1440 minutes."; render(); return; }
    void send({ kind: "session_command", command: "start", budget_minutes: value });
  }
  const command = { "pause-session": "pause", "resume-session": "resume", "end-session": "end", "overrun-finish": "overrun_finish", "overrun-stop": "overrun_stop" }[id || ""];
  if (command) void send({ kind: "session_command", command });
  if (id === "import-history") {
    const importButton = document.getElementById("import-history") as HTMLButtonElement;
    importButton.disabled = true;
    void chrome.runtime.sendMessage({ kind: "import_history" }).then(result => { importResult = result?.error || "Importing NeetCode history…"; render(); }, error => { importResult = `History import failed: ${String(error)}`; render(); });
  }
});
controls.addEventListener("click", event => {
  const id = (event.target as HTMLElement).closest("button")?.id;
  const command = { "pause-session": "pause", "resume-session": "resume", "end-session": "end" }[id || ""];
  if (command) void send({ kind: "session_command", command });
});
body.addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.target as HTMLElement).id === "first-name") { event.preventDefault(); saveName("first-name"); }
  if (event.key === "Enter" && (event.target as HTMLElement).id === "settings-name") { event.preventDefault(); saveName("settings-name"); }
});
body.addEventListener("change", event => {
  const target = event.target as HTMLInputElement | HTMLSelectElement;
  if (target.id === "settings-name") saveName("settings-name");
  if (target.id === "settings-theme") void send({ kind: "settings_set", key: "theme", value: target.value });
});
chrome.runtime.onMessage.addListener(message => {
  if (message?.kind === "transport") { transport = message.state; if (transport.kind === "offline") settingsOpen = false; render(); }
  if (message?.kind === "session_state") { session = message.state; receivedAt = Date.now(); render(); }
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
void Promise.all([
  chrome.runtime.sendMessage({ kind: "get_status" }),
  chrome.runtime.sendMessage({ kind: "get_session_state" }),
  chrome.runtime.sendMessage({ kind: "get_settings_state" }),
  chrome.runtime.sendMessage({ kind: "get_reader_error" }),
  chrome.runtime.sendMessage({ kind: "get_import_state" }),
]).then(([status, currentSession, currentSettings, error, importState]) => {
  transport = status; session = currentSession; settings = currentSettings; readerError = error || "";
  imported = importState?.imported || false; if (importState?.result) importResult = importState.result.already_imported ? "NeetCode history was already imported." : `Imported ${importState.result.matched} solved problems.`;
  receivedAt = Date.now(); render();
}).catch(error => { transport = { kind: "error", message: String(error) }; render(); });
window.setInterval(updateTimer, 1000);

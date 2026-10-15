import type { ConnectionState } from "./connection";
import type { SessionState, SettingsState } from "./protocol";
import { panelView, timerText, type PanelView } from "./panel-view";
import { remark } from "./remarks";
import { otter, touchForDate } from "./otter";
import type { ReaderReaction } from "./reader-reaction";
import { applyChat, emptyChat, type ChatTurn } from "./chat-state";
import { StreamPacer } from "./stream-pacer";
import { renderMarkdown } from "./markdown";
import { composerAvailability, enterAction, messageToSend } from "./composer-rules";
import type { ChatState, TutorMessage } from "./protocol";

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
const composer = document.getElementById("composer");
if (!root || !body || !headerOtter || !title || !dot || !remarkNode || !timer || !elapsed || !budget || !controls || !composer) throw new Error("Panel elements are missing");

let transport: ConnectionState = { kind: "offline" };
let session: SessionState | undefined;
let sessionTabId: number | null = null;
let panelTabId: number | null = null;
let panelTabUrl = "";
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
let pendingName: { value: string; pool: "name_first" | "name_settings" } | null = null;
let nameAcknowledgement = "";
let acknowledgementTimer: number | undefined;
let selectedBudget = 45;
let customBudget = "";
let reactionCaption: string | null = null;
let reactionClass = "";
let reactionTimeout: number | undefined;
let perkTimeout: number | undefined;
let lastTypingReaction = 0;
let pointer: { x: number; y: number } | null = null;
let chat = emptyChat();
let chatSlug: string | null = null;
let draft = "";
let composerNote = "";
const FADE_MS = 280;
type Birth = { from: number; at: number };
const pacers = new Map<number, { attempt: number; pacer: StreamPacer; births: Birth[] }>();
// The text each bubble currently shows, so an unchanged bubble is never rebuilt (that would drop a selection).
const painted = new WeakMap<HTMLElement, string>();
let paceTimer: number | undefined;
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
  panelTabUrl = tabs[0]?.url ?? "";
  render();
}
function onProblemPage(): boolean { return /^https:\/\/neetcode\.io\/problems\/[^/]+/.test(panelTabUrl); }
function onCurrentProblem(): boolean {
  if (!session?.problem) return false;
  try {
    const url = new URL(panelTabUrl);
    return url.origin === "https://neetcode.io" && url.pathname.startsWith("/problems/") && url.pathname.split("/")[2] === session.problem.slug;
  } catch { return false; }
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
  if (nameAcknowledgement) { remarkNode!.textContent = nameAcknowledgement; return; }
  if (chat.streaming !== null && !chat.turns.some(turn => turn.turn_id === chat.streaming && turn.kind === "reply" && turn.text)) {
    remarkNode!.textContent = remark("thinking", { name: settings?.name || null, minutesLeft: null, now: new Date() });
    return;
  }
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
  start.disabled = budgetValue() === null || !onProblemPage();
  card.append(choices, start);
  if (!onProblemPage()) appendText(card, "p", "Open a problem on NeetCode to start.", "quiet");
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
function chatNode(turn: ChatTurn): HTMLElement {
  const className = { press: "chat-press", chat: "chat-you", reply: "chat-reply", failed: "chat-error" }[turn.kind];
  const node = element("div", `chat-line ${className}`);
  node.dataset.key = `${turn.turn_id}:${turn.kind}`;
  if (turn.kind === "press") node.textContent = "You asked for a hint";
  else if (turn.kind === "failed") { node.textContent = turn.message || "The Tutor couldn't answer."; node.setAttribute("role", "alert"); }
  else fillBubble(node, turn, turn.kind === "reply" && pacers.has(turn.turn_id) ? pacers.get(turn.turn_id)!.pacer.visible : turn.text || "");
  return node;
}
function paintReply(node: HTMLElement, text: string, freshFrom?: number): void {
  node.replaceChildren(renderMarkdown(text, freshFrom));
  painted.set(node, text);
}
function fillBubble(node: HTMLElement, turn: ChatTurn, text: string): void {
  if (painted.get(node) === text) return;
  if (turn.kind === "reply") paintReply(node, text);
  else { node.textContent = text; painted.set(node, text); }
}
function nearBottom(): boolean { return body!.scrollHeight - body!.scrollTop - body!.clientHeight < 60; }
function scrollToEnd(): void { body!.scrollTop = body!.scrollHeight; }
function renderChat(): void {
  const stream = document.getElementById("chat-stream");
  if (!stream) return;
  const follow = nearBottom();
  const existing = new Map([...stream.querySelectorAll<HTMLElement>("[data-key]")].map(node => [node.dataset.key, node]));
  const nodes = chat.turns.map(turn => {
    const key = `${turn.turn_id}:${turn.kind}`;
    const node = existing.get(key) || chatNode(turn);
    if (turn.kind === "reply" && pacers.has(turn.turn_id)) {
      // The pacer owns visible text until it has played the whole answer.
    } else if (turn.kind === "reply" || turn.kind === "chat" || turn.kind === "failed") {
      fillBubble(node, turn, turn.kind === "failed" ? turn.message || "" : turn.text || "");
    }
    return node;
  });
  if (chat.notice) nodes.push(element("div", "chat-notice", chat.notice));
  if (nodes.length !== stream.children.length || nodes.some((node, index) => stream.children[index] !== node)) stream.replaceChildren(...nodes);
  if (follow) scrollToEnd();
  updateComposer();
}
function renderComposer(view: PanelView): void {
  const visible = ["active", "paused", "overrun"].includes(view.body);
  composer!.hidden = !visible || settingsOpen;
  if (composer!.hidden) { composer!.replaceChildren(); return; }
  // Built once while visible: the panel redraws every second for the timer, and rebuilding would drop the typing focus.
  if (composer!.childElementCount) { updateComposer(); return; }
  const help = button("help-request", "I need help", "help-request");
  const box = element("div", "composer-box");
  const input = element("textarea", "chat-input") as HTMLTextAreaElement;
  input.id = "chat-input"; input.placeholder = "Message the Tutor…"; input.rows = 2;
  input.setAttribute("aria-label", "Message the Tutor"); input.value = draft;
  const arrow = button("chat-send", "➤", "chat-send"); arrow.setAttribute("aria-label", "Send message");
  box.append(input, arrow);
  const reason = appendText(composer!, "div", "", "composer-reason"); reason.id = "composer-reason";
  const note = appendText(composer!, "div", composerNote, "composer-note"); note.id = "composer-note";
  composer!.prepend(help, box);
  resizeComposer(input);
  updateComposer();
}
function resizeComposer(input: HTMLTextAreaElement): void {
  input.style.height = "auto";
  const style = window.getComputedStyle(input);
  const lineHeight = Number.parseFloat(style.lineHeight) || 20;
  const limit = Math.max(lineHeight * 2, root!.clientHeight * .4);
  input.style.height = `${Math.min(Math.max(input.scrollHeight, lineHeight * 2 + 18), limit)}px`;
  input.scrollTop = input.scrollHeight;
}
function updateComposer(): void {
  const help = document.getElementById("help-request") as HTMLButtonElement | null;
  const input = document.getElementById("chat-input") as HTMLTextAreaElement | null;
  const arrow = document.getElementById("chat-send") as HTMLButtonElement | null;
  if (!help || !input || !arrow) return;
  const availability = composerAvailability(session?.state, Boolean(session?.problem), chat.streaming !== null || pacers.size > 0);
  help.disabled = input.disabled = !availability.enabled;
  arrow.disabled = !availability.enabled || !draft.trim();
  document.getElementById("composer-reason")!.textContent = availability.reason;
  document.getElementById("composer-note")!.textContent = composerNote;
}
function reducedMotion(): boolean { return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches); }
// Each fading word started at its own moment: backdate its animation so a repaint does not restart the fade.
function paintStreaming(node: HTMLElement, text: string, births: Birth[], now: number): void {
  paintReply(node, text, reducedMotion() ? undefined : births[0]?.from);
  for (const span of node.querySelectorAll<HTMLElement>(".stream-word")) {
    const from = Number(span.dataset.from);
    const birth = births.filter(candidate => candidate.from <= from).at(-1)!;
    span.style.animationDelay = `${birth.at - now}ms`;
  }
}
function pace(): void {
  window.clearTimeout(paceTimer);
  let pending = false;
  const follow = nearBottom();
  const now = Date.now();
  for (const [id, entry] of pacers) {
    const frame = entry.pacer.tick(now);
    const node = document.querySelector<HTMLElement>(`#chat-stream [data-key="${id}:reply"]`);
    if (node) {
      let from = frame.text.length - frame.added.join("").length;
      for (const word of frame.added) { entry.births.push({ from, at: now }); from += word.length; }
      entry.births = entry.births.filter(birth => now - birth.at < FADE_MS);
      if (frame.added.length || painted.get(node) !== frame.text) paintStreaming(node, frame.text, entry.births, now);
      if (frame.finished && node.querySelector(".stream-word")) {
        window.setTimeout(() => {
          if (node.isConnected && painted.get(node) === frame.text) paintReply(node, frame.text);
        }, FADE_MS);
      }
    }
    if (frame.finished) pacers.delete(id);
    else if (!frame.waiting) pending = true;
  }
  if (follow) scrollToEnd();
  updateComposer();
  if (pending) paceTimer = window.setTimeout(pace, 30);
}
function startPacer(turn_id: number, attempt: number, text: string): { attempt: number; pacer: StreamPacer; births: Birth[] } {
  const pacer = new StreamPacer(); pacer.push(text);
  const entry = { attempt, pacer, births: [] };
  pacers.set(turn_id, entry);
  return entry;
}
function receiveTutor(message: TutorMessage): void {
  if (message.type === "tutor_chunk") {
    const current = pacers.get(message.turn_id);
    if (!current) startPacer(message.turn_id, message.attempt, message.text);
    else if (message.attempt > current.attempt) {
      current.pacer.replace(message.text);
      current.attempt = message.attempt;
      current.births = [];
      const old = document.querySelector<HTMLElement>(`#chat-stream [data-key="${message.turn_id}:reply"]`);
      if (old) paintReply(old, "");
    } else if (message.attempt === current.attempt) current.pacer.push(message.text);
  }
  if (message.type === "tutor_done") {
    const entry = pacers.get(message.turn_id) || startPacer(message.turn_id, 1, message.reply_text);
    entry.pacer.finish(message.reply_text);
  }
  if (message.type === "tutor_error" && message.turn_id !== null) pacers.delete(message.turn_id);
  chat = applyChat(chat, message);
  if (!document.getElementById("chat-stream") && chat.turns.length) render();
  else if (document.getElementById("chat-stream") && !chat.turns.length) render();
  else renderChat();
  const view = panelView({ transport, session_state: session, settings, readerError, sessionTabId, panelTabId, boundTabExists, panelOnProblemPage: onProblemPage() });
  newRemark(view);
  if (pacers.size) pace();
}
function render(): void {
  const view = panelView({ transport, session_state: session, settings, readerError, sessionTabId, panelTabId, boundTabExists, panelOnProblemPage: onProblemPage() });
  renderHeader(view);
  renderComposer(view);
  // The conversation survives a redraw as the same elements, so scroll position, selection and code scrolling stay put.
  const keptStream = document.getElementById("chat-stream");
  const scrollTop = body!.scrollTop;
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
    if (view.body === "active" && !chat.turns.length) {
      const node = element("figure", "active-otter");
      node.innerHTML = otter(session?.accepted ? "shell" : "float");
      appendText(node, "figcaption", session?.accepted ? "Accepted! Keep polishing, or move on." : "I'm here to help whenever you need me.");
      body!.append(node);
    }
    if (["active", "paused", "overrun"].includes(view.body) && chat.turns.length) {
      const stream = keptStream || element("section", "chat-stream");
      stream.id = "chat-stream";
      body!.append(stream);
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
  body!.scrollTop = scrollTop;
  renderChat();
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
function requestChatState(): void {
  void chrome.runtime.sendMessage({ kind: "chat_get" }).then(result => {
    if (result?.error && transport.kind === "connected") {
      actionError = String(result.error);
      render();
    }
  }).catch(error => {
    if (transport.kind === "connected") {
      actionError = error instanceof Error ? error.message : String(error);
      render();
    }
  });
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
  const request = { value, pool: id === "first-name" ? "name_first" : "name_settings" } as const;
  pendingName = request;
  void send({ kind: "settings_set", key: "name", value }).then(ok => { if (!ok && pendingName === request) pendingName = null; });
}
headerOtter.addEventListener("click", () => { settingsOpen = !settingsOpen; render(); });
async function sendChat(): Promise<void> {
  const input = document.getElementById("chat-input") as HTMLTextAreaElement | null;
  if (!input || input.disabled) return;
  const result = messageToSend(input.value);
  composerNote = result.error;
  if (!result.text) { updateComposer(); return; }
  const text = result.text;
  chat = applyChat(chat, { type: "local_chat", text });
  draft = ""; input.value = ""; resizeComposer(input);
  if (!document.getElementById("chat-stream")) render(); else renderChat();
  if (!await send({ kind: "chat_message", text })) requestChatState();
}
composer.addEventListener("click", event => {
  const id = (event.target as HTMLElement).closest("button")?.id;
  if (id === "help-request") {
    chat = applyChat(chat, { type: "local_help" });
    if (!document.getElementById("chat-stream")) render(); else renderChat();
    void send({ kind: "help_request" }).then(ok => { if (!ok) requestChatState(); });
  }
  if (id === "chat-send") void sendChat();
});
composer.addEventListener("input", event => {
  const input = event.target as HTMLTextAreaElement;
  if (input.id !== "chat-input") return;
  draft = input.value; composerNote = "";
  resizeComposer(input); updateComposer();
});
composer.addEventListener("keydown", event => {
  if ((event.target as HTMLElement).id !== "chat-input") return;
  if (event.isComposing) return;
  if (enterAction(event.key, event.shiftKey) === "send") { event.preventDefault(); void sendChat(); }
});
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
    (document.getElementById("start-session") as HTMLButtonElement).disabled = !onProblemPage();
    for (const chip of body!.querySelectorAll<HTMLButtonElement>(".budget-chip")) chip.setAttribute("aria-pressed", String(chip.id === id));
  }
  if (id === "start-session") {
    const value = budgetValue();
    if (value !== null && onProblemPage()) void send({ kind: "session_command", command: "start", budget_minutes: value, tabId: panelTabId });
  }
  if (id === "go-to-session-tab" && sessionTabId !== null) void chrome.tabs.get(sessionTabId).then(async tab => {
    await chrome.tabs.update(tab.id!, { active: true });
    if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, { focused: true });
  }).catch(error => { actionError = String(error); render(); });
  if (id === "welcome-resume") void send({ kind: "session_command", command: "resume", tabId: panelTabId, ...(session?.problem && !onCurrentProblem() ? { problem_slug: session.problem.slug } : {}) });
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
  (document.getElementById("start-session") as HTMLButtonElement).disabled = budgetValue() === null || !onProblemPage();
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
    const slug = session?.problem?.slug || null;
    if (slug !== chatSlug) { chatSlug = slug; chat = emptyChat(); pacers.clear(); if (slug) requestChatState(); }
    if (startAfterEnd && session?.state === "ended") session = { ...session, state: "idle", mode: "idle" };
    if (session?.state === "active") startAfterEnd = false;
    if (session?.mode !== "watching") clearReaction();
    receivedAt = Date.now(); render();
  }
  if (message?.kind === "session_binding") void refreshBinding(message.tabId);
  if (message?.kind === "reader_reaction") playReaction(message.reaction);
  if (message?.kind === "chat_state") {
    chat = applyChat(chat, message.message as ChatState);
    for (const id of pacers.keys()) {
      if (id !== chat.streaming && !chat.turns.some(turn => turn.kind === "reply" && turn.turn_id === id)) pacers.delete(id);
    }
    if (Boolean(document.getElementById("chat-stream")) !== Boolean(chat.turns.length)) render(); else renderChat();
  }
  if (message?.kind === "tutor") receiveTutor(message.message as TutorMessage);
  if (message?.kind === "settings_state") {
    settings = message.settings;
    if (pendingName && settings?.name === pendingName.value) {
      window.clearTimeout(acknowledgementTimer);
      nameAcknowledgement = remark(pendingName.pool, { name: pendingName.value, minutesLeft: null, now: new Date() });
      pendingName = null;
      acknowledgementTimer = window.setTimeout(() => { nameAcknowledgement = ""; render(); }, 4000);
    }
    render();
  }
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
chrome.tabs.onUpdated.addListener((id, change) => { if (id === panelTabId && change.url) void refreshPanelTab(); });
void refreshPanelTab();
requestChatState();
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

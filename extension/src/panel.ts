import type { ConnectionState } from "./connection";
import { MISSING_READER_MESSAGE, type Followup, type ProgressCategoryState, type ProgressState, type RecapItem, type RecapNoted, type RecapsState, type ReferenceState, type SessionState, type SettingsState, type Suggestion } from "./protocol";
import { categoryCount, earlierLabel, healthLine, progressHeaderLines, recapDate, untriedLine } from "./tabs-view";
import { capLine, clock, panelView, timerText, type PanelView } from "./panel-view";
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
const clockNode = document.getElementById("clock");
const controls = document.getElementById("controls");
const composer = document.getElementById("composer");
const tabs = document.getElementById("tabs");
const recapsUnreadNode = document.getElementById("recaps-unread");
const progressBadge = document.getElementById("progress-badge");
if (!root || !body || !headerOtter || !title || !dot || !remarkNode || !timer || !clockNode || !controls || !composer || !tabs || !recapsUnreadNode || !progressBadge) throw new Error("Panel elements are missing");

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
let suggestion: Suggestion | undefined;
const PLANNER_WAIT_MS = 200_000;
let plannerWaitStartedAt: number | null = null;
type PlannerEntry = { kind: "said"; text: string } | { kind: "card"; suggestion: Suggestion };
let plannerLog: PlannerEntry[] = [];
let plannerTimedOut = false;
let plannerTimer: number | undefined;
let activeTab: "session" | "recaps" | "progress" = "session";
let recaps: RecapsState["recaps"] | undefined;
let recapsUnread = false;
const recapSeenSent = new Set<number>();
const recapHidden = new Set<number>();
const recapDrafts = new Map<number, string>();
const recapNoted = new Map<number, string>();
let progress: ProgressState | undefined;
let categoryDetail: ProgressCategoryState | undefined;
let openCategory: string | null = null;
let correction: number | null = null;
const earlierOpen = new Set<string>();
let excludeSessionId: number | null = null;
let excludeStep: "idle" | "confirm" = "idle";
let resetStep: "idle" | "warning" | "confirm" = "idle";
let resetText = "";
let referenceState: ReferenceState | undefined;
let referenceNotice = "";
let flagTurn: number | null = null;
let flagDraft = "";
const flagNoted = new Set<number>();
const referenceDrafts = new Map<string, string>();
let reactionCaption: string | null = null;
let reactionClass = "";
let reactionTimeout: number | undefined;
let perkTimeout: number | undefined;
let captionTimeout: number | undefined;
let lastTypingReaction = 0;
let pointer: { x: number; y: number } | null = null;
let chat = emptyChat();
let chatSlug: string | null = null;
let chatSession: number | null = null;
let draft = "";
let composerNote = "";
let composerMode: "hidden" | "tutor" | "planner" = "hidden";
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
  window.clearTimeout(captionTimeout);
  reactionCaption = null;
  reactionClass = "";
}

function endPlannerWait(): void {
  window.clearTimeout(plannerTimer);
  plannerTimer = undefined;
  plannerWaitStartedAt = null;
  plannerTimedOut = false;
}
const PLANNER_WAIT_LINES = [
  "Paddling through the problem list...",
  "Sniffing out a good one...",
  "Diving for a problem...",
  "Checking what you've been practicing...",
  "Juggling a few pebbles to choose from...",
];
let plannerWaitLine = PLANNER_WAIT_LINES[0];
function beginPlannerWait(): void {
  window.clearTimeout(plannerTimer);
  plannerWaitStartedAt = Date.now();
  plannerWaitLine = PLANNER_WAIT_LINES[Math.floor(Math.random() * PLANNER_WAIT_LINES.length)]!;
  plannerTimedOut = false;
  plannerTimer = window.setTimeout(() => {
    plannerTimer = undefined;
    plannerTimedOut = true;
    render();
  }, PLANNER_WAIT_MS);
}
function plannerWaiting(): boolean { return plannerWaitStartedAt !== null; }
function rememberSuggestion(): void {
  if (!suggestion) return;
  plannerLog.push({ kind: "card", suggestion });
  suggestion = undefined;
}

function playReaction(reaction: ReaderReaction): void {
  if (session?.mode !== "watching") return;
  const now = Date.now();
  if (reaction === "typing" && now - lastTypingReaction < 3000) return;
  if (reaction === "typing") lastTypingReaction = now;
  window.clearTimeout(reactionTimeout);
  window.clearTimeout(perkTimeout);
  window.clearTimeout(captionTimeout);
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
  const animation = duration + (reaction.startsWith("run_") && !still ? 350 : 0);
  reactionTimeout = window.setTimeout(() => { reactionClass = ""; render(); }, animation);
  // The hop is brief, but a run or submit outcome's words stay long enough to read.
  captionTimeout = window.setTimeout(() => { reactionCaption = null; render(); }, reaction === "typing" ? animation : 5000);
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
function currentCapSeconds(): number | null {
  if (!session || session.cap_seconds_left === null) return null;
  return Math.max(0, session.cap_seconds_left - (session.running ? Math.floor((Date.now() - receivedAt) / 1000) : 0));
}
function updateTimer(): void {
  if (!session?.problem || ["idle", "ended"].includes(session.state) || transport.kind === "offline") { timer!.hidden = true; return; }
  const left = currentCapSeconds();
  if (left === null) { timer!.hidden = true; return; }
  timer!.hidden = false;
  clockNode!.textContent = clock(left);
  timer!.className = `timer${session.running ? "" : " stopped"}${left === 0 ? " over" : ""}`;
}
async function refreshPanelTab(): Promise<void> {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  panelTabId = tabs[0]?.id ?? null;
  panelTabUrl = tabs[0]?.url ?? "";
  render();
}
function onProblemPage(): boolean { return /^https:\/\/neetcode\.io\/problems\/[^/]+/.test(panelTabUrl); }
function onNeetCode(): boolean { return /^https:\/\/neetcode\.io(?:\/|$)/.test(panelTabUrl); }
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
    remarkNode!.textContent = remark("thinking", { name: settings?.name || null, now: new Date() });
    return;
  }
  const key = `${view.remarkPool}:${session?.state || "idle"}:${session?.paused_reason || ""}:${session?.mode || ""}:${session?.time_up ? "up" : ""}:${session?.problem?.slug || ""}:${suggestion?.slug || ""}`;
  if (key !== remarkKey) {
    currentRemark = remark(view.remarkPool, { name: settings?.name || null, now: new Date() });
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
  appendText(card, "p", "The Planner will suggest one problem. Go with it, swap it, or steer it when its card appears.");
  const start = button("start-session", "Start", "btn primary");
  start.disabled = !onNeetCode();
  card.append(start);
  if (!onNeetCode()) appendText(card, "p", "Open NeetCode to start.", "quiet");
  return card;
}
function suggestionSummary(card: HTMLElement, item: Suggestion): void {
  appendText(card, "h4", item.title);
  const cap = capLine(item.difficulty, item.cap_minutes);
  if (cap) appendText(card, "p", cap, "cap-line");
  if (item.message) appendText(card, "p", item.message, "planner-message");
}
function pastSuggestionCard(item: Suggestion): HTMLElement {
  const card = element("section", "card past-card");
  suggestionSummary(card, item);
  return card;
}
function suggestionCard(): HTMLElement | null {
  if (!suggestion) return null;
  const card = element("section", "card suggestion-card");
  suggestionSummary(card, suggestion);
  if (suggestion.steer) {
    const steer = element("div", "steer-line");
    appendText(steer, "span", `Steering: ${suggestion.steer}`);
    const clear = button("planner-clear", "Clear", "link");
    steer.append(clear);
    card.append(steer);
  }
  const row = element("div", "row");
  const go = button("planner-go", "Go", "btn primary");
  const swap = button("planner-swap", "Swap", "btn");
  row.append(go, swap);
  card.append(row);
  return card;
}
function plannerWaitingCard(): HTMLElement {
  const card = element("section", "card planner-waiting-card");
  const otterNode = element("figure", "planner-waiting-otter"); otterNode.innerHTML = otter("float");
  card.append(otterNode);
  appendText(card, "h4", "Choosing a problem for you...");
  const line = element("p", "planner-waiting-line");
  line.innerHTML = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';
  line.append(document.createTextNode(plannerWaitLine));
  card.append(line);
  return card;
}
function plannerTimeoutCard(): HTMLElement {
  const card = element("section", "card planner-timeout-card");
  card.setAttribute("role", "alert");
  appendText(card, "h4", "The Planner is taking too long.");
  card.append(button("planner-retry", "Try again", "btn primary"));
  return card;
}
function recapCard(item: RecapItem): HTMLElement {
  const card = element("section", "card recap-item");
  card.dataset.session = String(item.session_id);
  appendText(card, "p", recapDate(item.date), "recap-date");
  if (item.status === "preparing") {
    const otterNode = element("figure", "recap-otter"); otterNode.innerHTML = otter("doze");
    card.append(otterNode);
    appendText(card, "h4", "Notes on your session");
    appendText(card, "p", "The otter is still writing them up. They will appear here when ready.");
    return card;
  }
  if (item.status === "failed") {
    card.setAttribute("role", "alert");
    appendText(card, "h4", "Session review is missing");
    appendText(card, "p", item.message || "The recap could not be written; it will be retried next time.");
    card.append(button(`recap-close-${item.session_id}`, "Close", "link"));
    return card;
  }
  appendText(card, "h4", item.status === "feedback_failed" ? "Recap" : "Session recap");
  if (item.status === "feedback_failed") {
    const alert = appendText(card, "p", item.message || "The feedback review failed; your note was saved.", "alert recap-alert");
    alert.setAttribute("role", "alert");
  }
  if (item.bullets.length) {
    appendText(card, "p", "What changed:", "quiet");
    const list = element("ul", "recap-bullets");
    for (const bullet of item.bullets) appendText(list, "li", bullet);
    card.append(list);
  } else {
    appendText(card, "p", "Smooth session. Nothing to flag.", "quiet");
  }
  const noted = recapNoted.get(item.session_id);
  if (noted) {
    appendText(card, "p", noted, "recap-noted");
  } else if (item.bullets.length && item.status !== "feedback_failed") {
    const input = element("input", "field recap-feedback") as HTMLInputElement;
    input.id = `recap-feedback-${item.session_id}`;
    input.placeholder = "Anything off or missing?";
    input.value = recapDrafts.get(item.session_id) || "";
    input.maxLength = 500;
    input.setAttribute("aria-label", "Anything off or missing?");
    card.append(input);
  }
  card.append(button(`recap-close-${item.session_id}`, "Close", "link"));
  return card;
}
function renderRecaps(): void {
  const view = element("section", "recaps-view");
  const visible = (recaps || []).filter(item => !recapHidden.has(item.session_id));
  if (!visible.length) {
    appendText(view, "p", recaps ? "No recaps yet. They appear here after a session." : "Loading recaps…", "quiet");
  } else {
    for (const item of visible) view.append(recapCard(item));
  }
  for (const item of visible) {
    if (item.status !== "ready" || item.seen || recapSeenSent.has(item.session_id)) continue;
    recapSeenSent.add(item.session_id);
    void send({ kind: "recap_seen", session_id: item.session_id });
  }
  body!.append(view);
}
function levelClass(level: string): string {
  return `level level-${level.replace(/ /g, "-")}`;
}
function progressBar(): HTMLElement {
  const bar = element("div", "progress-bar");
  const parts = [
    ["own", progress!.header.on_your_own, "on your own"],
    ["back", progress!.header.coming_back, "coming back"],
    ["todo", progress!.header.to_do, "to do"],
  ] as const;
  for (const [key, count, label] of parts) {
    const segment = element("i", key);
    segment.style.flex = String(count);
    segment.setAttribute("aria-label", `${count} ${label}`);
    if (!count) segment.style.display = "none";
    bar.append(segment);
  }
  return bar;
}
function disagreementMarker(parent: HTMLElement): void {
  appendText(parent, "span", "graders disagreed", "problem-disagreement");
}
function gradeRow(parent: HTMLElement, entry: ProgressCategoryState["problems"][number]["latest"], problem: string): void {
  if (!entry) return;
  const row = element("div", "problem-grade");
  appendText(row, "span", entry.word, `grade-word ${entry.word}`);
  appendText(row, "span", entry.reason, "grade-reason");
  if (entry.disagreement) disagreementMarker(row);
  row.append(button(`correct-${entry.review_id}`, "correct", "link"));
  parent.append(row);
  if (correction === entry.review_id) {
    const choices = element("div", "correction");
    for (const word of ["solid", "shaky", "missed"] as const) {
      choices.append(button(`grade-${entry.review_id}-${word}`, word, "btn"));
    }
    parent.append(choices);
  }
  const problemItem = categoryDetail?.problems.find(item => item.problem === problem);
  if (problemItem && problemItem.earlier_count > 0) {
    parent.append(button(`earlier-${problem}`, earlierLabel(problemItem.earlier_count), "link earlier-toggle"));
    if (earlierOpen.has(problem)) {
      const earlier = element("div", "earlier");
      for (const old of problemItem.earlier) {
        const line = element("div", "problem-grade");
        appendText(line, "span", old.word, `grade-word ${old.word}`);
        appendText(line, "span", old.reason, "grade-reason");
        if (old.disagreement) disagreementMarker(line);
        earlier.append(line);
      }
      parent.append(earlier);
    }
  }
}
function categoryProblems(parent: HTMLElement): void {
  if (!categoryDetail) {
    appendText(parent, "p", "Loading…", "quiet");
    return;
  }
  if (!categoryDetail.problems.length) {
    appendText(parent, "p", "No problems attempted here yet.", "quiet");
    return;
  }
  for (const item of categoryDetail.problems) {
    const node = element("div", "problem");
    const head = element("div", "problem-head");
    appendText(head, "span", item.title, "problem-title");
    appendText(head, "span", item.status_text, "problem-status");
    node.append(head);
    if (item.pending) {
      const waiting = item.pending_reason === "reference" ? "grade pending (reference)" : "grade pending (graders)";
      appendText(node, "span", waiting, "problem-pending");
    }
    if (item.disagreement) disagreementMarker(node);
    if (item.latest) gradeRow(node, item.latest, item.problem);
    else if (!item.pending) appendText(node, "span", "not graded yet", "quiet");
    parent.append(node);
  }
}
function excludeControl(parent: HTMLElement): void {
  const sessions = recaps || [];
  if (!sessions.length) return;
  if (excludeSessionId === null || !sessions.some(item => item.session_id === excludeSessionId)) {
    excludeSessionId = sessions[0].session_id;
  }
  if (excludeStep === "confirm") {
    const warning = element("div", "exclude-confirm");
    appendText(warning, "p", "This removes that session's grades from Progress. It can't be undone.");
    const row = element("div", "row");
    row.append(button("exclude-session-confirm", "Exclude", "btn"), button("exclude-session-cancel", "Cancel", "link"));
    warning.append(row);
    parent.append(warning);
    return;
  }
  const row = element("div", "exclude-row");
  const select = element("select", "field") as HTMLSelectElement;
  select.id = "exclude-session";
  select.setAttribute("aria-label", "Session to exclude");
  for (const item of sessions) {
    const option = document.createElement("option");
    option.value = String(item.session_id);
    option.textContent = recapDate(item.date);
    select.append(option);
  }
  select.value = String(excludeSessionId);
  row.append(select, button("exclude-session-start", "Exclude session…", "link"));
  parent.append(row);
}
function renderCategoryDetail(name: string): HTMLElement {
  const card = element("section", "category-detail");
  appendText(card, "h4", name);
  if (!categoryDetail || categoryDetail.category !== name) {
    appendText(card, "p", "Loading…", "quiet");
    return card;
  }
  appendText(card, "p", categoryDetail.next, "category-next");
  categoryProblems(card);
  const untried = untriedLine(categoryDetail.counts);
  if (untried) appendText(card, "p", untried, "quiet");
  excludeControl(card);
  appendText(card, "p", healthLine(categoryDetail.health), "quiet progress-health");
  return card;
}
function renderProgress(): void {
  const view = element("section", "progress-view");
  if (!progress) {
    appendText(view, "p", "Loading progress…", "quiet");
    body!.append(view);
    return;
  }
  const head = element("div", "progress-head");
  const numbers = element("div", "progress-nums");
  for (const line of progressHeaderLines(progress.header, progress.categories.length)) appendText(numbers, "span", line);
  head.append(numbers, progressBar());
  view.append(head);
  const list = element("div", "category-list");
  for (const category of progress.categories) {
    const row = element("button", `category-row${openCategory === category.name ? " on" : ""}`) as HTMLButtonElement;
    row.type = "button";
    row.dataset.category = category.name;
    appendText(row, "span", category.name, "category-name");
    appendText(row, "span", category.level, levelClass(category.level));
    appendText(row, "span", categoryCount(category.done, category.total), "category-count");
    if (category.rusty) appendText(row, "span", "rusty", "rusty");
    list.append(row);
    if (openCategory === category.name) list.append(renderCategoryDetail(category.name));
  }
  view.append(list);
  const review = referenceReviewSection({ verdicts: false, notice: true });
  if (review) view.append(review);
  appendText(view, "p", healthLine(progress.health), "quiet progress-health");
  body!.append(view);
}
function timeUpCard(): HTMLElement {
  const card = element("section", "card time-up-card");
  appendText(card, "h4", "Time's up");
  appendText(card, "p", "This problem has reached its active-time cap.");
  const row = element("div", "row");
  if (session?.time_up?.can_extend) row.append(button("time-up-extend", "Extend 10 minutes", "btn primary"));
  row.append(button("time-up-debrief", "Debrief", session?.time_up?.can_extend ? "btn" : "btn primary"));
  row.append(button("time-up-skip", "Skip", "link"));
  card.append(row);
  return card;
}
function followupCard(item: Followup): HTMLElement {
  const card = element("section", "card followup-card");
  appendText(card, "h4", "One more thought");
  appendText(card, "p", item.question, "followup-question");
  const row = element("div", "row");
  appendText(row, "span", "Answer in the chat, or skip", "quiet");
  row.append(button("followup-skip", "Skip", "link"));
  card.append(row);
  return card;
}
function debriefActions(): HTMLElement {
  const row = element("div", "card debrief-actions");
  appendText(row, "span", "Debriefing this problem.", "quiet");
  row.append(button("debrief-done", "Done", "btn primary"), button("debrief-skip", "Skip", "link"));
  return row;
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
function referenceReviewSection(options: { verdicts: boolean; notice: boolean }): HTMLElement | null {
  if (!referenceState || referenceState.count <= 0) return null;
  const state = referenceState;
  const review = element("section", "reference-review");
  appendText(review, "h4", `References needing review (${state.count})`);
  appendText(review, "p", options.verdicts ? "Copy a prompt to a separate AI, then paste its verdict here." : "Copy a prompt to a separate AI, then paste its verdict in Settings.", "fine");
  if (options.notice && referenceNotice) {
    const note = appendText(review, "p", referenceNotice, "reference-notice");
    note.id = "reference-notice";
  }
  for (const item of state.references) {
    const details = element("details", "reference-item");
    appendText(details, "summary", item.title);
    if (!item.prompt) {
      appendText(details, "p", item.reason || "The reference isn't ready yet.", "quiet");
    } else {
      const actions = element("div", "row");
      actions.append(button(`copy-reference-${item.slug}`, "Copy prompt", "btn"));
      details.append(actions);
      if (options.verdicts) {
        const input = element("textarea", "field reference-verdict") as HTMLTextAreaElement;
        input.id = `verdict-${item.slug}`; input.rows = 3; input.placeholder = "Paste the verdict JSON";
        input.value = referenceDrafts.get(item.slug) || "";
        details.append(input, button(`reference-apply-${item.slug}`, "Apply", "btn primary"));
      }
    }
    review.append(details);
  }
  return review;
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
  appendText(card, "p", "Your name stays on this Mac and is only used for the otter's remarks.", "fine name-note");
  const themeLabel = element("label"); appendText(themeLabel, "span", "Theme");
  const select = element("select", "field") as HTMLSelectElement; select.id = "settings-theme";
  for (const [value, label] of [["match", "Match my Mac"], ["light", "Light"], ["dark", "Dark"]]) {
    const option = document.createElement("option"); option.value = value; option.textContent = label; select.append(option);
  }
  select.value = settings?.theme || "match"; themeLabel.append(select); card.append(themeLabel);
  appendText(card, "h4", "Reset", "reset-title");
  if (resetStep === "idle") {
    card.append(button("reset-progress", "Reset all my progress", "btn"));
  } else if (resetStep === "warning") {
    const warning = element("div", "reset-warning");
    warning.id = "reset-warning";
    appendText(warning, "p", "This erases every session, review, note and your name. It can't be undone.");
    const row = element("div", "row");
    row.append(button("reset-continue", "Continue", "btn"), button("reset-cancel", "Cancel", "link"));
    warning.append(row);
    card.append(warning);
  } else {
    const label = element("label");
    appendText(label, "span", "Type RESET to confirm");
    const input = element("input", "field") as HTMLInputElement;
    input.id = "reset-confirm"; input.value = resetText; input.autocomplete = "off"; input.maxLength = 5;
    label.append(input); card.append(label);
    appendText(card, "p", "This can't be undone.", "fine");
    const row = element("div", "row");
    const sendReset = button("reset-send", "Reset everything", "btn");
    sendReset.disabled = resetText !== "RESET";
    row.append(sendReset, button("reset-cancel", "Cancel", "link"));
    card.append(row);
  }
  if (referenceNotice) {
    const note = appendText(card, "p", referenceNotice, "reference-notice");
    note.id = "reference-notice";
  }
  const review = referenceReviewSection({ verdicts: true, notice: false });
  if (review) card.append(review);
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
const FLAG_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 2v12M4 3h7l-1.5 2.5L11 8H4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const CHECK_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function flagFooter(turn_id: number): HTMLElement {
  const footer = element("div", "chat-flag");
  const open = flagTurn === turn_id;
  footer.dataset.state = `${flagNoted.has(turn_id) ? "noted" : "idle"}:${open ? "open" : "closed"}`;
  footer.classList.toggle("open", open);
  if (flagNoted.has(turn_id)) {
    const noted = element("span", "flag-noted");
    noted.setAttribute("role", "status");
    noted.setAttribute("aria-label", "Noted");
    noted.title = "Noted";
    noted.innerHTML = `${CHECK_ICON}<span>Noted</span>`;
    footer.append(noted);
    return footer;
  }
  const toggle = element("button", "flag-toggle") as HTMLButtonElement;
  toggle.id = `seems-wrong-${turn_id}`;
  toggle.type = "button";
  toggle.setAttribute("aria-label", "Seems wrong");
  toggle.title = "Seems wrong";
  toggle.innerHTML = FLAG_ICON;
  footer.append(toggle);
  if (!open) return footer;
  const row = element("div", "flag-row");
  const input = element("input", "field flag-line") as HTMLInputElement;
  input.placeholder = "What looks off? (optional)"; input.maxLength = 500;
  input.setAttribute("aria-label", "What looks off?");
  input.value = flagDraft;
  row.append(input, button(`flag-send-${turn_id}`, "Send", "btn"));
  footer.append(row);
  return footer;
}
function updateFlagFooter(node: HTMLElement, turn_id: number): void {
  const footer = node.querySelector<HTMLElement>(".chat-flag");
  if (!footer) return;
  const state = `${flagNoted.has(turn_id) ? "noted" : "idle"}:${flagTurn === turn_id ? "open" : "closed"}`;
  if (footer.dataset.state !== state) {
    const next = flagFooter(turn_id);
    footer.replaceWith(next);
    if (state.endsWith("open")) next.querySelector<HTMLInputElement>(".flag-line")?.focus();
    return;
  }
  const input = footer.querySelector<HTMLInputElement>(".flag-line");
  if (input && input.value !== flagDraft) input.value = flagDraft;
}
function chatNode(turn: ChatTurn): HTMLElement {
  const className = { press: "chat-press", chat: "chat-you", reply: "chat-reply", failed: "chat-error" }[turn.kind];
  const node = element("div", `chat-line ${className}`);
  node.dataset.key = `${turn.turn_id}:${turn.kind}`;
  if (turn.kind === "press") node.textContent = "You asked for a hint";
  else if (turn.kind === "failed") { node.textContent = turn.message || "The Tutor couldn't answer."; node.setAttribute("role", "alert"); }
  else fillBubble(node, turn, turn.kind === "reply" && pacers.has(turn.turn_id) ? pacers.get(turn.turn_id)!.pacer.visible : turn.text || "");
  if (turn.kind === "reply") node.append(flagFooter(turn.turn_id));
  return node;
}
function paintReply(node: HTMLElement, text: string, freshFrom?: number): void {
  const footer = node.querySelector<HTMLElement>(".chat-flag");
  node.replaceChildren(renderMarkdown(text, freshFrom));
  if (footer) node.append(footer);
  painted.set(node, text);
}
function fillBubble(node: HTMLElement, turn: ChatTurn, text: string): void {
  if (painted.get(node) === text) return;
  if (turn.kind === "reply") paintReply(node, text);
  else { node.textContent = text; painted.set(node, text); }
}
// Shown from the moment you ask until the first word arrives (and again if the first attempt is withdrawn and retried);
// the longer-wait line is added by a timer once it has been waiting 10 s, and the wait survives redraws.
const WAIT_MORE_MS = 10_000;
const waiting = element("div", "chat-line chat-waiting");
waiting.innerHTML = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="wait-text">Thinking it through</span><span class="wait-more" hidden>, still working on it</span>';
let waitSince: number | null = null;
let waitTimer = 0;
// True from the moment you send (before the companion's tutor_start arrives) until a reply has started to show.
function awaitingReply(): boolean {
  const last = chat.turns.at(-1);
  if (!last || chat.notice) return false;
  if (last.kind === "press" || last.kind === "chat") return true;
  return last.kind === "reply" && !last.text && chat.streaming === last.turn_id && !pacers.get(last.turn_id)?.pacer.visible;
}
function updateWaiting(): void {
  window.clearTimeout(waitTimer);
  if (!awaitingReply()) { waitSince = null; return; }
  waitSince ??= Date.now();
  const left = WAIT_MORE_MS - (Date.now() - waitSince);
  (waiting.querySelector(".wait-more") as HTMLElement).hidden = left > 0;
  if (left > 0) waitTimer = window.setTimeout(updateWaiting, left);
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
    if (turn.kind === "reply") updateFlagFooter(node, turn.turn_id);
    return node;
  });
  if (awaitingReply()) nodes.push(waiting);
  updateWaiting();
  if (chat.notice) nodes.push(element("div", "chat-notice", chat.notice));
  if (nodes.length !== stream.children.length || nodes.some((node, index) => stream.children[index] !== node)) stream.replaceChildren(...nodes);
  if (follow) scrollToEnd();
  updateComposer();
}
function renderComposer(view: PanelView): void {
  // Until the session has a problem, the Planner is the only one to talk to, and only once it has a card to talk about.
  const plannerMode = view.body === "active" && !session?.problem;
  const visible = activeTab === "session" && (plannerMode ? Boolean(suggestion) && !plannerTimedOut : ["active", "paused", "debrief"].includes(view.body));
  composer!.hidden = !visible || settingsOpen;
  composerMode = composer!.hidden ? "hidden" : plannerMode ? "planner" : "tutor";
  if (composer!.hidden) { composer!.replaceChildren(); return; }
  // Built once while visible: the panel redraws every second for the timer, and rebuilding would drop the typing focus.
  if (composer!.childElementCount) { updateComposer(); return; }
  const help = button("help-request", "I need help", "help-request");
  const box = element("div", "composer-box");
  const input = element("textarea", "chat-input") as HTMLTextAreaElement;
  input.id = "chat-input"; input.rows = 2;
  input.setAttribute("aria-label", "Message the Tutor"); input.value = draft;
  const arrow = button("chat-send", "➤", "chat-send"); arrow.setAttribute("aria-label", "Send message");
  box.append(input, arrow);
  const reason = appendText(composer!, "div", "", "composer-reason"); reason.id = "composer-reason"; reason.setAttribute("role", "status");
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
  const streaming = chat.streaming !== null || pacers.size > 0 || awaitingReply();
  const plannerMode = composerMode === "planner";
  if (plannerMode) {
    help.hidden = true;
    help.disabled = true;
    input.disabled = streaming;
    arrow.disabled = streaming || !draft.trim();
    input.placeholder = "Tell the Planner what to focus on…";
    document.getElementById("composer-reason")!.textContent = streaming ? "The Planner is thinking…" : "";
    document.getElementById("composer-note")!.textContent = draft.trim() ? "" : "Your words steer the next suggestion.";
    return;
  }
  help.hidden = false;
  const availability = composerAvailability(session?.state, Boolean(session?.problem), streaming);
  help.disabled = !availability.enabled;
  input.disabled = !availability.typing;
  arrow.disabled = !availability.enabled || !draft.trim();
  input.placeholder = "Message the Tutor…";
  document.getElementById("composer-reason")!.textContent = availability.reason;
  document.getElementById("composer-note")!.textContent = composerNote || (session?.state === "active" && session?.problem ? "The Tutor takes steering at the next suggestion." : "");
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
type TurnOwner = { slug: string | null; session: number | null };
const turnOwners = new Map<number, TurnOwner>();
function receiveTutor(message: TutorMessage): void {
  if (message.turn_id !== null) {
    if (message.type === "tutor_start") {
      turnOwners.set(message.turn_id, { slug: message.slug ?? chatSlug, session: message.session_id ?? chatSession });
    }
    const owner = turnOwners.get(message.turn_id);
    const slugWrong = message.slug !== undefined ? message.slug !== chatSlug : owner !== undefined && owner.slug !== chatSlug;
    const sessionWrong = message.session_id !== undefined ? message.session_id !== chatSession : owner !== undefined && owner.session !== chatSession;
    if (slugWrong || sessionWrong) {
      pacers.delete(message.turn_id); turnOwners.delete(message.turn_id); return;
    }
  }
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
  if ((message.type === "tutor_done" || message.type === "tutor_error") && message.turn_id !== null) turnOwners.delete(message.turn_id);
  if (!document.getElementById("chat-stream") && chat.turns.length) render();
  else if (document.getElementById("chat-stream") && !chat.turns.length) render();
  else renderChat();
  const view = panelView({ transport, session_state: session, settings, readerError, sessionTabId, panelTabId, boundTabExists, panelOnProblemPage: onProblemPage(), problemOpen: onCurrentProblem() });
  newRemark(view);
  if (pacers.size) pace();
}
function renderTabs(view: PanelView): void {
  tabs!.hidden = view.body === "offline";
  for (const [id, tab] of [["tab-session", "session"], ["tab-recaps", "recaps"], ["tab-progress", "progress"]] as const) {
    document.getElementById(id)!.classList.toggle("on", activeTab === tab);
  }
  recapsUnreadNode!.hidden = !recapsUnread;
  const reviews = referenceState?.count || 0;
  progressBadge!.hidden = reviews === 0;
  progressBadge!.textContent = reviews ? String(reviews) : "";
  progressBadge!.setAttribute("aria-label", `${reviews} ${reviews === 1 ? "reference needs" : "references need"} review`);
}
function appendAlerts(view: PanelView): void {
  for (const message of [...view.alerts, actionError]) if (message) {
    const alert = appendText(body!, "div", message, "alert");
    alert.setAttribute("role", "alert");
    // After an extension reload the open NeetCode tab keeps no reader; one click reloads it.
    if (message === MISSING_READER_MESSAGE && sessionTabId != null) alert.append(" ", button("reload-tab", "Reload tab", "btn"));
  }
}
function render(): void {
  const view = panelView({ transport, session_state: session, settings, readerError, sessionTabId, panelTabId, boundTabExists, panelOnProblemPage: onProblemPage(), problemOpen: onCurrentProblem() });
  renderHeader(view);
  renderComposer(view);
  renderTabs(view);
  // The conversation survives a redraw as the same elements, so scroll position, selection and code scrolling stay put.
  const keptStream = document.getElementById("chat-stream");
  const scrollTop = body!.scrollTop;
  // Settings open at the top of the panel: don't jump to the end of the conversation.
  const followEnd = nearBottom() && !settingsOpen;
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
  if (settingsOpen) {
    appendAlerts(view);
    body!.append(settingsCard());
    decorateOtters();
    return;
  }
  appendAlerts(view);
  if (activeTab !== "session") {
    if (activeTab === "recaps") renderRecaps();
    else renderProgress();
    body!.scrollTop = scrollTop;
    decorateOtters();
    return;
  }
  const referenceMessage = session?.reference_message || (session?.reference_status === "not_checked_yet" ? "Reference not checked yet" : "");
  if (referenceMessage && session?.problem && ["active", "paused", "time_up", "debrief"].includes(view.body)) {
    appendText(body!, "p", referenceMessage, "reference-status");
  }
  if (referenceNotice) appendText(body!, "p", referenceNotice, "reference-notice");
  if (view.body === "first_run") body!.append(firstRun());
  if (view.body === "ended") appendText(body!, "p", `Session ended · ${timerText(session?.active_seconds || 0).elapsed}`, "quiet");
  if (["start", "ended"].includes(view.body)) {
    body!.append(startCard());
  }
  if (view.body === "active" && !session?.problem) {
    for (const entry of plannerLog) {
      if (entry.kind === "said") appendText(body!, "div", entry.text, "chat-line chat-you");
      else body!.append(pastSuggestionCard(entry.suggestion));
    }
    if (plannerWaiting() && plannerTimedOut) body!.append(plannerTimeoutCard());
    else if (plannerWaiting()) body!.append(plannerWaitingCard());
    else if (suggestion) {
      const suggestionNode = suggestionCard();
      if (suggestionNode) body!.append(suggestionNode);
    }
  }
  if (view.body === "active" && !chat.turns.length && !plannerWaiting()) {
    const node = element("figure", "active-otter");
    node.innerHTML = otter(session?.accepted ? "shell" : "float");
    appendText(node, "figcaption", session?.accepted ? "Accepted! Keep polishing, or move on." : "I'm here to help whenever you need me.");
    body!.append(node);
  }
  if (["active", "paused", "time_up", "debrief"].includes(view.body) && chat.turns.length) {
    const stream = keptStream || element("section", "chat-stream");
    stream.id = "chat-stream";
    body!.append(stream);
  }
  if (session?.followup && ["active", "paused", "time_up", "debrief"].includes(view.body)) body!.append(followupCard(session.followup));
  if (view.body === "paused") body!.append(block("doze", view.pauseTitle || "Paused", view.pauseText || ""));
  if (view.body === "active" && session?.problem) {
    const actions = element("div", "problem-actions");
    actions.append(session.accepted ? button("next-problem", "Next problem", "btn primary") : button("skip-problem", "Skip this problem", "link"));
    body!.append(actions);
  }
  if (view.body === "time_up") body!.append(timeUpCard());
  if (view.body === "debrief") body!.append(debriefActions());
  if (view.body === "welcome_back") {
    const card = element("section", "card welcome-back");
    appendText(card, "h4", "Pick up where you left off?");
    const minutes = Math.floor((session?.active_seconds || 0) / 60);
    const duration = `${minutes} min`;
    appendText(card, "p", session?.problem?.title ? `${session.problem.title}, ${duration}` : duration);
    card.append(button("welcome-resume", "Resume", "btn primary"), button("end-and-start", "End and start a new one", "link"));
    body!.append(card);
  }
  body!.append(historyRow());
  body!.scrollTop = scrollTop;
  // A note added below the conversation (such as "Paused while you're away") must come into view when you were reading the end.
  if (followEnd) scrollToEnd();
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
tabs.addEventListener("click", event => {
  const id = (event.target as HTMLElement).closest("button")?.id;
  if (id === "tab-session") { activeTab = "session"; settingsOpen = false; render(); }
  if (id === "tab-recaps") { activeTab = "recaps"; settingsOpen = false; void send({ kind: "recaps_get" }); render(); }
  if (id === "tab-progress") {
    activeTab = "progress"; settingsOpen = false;
    void send({ kind: "progress_get" });
    if (!recaps) void send({ kind: "recaps_get" });
    render();
  }
});
async function sendChat(): Promise<void> {
  const input = document.getElementById("chat-input") as HTMLTextAreaElement | null;
  if (!input || input.disabled) return;
  // Typing is allowed while the Tutor answers, sending isn't: the draft stays and the reason shows under the box.
  const streaming = chat.streaming !== null || pacers.size > 0 || awaitingReply();
  const plannerMode = composerMode === "planner";
  if (plannerMode) {
    if (streaming) { updateComposer(); return; }
    const result = messageToSend(input.value);
    composerNote = result.error;
    if (!result.text) { updateComposer(); return; }
    draft = ""; input.value = ""; resizeComposer(input);
    const ok = await send({ kind: "planner_chat", text: result.text });
    if (ok) {
      rememberSuggestion();
      plannerLog.push({ kind: "said", text: result.text });
      beginPlannerWait(); render();
    }
    else composerNote = "The Planner didn't get that.";
    updateComposer();
    return;
  }
  if (!composerAvailability(session?.state, Boolean(session?.problem), streaming).enabled) { updateComposer(); return; }
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
  const categoryRow = (event.target as HTMLElement).closest<HTMLElement>("[data-category]");
  if (categoryRow?.dataset.category) {
    const name = categoryRow.dataset.category;
    correction = null; earlierOpen.clear(); excludeStep = "idle";
    if (openCategory === name) {
      openCategory = null; categoryDetail = undefined;
    } else {
      openCategory = name; categoryDetail = undefined;
      void send({ kind: "progress_category", category: name });
    }
    render();
    return;
  }
  const id = (event.target as HTMLElement).closest("button")?.id;
  if (id?.startsWith("earlier-")) {
    const problem = id.slice("earlier-".length);
    if (earlierOpen.has(problem)) earlierOpen.delete(problem); else earlierOpen.add(problem);
    render();
    return;
  }
  if (id?.startsWith("correct-")) {
    const reviewId = Number(id.slice("correct-".length));
    correction = correction === reviewId ? null : reviewId;
    render();
    return;
  }
  if (id?.startsWith("grade-")) {
    const match = /^grade-(\d+)-(solid|shaky|missed)$/.exec(id);
    if (match) {
      const review_id = Number(match[1]);
      correction = null;
      void send({ kind: "grade_correct", review_id, grade: match[2] });
      render();
    }
    return;
  }
  if (id === "exclude-session-start") { excludeStep = "confirm"; render(); return; }
  if (id === "exclude-session-cancel") { excludeStep = "idle"; render(); return; }
  if (id === "exclude-session-confirm") {
    const sessionId = excludeSessionId;
    excludeStep = "idle";
    if (sessionId !== null) void send({ kind: "session_exclude", session_id: sessionId });
    render();
    return;
  }
  if (id === "reset-progress") { resetStep = "warning"; resetText = ""; render(); return; }
  if (id === "reset-continue") { resetStep = "confirm"; resetText = ""; render(); return; }
  if (id === "reset-cancel") { resetStep = "idle"; resetText = ""; render(); return; }
  if (id === "reset-send") {
    if (resetText !== "RESET") return;
    void send({ kind: "reset_progress", confirm: "RESET" });
    return;
  }
  if (id?.startsWith("recap-close-")) {
    const sessionId = Number(id.slice("recap-close-".length));
    const text = (recapDrafts.get(sessionId) || "").trim();
    if (text) {
      void send({ kind: "recap_feedback", session_id: sessionId, text }).then(ok => { if (ok) { recapDrafts.delete(sessionId); render(); } });
    } else {
      recapHidden.add(sessionId);
      render();
    }
    return;
  }
  if (id === "close-settings") { settingsOpen = false; render(); }
  if (id === "reload-tab" && sessionTabId != null) void chrome.tabs.reload(sessionTabId);
  if (id === "save-first-name") saveName("first-name");
  if (id === "save-settings-name") saveName("settings-name");
  if (id === "skip-name") void send({ kind: "skip_name" });
  if (id === "start-session" && onNeetCode()) void send({ kind: "session_command", command: "start", tabId: panelTabId });
  if (id === "planner-go") void send({ kind: "planner_go", tabId: panelTabId });
  if (id === "planner-swap") { rememberSuggestion(); beginPlannerWait(); render(); void send({ kind: "planner_swap" }); }
  if (id === "planner-clear") { rememberSuggestion(); beginPlannerWait(); render(); void send({ kind: "planner_clear" }); }
  if (id === "planner-retry") { beginPlannerWait(); render(); void send({ kind: "planner_get" }); }
  if (id === "time-up-extend") void send({ kind: "session_command", command: "time_up_extend" });
  if (id === "time-up-debrief") void send({ kind: "session_command", command: "time_up_debrief" });
  if (id === "time-up-skip" || id === "debrief-skip" || id === "skip-problem") void send({ kind: "session_command", command: "skip" });
  if (id === "followup-skip" && session?.followup) void send({ kind: "followup_skip", question_id: session.followup.question_id });
  if (id === "debrief-done" || id === "next-problem") void send({ kind: "session_command", command: "done" });
  if (id?.startsWith("copy-reference-")) {
    const item = referenceState?.references.find(reference => reference.slug === id.slice("copy-reference-".length));
    if (item?.prompt) {
      const copied = navigator.clipboard?.writeText(item.prompt);
      if (!copied) { referenceNotice = "Copy isn't available here."; render(); }
      else void copied.then(() => { referenceNotice = "Prompt copied."; render(); }).catch(error => { referenceNotice = `Could not copy: ${String(error)}`; render(); });
    }
  }
  if (id?.startsWith("reference-apply-")) {
    const slug = id.slice("reference-apply-".length);
    const reply = (referenceDrafts.get(slug) || "").trim();
    if (reply) void send({ kind: "reference_apply", slug, reply }).then(ok => { if (ok) referenceNotice = "Checking the verdict…"; render(); });
  }
  if (id?.startsWith("seems-wrong-")) {
    const turnId = Number(id.slice("seems-wrong-".length));
    if (flagNoted.has(turnId)) return;
    flagTurn = flagTurn === turnId ? null : turnId;
    flagDraft = "";
    renderChat();
  }
  if (id?.startsWith("flag-send-")) {
    const turnId = Number(id.slice("flag-send-".length));
    const footer = (event.target as HTMLElement).closest(".chat-flag");
    const line = (footer?.querySelector<HTMLInputElement>(".flag-line")?.value || "").split("\n")[0].trim().slice(0, 500);
    void send({ kind: "reference_flag", message_id: turnId, line }).then(ok => {
      if (!ok) return;
      flagNoted.add(turnId);
      flagTurn = null; flagDraft = "";
      render();
    });
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
  const command = { "pause-session": "pause", "resume-session": "resume", "end-session": "end" }[id || ""];
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
  if (target.classList.contains("flag-line")) { flagDraft = target.value; return; }
  if (target.id.startsWith("recap-feedback-")) { recapDrafts.set(Number(target.id.slice("recap-feedback-".length)), target.value); return; }
  if (target.id === "reset-confirm") {
    resetText = target.value;
    (document.getElementById("reset-send") as HTMLButtonElement | null)!.disabled = resetText !== "RESET";
    return;
  }
  if (target.id.startsWith("verdict-")) referenceDrafts.set(target.id.slice("verdict-".length), target.value);
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
  if (target.id === "exclude-session") excludeSessionId = Number(target.value);
});
chrome.runtime.onMessage.addListener(message => {
  if (message?.kind === "transport") {
    transport = message.state;
    if (transport.kind === "offline") { settingsOpen = false; clearReaction(); }
    render();
  }
  if (message?.kind === "session_state") {
    session = message.state;
    if (session?.state === "ended" || (session?.state === "active" && session.problem)) { suggestion = undefined; plannerLog = []; }
    if (session?.state === "active" && !session.problem) {
      if (!suggestion && !plannerWaiting()) beginPlannerWait();
    } else endPlannerWait();
    const slug = session?.problem?.slug || null;
    const sessionId = session?.session_id ?? null;
    if (slug !== chatSlug || sessionId !== chatSession) {
      chatSlug = slug; chatSession = sessionId; chat = emptyChat(); pacers.clear();
      if (slug) requestChatState();
    }
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
      nameAcknowledgement = remark(pendingName.pool, { name: pendingName.value, now: new Date() });
      pendingName = null;
      acknowledgementTimer = window.setTimeout(() => { nameAcknowledgement = ""; render(); }, 4000);
    }
    render();
  }
  if (message?.kind === "suggestion") {
    suggestion = message.message || undefined;
    if (suggestion || session?.state !== "active" || session?.problem) endPlannerWait();
    else beginPlannerWait();
    render();
  }
  if (message?.kind === "planner_open") { suggestion = undefined; plannerLog = []; endPlannerWait(); render(); }
  if (message?.kind === "recap_state") { void send({ kind: "recaps_get" }); }
  if (message?.kind === "recaps_state") {
    const state = message.state as RecapsState;
    recaps = state.recaps;
    recapsUnread = state.unread;
    render();
  }
  if (message?.kind === "recap_noted") {
    const noted = message.message as RecapNoted;
    recapNoted.set(noted.session_id, noted.message);
    recapDrafts.delete(noted.session_id);
    render();
  }
  if (message?.kind === "progress_state") {
    progress = message.state as ProgressState;
    if (openCategory) void send({ kind: "progress_category", category: openCategory });
    render();
  }
  if (message?.kind === "progress_category_state") {
    const state = message.state as ProgressCategoryState;
    if (state.category === openCategory) { categoryDetail = state; render(); }
  }
  if (message?.kind === "reset_done") {
    settingsOpen = false;
    activeTab = "session";
    recaps = undefined; recapsUnread = false;
    recapSeenSent.clear(); recapHidden.clear(); recapDrafts.clear(); recapNoted.clear();
    progress = undefined; categoryDetail = undefined; openCategory = null; correction = null;
    earlierOpen.clear(); excludeSessionId = null; excludeStep = "idle";
    resetStep = "idle"; resetText = "";
    pendingName = null; nameAcknowledgement = "";
    imported = false; importResult = "";
    suggestion = undefined; plannerLog = [];
    session = undefined; settings = undefined; actionError = "";
    render();
  }
  if (message?.kind === "reference_state") { referenceState = message.state as ReferenceState; render(); }
  if (message?.kind === "reference_flagged") {
    const turnId = message.message?.message_id;
    if (Number.isInteger(turnId)) { flagNoted.add(turnId); if (flagTurn === turnId) flagTurn = null; }
    render();
  }
  if (message?.kind === "reference_verdict") { referenceNotice = message.message.accepted ? `Reference updated: ${message.message.reason}` : `Verdict refused: ${message.message.reason}`; render(); }
  if (message?.kind === "error") { actionError = message.message; render(); }
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
  chrome.runtime.sendMessage({ kind: "get_suggestion" }),
  chrome.runtime.sendMessage({ kind: "get_recaps_state" }),
  chrome.runtime.sendMessage({ kind: "get_reference_state" }),
]).then(([status, currentSession, currentSettings, error, importState, binding, currentSuggestion, currentRecaps, currentReference]) => {
  transport = status; session = currentSession; settings = currentSettings; readerError = error || "";
  suggestion = currentSuggestion || undefined;
  if (session?.state === "active" && !session.problem && !suggestion) beginPlannerWait();
  if (currentRecaps) { recaps = currentRecaps.recaps; recapsUnread = currentRecaps.unread; }
  referenceState = currentReference || undefined;
  imported = importState?.imported || false; if (importState?.result) importResult = importState.result.already_imported ? "NeetCode history was already imported." : `Imported ${importState.result.matched} solved problems.`;
  receivedAt = Date.now(); void refreshBinding(binding?.tabId ?? null);
}).catch(error => { transport = { kind: "error", message: String(error) }; render(); });
window.setInterval(updateTimer, 1000);
window.setInterval(decorateOtters, 10 * 60 * 1000);

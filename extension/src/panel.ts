import type { ConnectionState } from "./connection";

const status = document.getElementById("status");
const readerError = document.getElementById("reader-error");
const readerActivity = document.getElementById("reader-activity");
const testButton = document.getElementById("reader-test");
if (!status) throw new Error("Missing status element");
if (!readerError || !testButton) throw new Error("Missing reader controls");

let currentMode = "idle";
function showMode(mode: string): void {
  currentMode = mode;
  if (testButton) testButton.textContent = mode === "watching" ? "Reader test: stop watching" : "Reader test: start watching";
}
testButton.addEventListener("click", () => {
  const next = currentMode === "watching" ? "idle" : "watching";
  chrome.runtime.sendMessage({ kind: "set_mode", mode: next });
  showMode(next);
});

function show(state: ConnectionState): void {
  if (!status) return;
  if (state.kind === "offline") status.textContent = "Tutor service isn't running. Start it with uv run python -m companion";
  else if (state.kind === "error") status.textContent = `Tutor service error: ${state.message}`;
  else status.textContent = "Tutor service connected.";
}

show({ kind: "offline" });
chrome.runtime.onMessage.addListener((message) => {
  if (message?.kind === "transport") show(message.state as ConnectionState);
  if (message?.kind === "watch_mode") showMode(message.mode);
  if (message?.kind === "reader_error" && readerError) readerError.textContent = message.message;
  if (message?.kind === "reader_activity" && readerActivity) readerActivity.textContent = message.message;
});
chrome.runtime.sendMessage({ kind: "get_status" }).then((state: ConnectionState) => show(state));
chrome.runtime.sendMessage({ kind: "get_mode" }).then(showMode);
chrome.runtime.sendMessage({ kind: "get_reader_error" }).then(message => { if (readerError) readerError.textContent = message || ""; });
chrome.runtime.sendMessage({ kind: "get_reader_activity" }).then(message => { if (readerActivity) readerActivity.textContent = message || "No reader events yet."; });

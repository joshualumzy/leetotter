import type { ConnectionState } from "./connection";

const status = document.getElementById("status");
if (!status) throw new Error("Missing status element");

function show(state: ConnectionState): void {
  if (!status) return;
  if (state.kind === "offline") status.textContent = "Tutor service isn't running. Start it with uv run python -m companion";
  else if (state.kind === "error") status.textContent = `Tutor service error: ${state.message}`;
  else status.textContent = "Tutor service connected.";
}

show({ kind: "offline" });
chrome.runtime.onMessage.addListener((message) => {
  if (message?.kind === "transport") show(message.state as ConnectionState);
});
chrome.runtime.sendMessage({ kind: "get_status" }).then((state: ConnectionState) => show(state));

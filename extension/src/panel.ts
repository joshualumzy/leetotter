import type { ConnectionState } from "./connection";

const status = document.getElementById("status");
const readerError = document.getElementById("reader-error");
const readerActivity = document.getElementById("reader-activity");
const importButton = document.getElementById("import-history");
const importResult = document.getElementById("import-result");
if (!status) throw new Error("Missing status element");
if (!readerError) throw new Error("Missing reader controls");
if (!importButton || !importResult) throw new Error("Missing import controls");

function showImport(imported: boolean, result?: { already_imported: boolean; matched: number; unmatched: string[] }): void {
  if (!importButton || !importResult) return;
  importButton.hidden = imported;
  if (result) importResult.textContent = result.already_imported
    ? "NeetCode history was already imported."
    : `Imported ${result.matched} solved problems.${result.unmatched.length ? ` Unmatched URLs: ${result.unmatched.join(", ")}` : ""}`;
}
importButton.addEventListener("click", async () => {
  if (!importResult || !importButton) return;
  importButton.setAttribute("disabled", "");
  try {
    const reply = await chrome.runtime.sendMessage({ kind: "import_history" });
    importResult.textContent = reply?.error || "Importing NeetCode history…";
  } catch (error) {
    importResult.textContent = `History import failed: ${(error as Error).message}`;
  } finally { importButton.removeAttribute("disabled"); }
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
  if (message?.kind === "reader_error" && readerError) readerError.textContent = message.message;
  if (message?.kind === "reader_activity" && readerActivity) readerActivity.textContent = message.message;
  if (message?.kind === "import_state") showImport(message.imported, message.result);
  if (message?.kind === "import_error" && importResult) importResult.textContent = message.message;
});
chrome.runtime.sendMessage({ kind: "get_status" }).then((state: ConnectionState) => show(state));
chrome.runtime.sendMessage({ kind: "get_reader_error" }).then(message => { if (readerError) readerError.textContent = message || ""; });
chrome.runtime.sendMessage({ kind: "get_reader_activity" }).then(message => { if (readerActivity) readerActivity.textContent = message || "No reader events yet."; });
chrome.runtime.sendMessage({ kind: "get_import_state" }).then(state => showImport(state.imported, state.result));

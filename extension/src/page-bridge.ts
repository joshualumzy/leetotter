const channel = "leetotter-neetcode-reader-v1";
let currentMode: unknown;
function pageMode(mode: unknown): void {
  if (["idle", "watching", "paused"].includes(mode as string) && mode !== currentMode) {
    currentMode = mode;
    window.postMessage({ channel, direction: "to_page", mode }, location.origin);
  }
}
async function relay(item: unknown): Promise<void> {
  try {
    const reply = await chrome.runtime.sendMessage({ kind: "reader_event", event: item });
    if (!reply || typeof reply.accepted !== "boolean") throw new Error("No reader acknowledgement");
    if (reply?.mode) pageMode(reply.mode);
  } catch {
    try {
      pageMode(await chrome.runtime.sendMessage({ kind: "get_mode" }));
      const reply = await chrome.runtime.sendMessage({ kind: "reader_event", event: item });
      if (reply?.mode) pageMode(reply.mode);
    } catch { /* The worker reports delivery errors when it can receive messages again. */ }
  }
}
window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel || event.data.direction !== "to_extension") return;
  const item = event.data.event;
  if (!item || typeof item !== "object" || typeof item.type !== "string" || !item.payload || typeof item.payload !== "object") return;
  void relay(item);
});
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message?.kind === "watch_mode") pageMode(message.mode);
  if (message?.kind === "read_history") {
    const requestId = crypto.randomUUID();
    const timer = setTimeout(() => { window.removeEventListener("message", onResult); respond({ error: "synced-progress-cache reader did not respond" }); }, 5000);
    function onResult(event: MessageEvent): void {
      if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel || event.data.direction !== "to_extension" || event.data.kind !== "history_result" || event.data.requestId !== requestId) return;
      clearTimeout(timer); window.removeEventListener("message", onResult);
      respond(event.data.error ? { error: event.data.error } : { urls: event.data.urls });
    }
    window.addEventListener("message", onResult);
    window.postMessage({ channel, direction: "to_page", kind: "read_history", requestId }, location.origin);
    return true;
  }
});
chrome.runtime.sendMessage({ kind: "get_mode" }).then(mode => {
  pageMode(mode);
});

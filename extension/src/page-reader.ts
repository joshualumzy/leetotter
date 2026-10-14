import { NeetCodeReader, type WatchMode } from "./reader";
import { readHistoryCache } from "./history";

const channel = "leetotter-neetcode-reader-v1";
const reader = new NeetCodeReader(event => window.postMessage({ channel, direction: "to_extension", event }, location.origin));
window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel || event.data.direction !== "to_page") return;
  if (["idle", "watching", "paused"].includes(event.data.mode)) reader.setMode(event.data.mode as WatchMode);
  if (event.data.kind === "read_history" && typeof event.data.requestId === "string") {
    try {
      const urls = readHistoryCache(localStorage.getItem("synced-progress-cache"));
      window.postMessage({ channel, direction: "to_extension", kind: "history_result", requestId: event.data.requestId, urls }, location.origin);
    } catch (error) {
      window.postMessage({ channel, direction: "to_extension", kind: "history_result", requestId: event.data.requestId, error: (error as Error).message }, location.origin);
    }
  }
});
reader.install();

import { NeetCodeReader, type WatchMode } from "./reader";

const channel = "leetotter-neetcode-reader-v1";
const reader = new NeetCodeReader(event => window.postMessage({ channel, direction: "to_extension", event }, location.origin));
window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel || event.data.direction !== "to_page") return;
  if (["idle", "watching", "paused"].includes(event.data.mode)) reader.setMode(event.data.mode as WatchMode);
});
reader.install();

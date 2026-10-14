const channel = "leetotter-neetcode-reader-v1";
window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel || event.data.direction !== "to_extension") return;
  const item = event.data.event;
  if (!item || typeof item !== "object" || typeof item.type !== "string" || !item.payload || typeof item.payload !== "object") return;
  chrome.runtime.sendMessage({ kind: "reader_event", event: item }).catch(() => {});
});
chrome.runtime.onMessage.addListener(message => {
  if (message?.kind === "watch_mode") window.postMessage({ channel, direction: "to_page", mode: message.mode }, location.origin);
});
chrome.runtime.sendMessage({ kind: "get_mode" }).then(mode => {
  window.postMessage({ channel, direction: "to_page", mode }, location.origin);
});

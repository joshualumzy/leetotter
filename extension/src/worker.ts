import { CompanionConnection, type ConnectionState } from "./connection";

declare const __LEETOTTER_PORT__: number;

let state: ConnectionState = { kind: "offline" };
const connection = new CompanionConnection(
  `ws://127.0.0.1:${__LEETOTTER_PORT__}`,
  (url) => new WebSocket(url),
  (next) => {
    state = next;
    chrome.runtime.sendMessage({ kind: "transport", state: next }).catch(() => {
      // The side panel may be closed; it asks for current state when opened.
    });
  },
);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.kind === "get_status") sendResponse(state);
});

connection.start();

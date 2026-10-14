import { beforeEach, describe, expect, it, vi } from "vitest";

type Listener = (...args: any[]) => any;
const messages: Listener[] = [];
const activated: Listener[] = [];
const updated: Listener[] = [];
const removed: Listener[] = [];
const focused: Listener[] = [];
const sent: { tab: number; message: any }[] = [];
let saved: Record<string, any> = {};
let socket: FakeSocket;

class FakeSocket {
  readyState = 0;
  onopen: Listener | null = null;
  onclose: Listener | null = null;
  onerror: Listener | null = null;
  onmessage: Listener | null = null;
  sent: any[] = [];
  constructor(_url: string) { socket = this; }
  send(raw: string) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.onclose?.(); }
}

async function dispatch(message: any, tab?: any): Promise<any> {
  return new Promise(resolve => {
    const returned = messages.at(-1)?.(message, { tab }, resolve);
    if (returned !== true) resolve(undefined);
  });
}
async function flush() { await vi.waitFor(() => { expect(messages.length).toBeGreaterThan(0); }); }
async function start() {
  vi.resetModules();
  await import("./worker");
  await flush();
  await dispatch({ kind: "get_mode" });
}

beforeEach(() => {
  vi.useRealTimers();
  messages.length = activated.length = updated.length = removed.length = focused.length = sent.length = 0;
  saved = {};
  vi.stubGlobal("__LEETOTTER_PORT__", 8765);
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("chrome", {
    storage: { session: { get: vi.fn(async () => ({ readerState: saved.readerState })), set: vi.fn(async (value: any) => { saved = { ...saved, ...value }; }) } },
    runtime: { onMessage: { addListener: (fn: Listener) => messages.push(fn) }, sendMessage: vi.fn(async () => {}) },
    tabs: {
      query: vi.fn(async () => [{ id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" }]),
      sendMessage: vi.fn(async (tab: number, message: any) => { sent.push({ tab, message }); }),
      onActivated: { addListener: (fn: Listener) => activated.push(fn) },
      onUpdated: { addListener: (fn: Listener) => updated.push(fn) },
      onRemoved: { addListener: (fn: Listener) => removed.push(fn) },
    },
    windows: { WINDOW_ID_NONE: -1, getLastFocused: vi.fn(async () => ({ id: 3, focused: true })), onFocusChanged: { addListener: (fn: Listener) => focused.push(fn) } },
  });
});

describe("worker reader delivery", () => {
  it("restores watching and pushes the mode after a simulated worker restart", async () => {
    await start();
    await dispatch({ kind: "set_mode", mode: "watching" });
    expect(saved.readerState.mode).toBe("watching");
    messages.length = 0;
    await start();
    expect(await dispatch({ kind: "get_mode" })).toBe("watching");
    expect(sent).toContainEqual({ tab: 7, message: { kind: "watch_mode", mode: "watching" } });
  });

  it("surfaces a dropped send and records activity only after delivery", async () => {
    await start(); await dispatch({ kind: "set_mode", mode: "watching" });
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    const event = { kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } };
    expect((await dispatch(event, tab)).accepted).toBe(false);
    expect(await dispatch({ kind: "get_reader_activity" })).toBe("No reader events yet.");
    expect(await dispatch({ kind: "get_reader_error" })).toContain("companion");
    socket.readyState = 1;
    expect((await dispatch(event, tab)).accepted).toBe(true);
    expect(await dispatch({ kind: "get_reader_activity" })).toBe("Last reader event: focus_changed");
    expect(socket.sent.map(item => item.type)).toEqual(["problem_opened", "focus_changed"]);
  });

  it("drops stale focus entries after navigating away", async () => {
    await start(); await dispatch({ kind: "set_mode", mode: "watching" });
    socket.readyState = 1;
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    await dispatch({ kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } }, tab);
    const before = socket.sent.length;
    updated.at(-1)?.(7, { url: "https://neetcode.io/practice" });
    await vi.waitFor(() => expect(saved.readerState.problemTabs).toHaveLength(0));
    focused.at(-1)?.(-1);
    await dispatch({ kind: "get_mode" });
    expect(socket.sent).toHaveLength(before);
  });

  it("waits for problem_opened after navigation to another problem", async () => {
    await start(); await dispatch({ kind: "set_mode", mode: "watching" });
    socket.readyState = 1;
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    await dispatch({ kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } }, tab);
    const before = socket.sent.length;
    updated.at(-1)?.(7, { url: "https://neetcode.io/problems/two-sum/" });
    await vi.waitFor(() => expect(saved.readerState.problemTabs).toHaveLength(0));
    focused.at(-1)?.(-1);
    await dispatch({ kind: "get_mode" });
    expect(socket.sent).toHaveLength(before);
  });

  it("reports focus once per switch and only after a problem opens", async () => {
    await start(); await dispatch({ kind: "set_mode", mode: "watching" });
    socket.readyState = 1;
    activated.at(-1)?.({ tabId: 7, windowId: 3 });
    await vi.waitFor(() => expect(socket.sent).toHaveLength(0));
    const tab = { id: 7, windowId: 3, active: true, url: "https://neetcode.io/problems/duplicate-integer/" };
    await dispatch({ kind: "reader_event", event: { type: "problem_opened", payload: { site: "neetcode", slug: "duplicate-integer", title: "Duplicate Integer" } } }, tab);
    expect(socket.sent.map(item => item.type)).toEqual(["problem_opened", "focus_changed"]);
    activated.at(-1)?.({ tabId: 8, windowId: 3 });
    await vi.waitFor(() => expect(socket.sent).toHaveLength(3));
    activated.at(-1)?.({ tabId: 8, windowId: 3 });
    await vi.waitFor(() => expect(socket.sent).toHaveLength(3));
    expect(socket.sent.at(-1).payload).toEqual({ tab_active: false, window_focused: true });
  });
});

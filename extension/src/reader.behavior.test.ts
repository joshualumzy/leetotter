// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://neetcode.io/problems/duplicate-integer/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JSDOM } from "jsdom";
import { NeetCodeReader, type ReaderEvent } from "./reader";
import runRequest from "./fixtures/run_request.json";
import runResponse from "./fixtures/run_accepted_response.json";
import submitRequest from "./fixtures/submit_request.json";
import submitResponse from "./fixtures/submit_accepted_response.json";

let reader: NeetCodeReader;
let events: ReaderEvent[];
let page: Window;
let dom: JSDOM;
beforeEach(() => {
  vi.useFakeTimers();
  events = [];
  dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://neetcode.io/problems/duplicate-integer/" });
  page = dom.window as unknown as Window;
  page.fetch = vi.fn(() => new Promise<Response>(() => {}));
  reader = new NeetCodeReader(event => events.push(event), page);
});
afterEach(() => { reader.dispose(); dom.window.close(); vi.useRealTimers(); });

function ready() {
  page.document.body.innerHTML = "<h1>Duplicate Integer</h1><div class='monaco-editor'></div>";
  page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one\ntwo" }]));
}

describe("page reader", () => {
  it("names each failing self-check, including two tabs and a missing hook", () => {
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't identify the problem title on this page");
    page.document.body.innerHTML = "<h1>Duplicate Integer</h1>";
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't find the editor on this page");
    page.document.body.innerHTML += "<div class='monaco-editor'></div>";
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't read the editor on this page");
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one" }, { code: "two" }]));
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't read the editor on this page");
    ready();
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't see run results on this page");
  });

  it("gates events by mode and sends a line diff at most once per three seconds", () => {
    ready(); reader.install(); reader.open();
    expect(events.some(e => e.type === "problem_opened")).toBe(false);
    reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened")).toHaveLength(1);
    expect(events.find(e => e.type === "code_snapshot")?.payload.code).toBe("one\ntwo");
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one\nthree" }]));
    vi.advanceTimersByTime(1);
    expect(events.find(e => e.type === "code_changed")?.payload.diff).toEqual({ start_line: 2, removed: ["two"], added: ["three"] });
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one\nfour" }]));
    vi.advanceTimersByTime(2998);
    expect(events.filter(e => e.type === "code_changed")).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(events.filter(e => e.type === "code_changed")).toHaveLength(2);
    reader.setMode("paused");
    const count = events.length;
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "five" }]));
    vi.advanceTimersByTime(60_000);
    expect(events.slice(count).filter(e => e.type === "code_changed" || e.type === "code_snapshot")).toHaveLength(0);
    reader.focus();
    expect(events.at(-1)?.type).toBe("focus_changed");
    reader.setMode("idle");
    const idleCount = events.length;
    reader.focus(); reader.snapshot();
    expect(events).toHaveLength(idleCount);
  });

  it("sends a periodic full snapshot while watching", () => {
    ready(); reader.install(); reader.setMode("watching");
    const count = events.filter(e => e.type === "code_snapshot").length;
    vi.advanceTimersByTime(60_000);
    expect(events.filter(e => e.type === "code_snapshot")).toHaveLength(count + 1);
  });

  it("shows a named failure when a Run response does not arrive within 30 seconds", () => {
    ready(); reader.install(); reader.setMode("watching");
    void page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    expect(events.at(-1)?.type).toBe("code_snapshot");
    vi.advanceTimersByTime(29_999);
    expect(events.some(e => e.payload.message === "Can't see run results on this page")).toBe(false);
    vi.advanceTimersByTime(1);
    expect(events.at(-1)?.payload.message).toBe("Can't see run results on this page");
  });

  it("observes fetch responses and lets only Submit results through while paused", async () => {
    ready();
    page.fetch = vi.fn(async (url: string | URL | Request) => new Response(JSON.stringify(String(url).includes("executeCode") ? submitResponse : runResponse)));
    reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    for (let i = 0; i < 20; i++) await Promise.resolve();
    expect(events.some(e => e.type === "run_result")).toBe(true);
    reader.setMode("paused");
    await page.fetch("/api/executeCodeFunctionHttp", { method: "POST", body: JSON.stringify(submitRequest) });
    for (let i = 0; i < 20; i++) await Promise.resolve();
    expect(events.some(e => e.type === "submit_result")).toBe(true);
    const runCount = events.filter(e => e.type === "run_result").length;
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    for (let i = 0; i < 20; i++) await Promise.resolve();
    expect(events.filter(e => e.type === "run_result")).toHaveLength(runCount);
    reader.setMode("idle");
    const submitCount = events.filter(e => e.type === "submit_result").length;
    await page.fetch("/api/executeCodeFunctionHttp", { method: "POST", body: JSON.stringify(submitRequest) });
    for (let i = 0; i < 20; i++) await Promise.resolve();
    expect(events.filter(e => e.type === "submit_result")).toHaveLength(submitCount);
  });
});

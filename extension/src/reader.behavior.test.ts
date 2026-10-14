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
  page.document.body.innerHTML = "<h1>Contains Duplicate</h1><div class='monaco-editor'></div>";
  page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one\ntwo" }]));
}
async function flushUntil(test: () => boolean) { await vi.waitFor(() => { expect(test()).toBe(true); }); }

describe("page reader", () => {
  it("emits nothing while idle and cancels pending result checks on pause", () => {
    reader.install();
    vi.advanceTimersByTime(3000);
    expect(events).toEqual([]);
    ready(); reader.setMode("watching");
    void page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    reader.setMode("paused");
    const count = events.length;
    vi.advanceTimersByTime(30000);
    expect(events).toHaveLength(count);
  });

  it("recovers from a failed editor check with a snapshot and no duplicate open", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.localStorage.setItem("duplicate-integer_python_tabs", "[]");
    expect(events.at(-1)?.payload.ok).toBe(false);
    reader.setMode("paused");
    ready(); reader.setMode("watching");
    expect(events.at(-2)?.payload.ok).toBe(true);
    expect(events.at(-1)?.type).toBe("code_snapshot");
    expect(events.filter(e => e.type === "problem_opened")).toHaveLength(1);
  });

  it("does not read unrelated fetch bodies", async () => {
    page.fetch = vi.fn(async () => new Response("ok"));
    ready(); reader.install(); reader.setMode("watching");
    const request = new Request("https://neetcode.io/api/other", { method: "POST", body: "unrelated" });
    vi.spyOn(request, "clone").mockImplementation(() => { throw new Error("unrelated body read"); });
    await expect(page.fetch(request)).resolves.toBeDefined();
  });

  it("reports a non-string matched request body", async () => {
    page.fetch = vi.fn(async () => new Response("ok"));
    ready(); reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: new Blob(["{} "]) });
    expect(events.at(-1)?.payload.message).toBe("Can't read run request body on this page");
  });

  it("includes HTTP status for a failed matched response", async () => {
    page.fetch = vi.fn(async () => new Response("bad", { status: 503 }));
    ready(); reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.some(e => String(e.payload.message).includes("HTTP 503")));
    expect(events.at(-1)?.payload.message).toContain("HTTP 503");
  });

  it("includes a parse failure and clears it on the next result", async () => {
    page.fetch = vi.fn()
      .mockResolvedValueOnce(new Response("not json"))
      .mockResolvedValueOnce(new Response(JSON.stringify(runResponse)));
    ready(); reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.some(e => String(e.payload.message).includes("Can't see run results on this page:")));
    expect(String(events.at(-1)?.payload.message)).toMatch(/JSON|Unexpected token/);
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.some(e => e.type === "run_result"));
    expect(events.at(-2)?.payload.ok).toBe(true);
  });

  it("keeps an in-flight Submit result when watching pauses", async () => {
    let complete!: (value: Response) => void;
    page.fetch = vi.fn(() => new Promise<Response>(resolve => { complete = resolve; }));
    ready(); reader.install(); reader.setMode("watching");
    const request = page.fetch("/api/executeCodeFunctionHttp", { method: "POST", body: JSON.stringify(submitRequest) });
    reader.setMode("paused");
    vi.advanceTimersByTime(30_000);
    expect(events.some(e => String(e.payload.message).startsWith("Can't see run results on this page ("))).toBe(false);
    complete(new Response(JSON.stringify(submitResponse)));
    await request;
    await flushUntil(() => events.some(e => e.type === "submit_result"));
  });

  it("clears a missing-result check when that response arrives late", async () => {
    let complete!: (value: Response) => void;
    page.fetch = vi.fn(() => new Promise<Response>(resolve => { complete = resolve; }));
    ready(); reader.install(); reader.setMode("watching");
    const request = page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    vi.advanceTimersByTime(30_000);
    expect(String(events.at(-1)?.payload.message)).toContain("Can't see run results on this page (");
    complete(new Response(JSON.stringify(runResponse)));
    await request;
    await flushUntil(() => events.some(e => e.type === "run_result"));
    expect(events.at(-2)?.payload.ok).toBe(true);
  });

  it("does not announce the previous title during SPA navigation", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.history.pushState({}, "", "/problems/two-sum/");
    page.localStorage.setItem("two-sum_python_tabs", JSON.stringify([{ code: "next" }]));
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_opened")).toHaveLength(1);
    page.document.querySelector("h1")!.textContent = "Two Sum";
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_opened").at(-1)?.payload).toMatchObject({ slug: "two-sum", title: "Two Sum" });
  });

  it("announces a new route after a title check recovers from pause", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.history.pushState({}, "", "/problems/two-sum/");
    vi.advanceTimersByTime(1000);
    reader.setMode("paused");
    page.document.querySelector("h1")!.textContent = "Two Sum";
    page.localStorage.setItem("two-sum_python_tabs", JSON.stringify([{ code: "next" }]));
    vi.advanceTimersByTime(1000);
    reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened").map(e => e.payload.slug)).toEqual(["duplicate-integer", "two-sum"]);
  });

  it("recovers a failed check while paused without opening again", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.localStorage.setItem("duplicate-integer_python_tabs", "[]");
    reader.setMode("paused");
    ready(); vi.advanceTimersByTime(1000);
    expect(events.at(-1)?.payload.ok).toBe(true);
    reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened")).toHaveLength(1);
  });

  it("names a failed editor check from the diff timer", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "changed" }]));
    // The write schedules a diff; the stored tabs disappear before its timer fires.
    page.localStorage.removeItem("duplicate-integer_python_tabs");
    vi.advanceTimersByTime(1);
    expect(events.at(-1)?.payload.message).toBe("Can't read the editor on this page");
  });
  it("names each failing self-check, including two tabs and a missing hook", () => {
    reader.setMode("watching");
    expect(events.at(-1)?.payload.message).toBe("Can't identify the problem title on this page");
    page.document.body.innerHTML = "<h1>Contains Duplicate</h1>";
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
    expect(String(events.at(-1)?.payload.message)).toContain("Can't see run results on this page (");
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
    reader.setMode("idle");
    const idleCount = events.length;
    reader.snapshot();
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
    expect(events.some(e => String(e.payload.message).startsWith("Can't see run results on this page ("))).toBe(false);
    vi.advanceTimersByTime(1);
    expect(String(events.at(-1)?.payload.message)).toContain("Can't see run results on this page (");
  });

  it("observes fetch responses and lets only Submit results through while paused", async () => {
    ready();
    page.fetch = vi.fn(async (url: string | URL | Request) => new Response(JSON.stringify(String(url).includes("executeCode") ? submitResponse : runResponse)));
    reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.some(e => e.type === "run_result"));
    expect(events.some(e => e.type === "run_result")).toBe(true);
    reader.setMode("paused");
    await page.fetch("/api/executeCodeFunctionHttp", { method: "POST", body: JSON.stringify(submitRequest) });
    await flushUntil(() => events.some(e => e.type === "submit_result"));
    expect(events.some(e => e.type === "submit_result")).toBe(true);
    const runCount = events.filter(e => e.type === "run_result").length;
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.filter(e => e.type === "run_result").length === runCount);
    expect(events.filter(e => e.type === "run_result")).toHaveLength(runCount);
    reader.setMode("idle");
    const submitCount = events.filter(e => e.type === "submit_result").length;
    await page.fetch("/api/executeCodeFunctionHttp", { method: "POST", body: JSON.stringify(submitRequest) });
    await flushUntil(() => events.filter(e => e.type === "submit_result").length === submitCount);
    expect(events.filter(e => e.type === "submit_result")).toHaveLength(submitCount);
  });
});

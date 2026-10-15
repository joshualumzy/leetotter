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
  page.document.body.innerHTML = `<h1>Contains Duplicate</h1><div class="editor-shell"><div class="my-code-editor">
    <div class="monaco-editor no-user-select mac vs-dark"><div class="view-lines"><div class="view-line" style="top: 0px"></div></div></div>
  </div></div>`;
  page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one\ntwo" }]));
}
function diffEditors() {
  return `<div class="monaco-diff-editor side-by-side">
    <div class="editor original"><div class="monaco-editor original-in-monaco-diff-editor"><div class="view-lines"><div class="view-line" style="top: 0px"></div></div></div></div>
    <div class="editor modified"><div class="monaco-editor modified-in-monaco-diff-editor"><div class="view-lines"><div class="view-line" style="top: 0px"></div></div></div></div>
    <div class="monaco-editor gutter"><div class="view-lines"><div class="view-line" style="top: 0px"></div></div></div>
  </div>`;
}
function questionTab(statement: string) {
  const tab = page.document.createElement("div");
  tab.className = "question-tab";
  tab.innerHTML = '<div class="tab-content-padding"><h2>Contains Duplicate</h2><span>Easy</span><div>Topics</div><div>Company Tags</div><div>Hints</div><div class="my-div"><div></div></div></div>';
  tab.querySelector(".my-div > div")!.textContent = statement;
  page.document.body.append(tab);
  return tab;
}
async function flushUntil(test: () => boolean) { await vi.waitFor(() => { expect(test()).toBe(true); }); }

describe("page reader", () => {
  it("reads untouched starter code from Monaco lines in visual order", () => {
    page.history.pushState({}, "", "/problems/implement-prefix-tree/");
    const code = [
      "class PrefixTree:", "    def __init__(self):", "        self.root = {}", "", "    def insert(self, word):",
      "        node = self.root", "        for char in word:", "            node = node.setdefault(char, {})",
      "        node['#'] = True", "", "    def search(self, word):", "        return False", "",
      "    def startsWith(self, prefix):",
    ];
    const lines = [2, 0, 3, 1, ...Array.from({ length: 10 }, (_, i) => i + 4)]
      .map(i => `<div class="view-line" style="top: ${i * 24}px">${code[i]}</div>`).join("");
    page.document.body.innerHTML = `<h1>Implement Prefix Tree</h1><div class="editor-shell"><div class="my-code-editor"><div class="monaco-editor no-user-select mac vs-dark"><div class="view-lines">${lines}</div></div>${diffEditors()}</div></div>`;
    reader.install(); reader.setMode("watching");
    expect(events.find(e => e.type === "reader_check")?.payload).toEqual({ ok: true });
    expect(events.find(e => e.type === "code_snapshot")?.payload.code).toBe(
      "class PrefixTree:\n    def __init__(self):\n        self.root = {}\n\n    def insert(self, word):\n        node = self.root\n        for char in word:\n            node = node.setdefault(char, {})\n        node['#'] = True\n\n    def search(self, word):\n        return False\n\n    def startsWith(self, prefix):",
    );
  });
  it("cannot find a code editor when the page has only diff editor pieces", () => {
    page.document.body.innerHTML = `<h1>Contains Duplicate</h1><div class="my-code-editor">${diffEditors()}</div>`;
    reader.install(); reader.setMode("watching");
    expect(events.at(-1)?.payload.message).toBe("Can't find the editor on this page");
  });
  it("rejects a broken stored value even when Monaco lines are readable", () => {
    page.document.body.innerHTML = '<h1>Contains Duplicate</h1><div class="my-code-editor"><div class="monaco-editor"><div class="view-lines"><div class="view-line" style="top: 0px">starter</div></div></div></div>';
    reader.install();
    for (const [value, reason] of [
      ["invalid json", "stored code isn't valid JSON"],
      ["{}", "stored code isn't an editor tabs list"],
      ["[]", "stored code has 0 editor tabs, expected 1"],
      ['[{"code":"one"},{"code":"two"}]', "stored code has 2 editor tabs, expected 1"],
      ['[{"code":123}]', "stored code has no text"],
      ['[{"code":""}]', "stored code has no text"],
    ]) {
      page.localStorage.setItem("duplicate-integer_python_tabs", value);
      reader.setMode("watching");
      expect(events.at(-1)).toEqual({ type: "reader_check", payload: { ok: false, message: `Can't read the editor on this page (${reason})` } });
      reader.setMode("idle");
    }
    expect(events.some(e => e.type === "code_snapshot")).toBe(false);
  });
  it("rejects an absent key when no Monaco line text is available", () => {
    page.document.body.innerHTML = '<h1>Contains Duplicate</h1><div class="my-code-editor"><div class="monaco-editor"><div class="view-lines"><div class="view-line" style="top: 0px"></div></div></div></div>';
    reader.install(); reader.setMode("watching");
    expect(events.at(-1)).toEqual({ type: "reader_check", payload: { ok: false, message: "Can't read the editor on this page (no code found in the editor)" } });
  });
  it("snapshots each source switch and diffs only within the new source", () => {
    page.document.body.innerHTML = '<h1>Contains Duplicate</h1><div class="my-code-editor"><div class="monaco-editor"><div class="view-lines"><div class="view-line" style="top: 0px">starter</div></div></div></div>';
    reader.install(); reader.setMode("watching");
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "stored" }]));
    expect(events.filter(e => e.type === "code_snapshot").map(e => e.payload.code)).toEqual(["starter", "stored"]);
    expect(events.filter(e => e.type === "code_changed")).toHaveLength(0);
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "stored edit" }]));
    vi.advanceTimersByTime(1);
    expect(events.filter(e => e.type === "code_changed").at(-1)?.payload.diff).toEqual({ start_line: 1, removed: ["stored"], added: ["stored edit"] });

    page.localStorage.removeItem("duplicate-integer_python_tabs");
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "code_snapshot").map(e => e.payload.code)).toEqual(["starter", "stored", "starter"]);
    page.document.querySelector(".view-line")!.textContent = "starter edit";
    vi.advanceTimersByTime(2000);
    expect(events.filter(e => e.type === "code_changed").at(-1)?.payload.diff).toEqual({ start_line: 1, removed: ["starter"], added: ["starter edit"] });
  });
  it("reads the Question tab statement when it appears on a later poll, without treating Solution as a failure", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.document.body.insertAdjacentHTML("beforeend", '<div class="solution-tab">Solution</div>');
    vi.advanceTimersByTime(3000);
    expect(events.filter(e => e.type === "problem_statement")).toEqual([]);
    expect(events.some(e => e.type === "reader_check" && e.payload.ok === false)).toBe(false);

    questionTab("  Given an integer array nums, return true if any value appears more than once.\n\nExample 1: nums = [1,2,1]  ");
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_statement")).toEqual([{ type: "problem_statement", payload: {
      slug: "duplicate-integer", statement: "Given an integer array nums, return true if any value appears more than once.\n\nExample 1: nums = [1,2,1]",
    } }]);
  });
  it("emits a statement once until its text changes across several polls", () => {
    ready(); const tab = questionTab("First statement"); reader.install(); reader.setMode("watching");
    vi.advanceTimersByTime(4000);
    expect(events.filter(e => e.type === "problem_statement")).toEqual([
      { type: "problem_statement", payload: { slug: "duplicate-integer", statement: "First statement" } },
    ]);
    tab.querySelector(".my-div > div")!.textContent = "Revised statement";
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_statement").at(-1)?.payload).toEqual({ slug: "duplicate-integer", statement: "Revised statement" });
    expect(events.filter(e => e.type === "problem_statement")).toHaveLength(2);
  });
  it("sends the statement again when a new session starts on the same problem", () => {
    ready(); questionTab("Same problem statement"); reader.install(); reader.setMode("watching");
    vi.advanceTimersByTime(1000);
    reader.setMode("idle");
    reader.setMode("watching");
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_statement")).toHaveLength(2);
  });
  it("waits for a new statement after a checked SPA navigation leaves the old statement in place", () => {
    ready(); const tab = questionTab("Old problem statement"); reader.install(); reader.setMode("watching");
    vi.advanceTimersByTime(1000);
    page.history.pushState({}, "", "/problems/two-sum/");
    page.localStorage.setItem("two-sum_python_tabs", JSON.stringify([{ code: "next" }]));
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_statement")).toHaveLength(1);
    page.document.querySelector("h1")!.textContent = "Two Sum";
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_opened").at(-1)?.payload.slug).toBe("two-sum");
    expect(events.filter(e => e.type === "problem_statement")).toHaveLength(1);
    tab.querySelector(".my-div > div")!.textContent = "New problem statement";
    vi.advanceTimersByTime(1000);
    expect(events.filter(e => e.type === "problem_statement").at(-1)?.payload).toEqual({ slug: "two-sum", statement: "New problem statement" });
  });
  it("caps a long statement and marks the payload as truncated", () => {
    ready(); questionTab("x".repeat(20_001)); reader.install(); reader.setMode("watching");
    vi.advanceTimersByTime(1000);
    expect(events.find(e => e.type === "problem_statement")?.payload).toEqual({
      slug: "duplicate-integer", statement: "x".repeat(20_000), truncated: true,
    });
  });
  it("announces an already-open problem again for a new session, but not after pause", () => {
    ready(); reader.install(); reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened")).toHaveLength(1);

    reader.setMode("paused"); reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened")).toHaveLength(1);

    reader.setMode("idle"); reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened").map(e => e.payload)).toEqual([
      { site: "neetcode", slug: "duplicate-integer", title: "Contains Duplicate" },
      { site: "neetcode", slug: "duplicate-integer", title: "Contains Duplicate" },
    ]);
  });
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

  it("reports a non-429 HTTP run response as a notice without failing its check", async () => {
    page.fetch = vi.fn(async () => new Response("bad", { status: 503 }));
    ready(); reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.some(e => String(e.payload.message).includes("HTTP 503")));
    expect(events.at(-1)).toEqual({ type: "reader_notice", payload: { message: "NeetCode couldn't run that (HTTP 503). Try again in a moment." } });
    expect(events.some(e => e.type === "reader_check" && e.payload.ok === false)).toBe(false);
  });

  it("reports a rate-limited Run as a notice without failing its check", async () => {
    page.fetch = vi.fn(async () => new Response("rate limited", { status: 429 }));
    ready(); reader.install(); reader.setMode("watching");
    await page.fetch("/api/runCodeFunctionHttp", { method: "POST", body: JSON.stringify(runRequest) });
    await flushUntil(() => events.some(e => e.type === "reader_notice"));
    expect(events.at(-1)).toEqual({ type: "reader_notice", payload: { message: "NeetCode is rate-limiting your runs. Wait a moment and try again." } });
    expect(events.some(e => e.type === "reader_check" && e.payload.ok === false)).toBe(false);
  });

  it("reports a non-2xx Submit as a notice without failing its check", async () => {
    page.fetch = vi.fn(async () => new Response("unavailable", { status: 503 }));
    ready(); reader.install(); reader.setMode("watching");
    await page.fetch("/api/executeCodeFunctionHttp", { method: "POST", body: JSON.stringify(submitRequest) });
    await flushUntil(() => events.some(e => e.type === "reader_notice"));
    expect(events.at(-1)).toEqual({ type: "reader_notice", payload: { message: "NeetCode couldn't run that (HTTP 503). Try again in a moment." } });
    expect(events.some(e => e.type === "reader_check" && e.payload.ok === false)).toBe(false);
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

  it("pauses once on a contents route and announces the next problem from its own timer", () => {
    ready(); reader.install(); reader.setMode("watching");
    page.history.pushState({}, "", "/practice/");
    vi.advanceTimersByTime(1000);
    expect(events.at(-1)).toEqual({ type: "reader_check", payload: { ok: false, message: "Open a problem and I'll pick the timer back up." } });
    const failures = events.filter(e => e.type === "reader_check" && e.payload.ok === false);
    vi.advanceTimersByTime(3000);
    expect(events.filter(e => e.type === "reader_check" && e.payload.ok === false)).toEqual(failures);
    reader.setMode("paused");
    page.history.pushState({}, "", "/problems/two-sum/");
    page.document.querySelector("h1")!.textContent = "Two Sum";
    page.localStorage.setItem("two-sum_python_tabs", JSON.stringify([{ code: "next" }]));
    vi.advanceTimersByTime(1000);
    expect(events.at(-1)).toEqual({ type: "reader_check", payload: { ok: true } });
    reader.setMode("watching");
    expect(events.filter(e => e.type === "problem_opened").at(-1)?.payload).toEqual({ site: "neetcode", slug: "two-sum", title: "Two Sum" });
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
    expect(String(events.at(-1)?.payload.message)).toMatch(/^Can't read the editor on this page \(/);
  });
  it("names each failing self-check, including two tabs and a missing hook", () => {
    reader.setMode("watching");
    expect(events.at(-1)?.payload.message).toBe("Can't identify the problem title on this page");
    page.document.body.innerHTML = "<h1>Contains Duplicate</h1>";
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't find the editor on this page");
    page.document.body.innerHTML += '<div class="my-code-editor"><div class="monaco-editor"><div class="view-lines"><div class="view-line" style="top: 0px"></div></div></div></div>';
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't read the editor on this page (no code found in the editor)");
    page.localStorage.setItem("duplicate-integer_python_tabs", JSON.stringify([{ code: "one" }, { code: "two" }]));
    reader.open();
    expect(events.at(-1)?.payload.message).toBe("Can't read the editor on this page (stored code has 2 editor tabs, expected 1)");
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

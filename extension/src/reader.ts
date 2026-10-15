export type WatchMode = "idle" | "watching" | "paused";
export type FailureCase = { input: string; expected: string; actual: string };
type RecordValue = Record<string, unknown>;

function object(value: unknown): RecordValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid NeetCode response");
  return value as RecordValue;
}
function string(value: unknown): string { return typeof value === "string" ? value : ""; }
function number(value: unknown): number { if (typeof value !== "number") throw new Error("Invalid NeetCode count"); return value; }
function status(entry: RecordValue): number { return number(object(entry.status).id); }
function failing(entry: RecordValue): FailureCase | undefined {
  if (!entry.last_executed_test_case) return undefined;
  const value = object(entry.last_executed_test_case);
  return { input: string(value.input), expected: string(value.expected_output), actual: string(value.user_output) };
}
function request(value: unknown): RecordValue {
  const data = object(object(value).data);
  if (typeof data.problemId !== "string" || typeof data.rawCode !== "string") throw new Error("Invalid NeetCode request");
  return data;
}

export function judgeError(id: number): string | null {
  if (id === 1) return "in_queue";
  if (id === 2) return "processing";
  if (id === 3) return null;
  if (id === 4) return "wrong_answer";
  if (id === 5) return "time_limit";
  if (id === 6) return "compile_error";
  if (id >= 7 && id <= 12) return "runtime_error";
  if (id === 13) return "internal_error";
  if (id === 14) return "exec_format_error";
  return `unknown_${id}`;
}

export function parseRun(rawRequest: unknown, rawResponse: unknown) {
  const sent = request(rawRequest);
  const data = object(rawResponse).data;
  if (!Array.isArray(data)) throw new Error("Invalid NeetCode Run response");
  return { slug: sent.problemId as string, cases: data.map(value => {
    const entry = object(value);
    const id = status(entry);
    return { passed: id === 3, error_type: judgeError(id), error_message: string(entry.stderr || entry.compile_output || entry.message), runtime: string(entry.time), ...(id === 3 ? {} : { failing_case: failing(entry) }) };
  }) };
}

export function parseSubmit(rawRequest: unknown, rawResponse: unknown) {
  const sent = request(rawRequest);
  const entry = object(object(rawResponse).data);
  const id = status(entry);
  return { slug: sent.problemId as string, accepted: id === 3, error_type: judgeError(id), error_message: string(entry.stderr || entry.compile_output || entry.message), runtime: string(entry.time), memory: number(entry.memory), passed: number(entry.correct_test_case_count), total: number(entry.test_case_count), ...(id === 3 ? {} : { failing_case: failing(entry) }) };
}

export function lineDiff(before: string, after: string) {
  const old = before.split("\n"), next = after.split("\n");
  let start = 0;
  while (start < old.length && start < next.length && old[start] === next[start]) start++;
  let oldEnd = old.length, newEnd = next.length;
  while (oldEnd > start && newEnd > start && old[oldEnd - 1] === next[newEnd - 1]) { oldEnd--; newEnd--; }
  return { start_line: start + 1, removed: old.slice(start, oldEnd), added: next.slice(start, newEnd) };
}

export type ReaderEvent = { type: string; payload: RecordValue };
export class NeetCodeReader {
  private mode: WatchMode = "idle";
  private slug = "";
  private previous = "";
  private lastDiff = -3000;
  private diffTimer: ReturnType<typeof setTimeout> | undefined;
  private snapshotTimer: ReturnType<typeof setInterval> | undefined;
  private pending = new Set<ReturnType<typeof setTimeout>>();
  private hooked = false;
  private ready = false;
  private openedSlug = "";
  private titleOf?: { slug: string; title: string };
  private resultFailure = false;
  private cleanup: (() => void)[] = [];
  constructor(private emit: (event: ReaderEvent) => void, private page: Window = window) {}

  install(): void {
    if (this.hooked) return;
    this.hooked = true;
    const reader = this;
    const globals = this.page as Window & typeof globalThis;
    const originalSet = globals.Storage.prototype.setItem;
    globals.Storage.prototype.setItem = function(key: string, value: string) {
      originalSet.call(this, key, value);
      if (key === `${reader.slug}_python_tabs`) reader.codeWrite();
    };
    this.cleanup.push(() => { globals.Storage.prototype.setItem = originalSet; });
    const originalFetch = this.page.fetch.bind(this.page);
    const fetchValue = this.page.fetch;
    this.page.fetch = (async (...args: Parameters<typeof fetch>) => {
      const kind = reader.kind(String(args[0] instanceof Request ? args[0].url : args[0]));
      if (!kind || reader.mode === "idle" || (reader.mode === "paused" && kind === "run")) return originalFetch(...args);
      const body = args[1]?.body ?? (args[0] instanceof Request ? await args[0].clone().text() : undefined);
      if (typeof body !== "string") { reader.fail("Can't read run request body on this page"); return originalFetch(...args); }
      const done = reader.started(kind, body);
      try { const response = await originalFetch(...args); void response.clone().text().then(text => done(text, response.status), error => done("", response.status, String(error))); return response; }
      catch (error) { done("", 0, String(error)); throw error; }
    }) as typeof fetch;
    this.cleanup.push(() => { this.page.fetch = fetchValue; });
    const proto = globals.XMLHttpRequest.prototype;
    const originalOpen = proto.open;
    const originalSend = proto.send;
    proto.open = function(method: string, url: string | URL, ...rest: unknown[]) {
      (this as XMLHttpRequest & { leetKind?: "run" | "submit" }).leetKind = method.toUpperCase() === "POST" ? reader.kind(String(url)) : undefined;
      return originalOpen.apply(this, [method, url, ...rest] as Parameters<XMLHttpRequest["open"]>);
    };
    proto.send = function(body?: Document | XMLHttpRequestBodyInit | null) {
      const kind = (this as XMLHttpRequest & { leetKind?: "run" | "submit" }).leetKind;
      if (kind && typeof body === "string") {
        const done = reader.started(kind, body);
        this.addEventListener("loadend", () => done(typeof this.response === "string" ? this.response : JSON.stringify(this.response), this.status));
      } else if (kind) reader.fail("Can't read run request body on this page");
      return originalSend.call(this, body);
    };
    this.cleanup.push(() => { proto.open = originalOpen; proto.send = originalSend; });
    const poll = this.page.setInterval(() => { this.open(); this.codeWrite(); }, 1000);
    const first = this.page.setTimeout(() => this.open(), 2000);
    this.cleanup.push(() => { this.page.clearInterval(poll); this.page.clearTimeout(first); });
  }

  dispose(): void {
    if (this.snapshotTimer) clearInterval(this.snapshotTimer);
    if (this.diffTimer) clearTimeout(this.diffTimer);
    for (const timer of this.pending) clearTimeout(timer);
    for (const action of this.cleanup.reverse()) action();
    this.cleanup = [];
    this.hooked = false;
  }

  setMode(mode: WatchMode): void {
    const was = this.mode;
    this.mode = mode;
    if (this.snapshotTimer) clearInterval(this.snapshotTimer);
    if (mode !== "watching") {
      for (const timer of this.pending) clearTimeout(timer);
      if (mode === "idle") { this.pending.clear(); this.openedSlug = ""; }
    }
    if (mode === "watching") {
      if (was !== "watching") this.resultFailure = false;
      this.open(true);
      this.snapshotTimer = setInterval(() => this.snapshot(), 60_000);
    }
  }

  private kind(url: string): "run" | "submit" | undefined {
    try {
      const parsed = new URL(url, this.page.location.href);
      if (parsed.origin !== this.page.location.origin) return undefined;
      if (parsed.pathname === "/api/runCodeFunctionHttp") return "run";
      if (parsed.pathname === "/api/executeCodeFunctionHttp") return "submit";
    } catch { /* Malformed unrelated URL. */ }
    return undefined;
  }
  private slugFromUrl(): string { return /^\/problems\/([^/]+)/.exec(this.page.location.pathname)?.[1] ?? ""; }
  private code(): string {
    const raw = this.page.localStorage.getItem(`${this.slug}_python_tabs`);
    if (!raw) throw new Error("Can't read the editor on this page");
    let tabs: unknown;
    try { tabs = JSON.parse(raw); } catch { throw new Error("Can't read the editor on this page"); }
    if (!Array.isArray(tabs) || tabs.length !== 1 || typeof tabs[0]?.code !== "string") throw new Error("Can't read the editor on this page");
    return tabs[0].code;
  }
  private check(): { title: string; code: string } {
    if (!this.slug) throw new Error("Can't identify the problem on this page");
    const title = this.page.document.querySelector("h1")?.textContent?.trim();
    if (!title) throw new Error("Can't identify the problem title on this page");
    // NeetCode titles differ from their slugs, so staleness is the previous problem's title still showing.
    if (this.titleOf && this.titleOf.slug !== this.slug && this.titleOf.title === title) throw new Error("Can't identify the problem title on this page");
    if (!this.page.document.querySelector(".monaco-editor")) throw new Error("Can't find the editor on this page");
    const code = this.code();
    if (!this.hooked) throw new Error("Can't see run results on this page (not installed)");
    return { title, code };
  }
  open(force = false): void {
    if (this.mode === "idle") return;
    const slug = this.slugFromUrl();
    if (!slug) return;
    if (slug !== this.slug) this.ready = false;
    if (!force && slug === this.slug && this.ready) return;
    this.slug = slug;
    try {
      const { title, code } = this.check();
      this.titleOf = { slug, title };
      this.ready = true;
      if (this.diffTimer) { clearTimeout(this.diffTimer); this.diffTimer = undefined; }
      this.previous = code;
      if (!this.resultFailure) this.emit({ type: "reader_check", payload: { ok: true } });
      if (slug !== this.openedSlug && this.mode === "watching") { this.send("problem_opened", { site: "neetcode", slug, title }); this.openedSlug = slug; }
      this.send("code_snapshot", { code });
    } catch (error) {
      this.ready = false;
      this.fail((error as Error).message);
    }
  }
  codeWrite(): void {
    if (!this.slug || this.mode !== "watching") return;
    let code: string;
    try { code = this.code(); } catch (error) { this.ready = false; this.fail((error as Error).message); return; }
    if (code === this.previous || this.diffTimer) return;
    const wait = Math.max(0, 3000 - (Date.now() - this.lastDiff));
    this.diffTimer = setTimeout(() => {
      this.diffTimer = undefined;
      if (this.mode !== "watching") return;
      let latest: string;
      try { latest = this.code(); } catch (error) { this.ready = false; this.fail((error as Error).message); return; }
      if (latest === this.previous) return;
      this.send("code_changed", { diff: lineDiff(this.previous, latest) });
      this.previous = latest;
      this.lastDiff = Date.now();
    }, wait);
  }
  snapshot(): void {
    if (this.mode !== "watching") return;
    try { const code = this.code(); this.previous = code; this.send("code_snapshot", { code }); }
    catch (error) { this.ready = false; this.fail((error as Error).message); }
  }
  private fail(message: string): void { if (this.mode !== "idle") this.emit({ type: "reader_check", payload: { ok: false, message } }); }
  private send(type: string, payload: RecordValue): void {
    if (this.mode === "watching" || (this.mode === "paused" && (type === "focus_changed" || type === "submit_result"))) this.emit({ type, payload });
  }
  private started(kind: "run" | "submit", body: string): (response: string, status?: number, parseError?: string) => void {
    if (this.mode === "idle" || (this.mode === "paused" && kind === "run")) return () => {};
    if (this.mode === "watching") {
      try {
        const code = string(request(JSON.parse(body)).rawCode);
        if (this.diffTimer) { clearTimeout(this.diffTimer); this.diffTimer = undefined; }
        this.previous = code;
        this.send("code_snapshot", { code });
      }
      catch { this.snapshot(); }
    }
    const timer = setTimeout(() => {
      this.resultFailure = true;
      this.fail(`Can't see run results on this page (no response to the ${kind} within 30 seconds)`);
    }, 30_000);
    this.pending.add(timer);
    return (response: string, status = 200, parseError?: string) => {
      if (!this.pending.delete(timer)) return;
      clearTimeout(timer);
      if (this.mode === "idle") return;
      if (status !== 0 && (status < 200 || status >= 300)) {
        this.emit({ type: "reader_notice", payload: { message: status === 429
          ? "NeetCode is rate-limiting your runs. Wait a moment and try again."
          : `NeetCode couldn't run that (HTTP ${status}). Try again in a moment.` } });
        return;
      }
      try {
        if (status === 0) throw new Error(parseError || "No response");
        if (parseError) throw new Error(parseError);
        const payload = kind === "run" ? parseRun(JSON.parse(body), JSON.parse(response)) : parseSubmit(JSON.parse(body), JSON.parse(response));
        this.resultFailure = false;
        this.emit({ type: "reader_check", payload: { ok: true } });
        this.send(`${kind}_result`, payload);
      } catch (error) {
        this.resultFailure = true;
        this.fail(`Can't see run results on this page: ${(error as Error).message}`);
      }
    };
  }
}

import { beforeEach, describe, expect, it, vi } from "vitest";
import { JSDOM } from "jsdom";

let listener: (message: any) => void;
let sendMessage: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  vi.resetModules();
  const dom = new JSDOM('<p id="status"></p><p id="reader-error"></p><button id="reader-test"></button><p id="reader-activity"></p><button id="import-history"></button><p id="import-result"></p>');
  vi.stubGlobal("document", dom.window.document);
  sendMessage = vi.fn(async (message: any) => {
    if (message.kind === "get_import_state") return { imported: false };
    if (message.kind === "get_status") return { kind: "connected" };
    if (message.kind === "get_mode") return "idle";
    if (message.kind === "get_reader_error") return "";
    if (message.kind === "get_reader_activity") return "No reader events yet.";
    if (message.kind === "import_history") return { pending: true };
  });
  vi.stubGlobal("chrome", { runtime: { onMessage: { addListener: (fn: typeof listener) => { listener = fn; } }, sendMessage } });
  await import("./panel");
  await vi.waitFor(() => expect(document.getElementById("import-history")?.hidden).toBe(false));
});

describe("panel import", () => {
  it("requests import and displays matched and unmatched results", async () => {
    document.getElementById("import-history")?.click();
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledWith({ kind: "import_history" }));
    listener({ kind: "import_state", imported: true, result: { already_imported: false, matched: 2, unmatched: ["https://leetcode.com/problems/unknown/"] } });
    expect(document.getElementById("import-history")?.hidden).toBe(true);
    expect(document.getElementById("import-result")?.textContent).toContain("2");
    expect(document.getElementById("import-result")?.textContent).toContain("unknown");
  });
  it("shows named cache errors", () => {
    listener({ kind: "import_error", message: "synced-progress-cache is missing" });
    expect(document.getElementById("import-result")?.textContent).toContain("synced-progress-cache is missing");
  });
});

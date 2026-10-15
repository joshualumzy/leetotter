import { beforeEach, describe, expect, it, vi } from "vitest";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import type { SessionState } from "./protocol";

let listener: (message: any) => void;
let sendMessage: ReturnType<typeof vi.fn>;
const session: SessionState = { type: "session_state", ts: "now", state: "idle", mode: "idle", paused_reason: null, check_message: null, budget_minutes: null, active_seconds: 0, running: false, overrun_prompt: false, problem: null, accepted: false };
const notify = (message: any) => listener(message);
const click = (id: string) => document.getElementById(id)?.click();

beforeEach(async () => {
  vi.resetModules();
  const html = readFileSync(new URL("../panel.html", import.meta.url), "utf8");
  const dom = new JSDOM(html);
  vi.stubGlobal("document", dom.window.document);
  vi.stubGlobal("window", dom.window);
  sendMessage = vi.fn(async (message: any) => {
    if (message.kind === "get_import_state") return { imported: false };
    if (message.kind === "get_status") return { kind: "connected" };
    if (message.kind === "get_reader_error") return "";
    if (message.kind === "get_session_state") return session;
    if (message.kind === "get_settings_state") return { type: "settings_state", ts: "now", name: null, theme: "match" };
    if (message.kind === "import_history") return { pending: true };
    return { sent: true };
  });
  vi.stubGlobal("chrome", { runtime: { onMessage: { addListener: (fn: typeof listener) => { listener = fn; } }, sendMessage } });
  await import("./panel");
  await vi.waitFor(() => expect(document.getElementById("start-session")).toBeTruthy());
});

describe("panel actions", () => {
  it("starts, pauses, resumes and ends through session commands", async () => {
    (document.getElementById("budget-minutes") as HTMLInputElement).value = "30";
    click("start-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "start", budget_minutes: 30 });
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 30 } });
    click("pause-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "pause" });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "button", budget_minutes: 30 } });
    click("resume-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "resume" });
    click("end-session");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "end" });
  });
  it("asks for the first name and supports Enter, Skip and settings", () => {
    expect(document.getElementById("first-run")?.textContent).toContain("What should I call you?");
    const input = document.getElementById("first-name") as HTMLInputElement;
    input.value = " Ada ";
    input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(sendMessage).toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "Ada" });
    click("skip-name");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "settings_set", key: "name", value: "" });
    click("header-otter");
    expect(document.getElementById("settings-card")).toBeTruthy();
    click("close-settings");
    expect(document.getElementById("settings-card")).toBeFalsy();
  });
  it("sends both overrun choices and hides auto-resume", () => {
    notify({ kind: "session_state", state: { ...session, state: "active", mode: "watching", running: true, budget_minutes: 45, overrun_prompt: true } });
    click("overrun-finish"); click("overrun-stop");
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "overrun_finish" });
    expect(sendMessage).toHaveBeenCalledWith({ kind: "session_command", command: "overrun_stop" });
    notify({ kind: "session_state", state: { ...session, state: "paused", mode: "paused", paused_reason: "focus", budget_minutes: 45 } });
    expect(document.getElementById("resume-session")).toBeFalsy();
  });
  it("keeps history import and shows errors", async () => {
    click("import-history");
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledWith({ kind: "import_history" }));
    notify({ kind: "import_state", imported: true, result: { already_imported: false, matched: 2, unmatched: ["unknown"] } });
    expect(document.getElementById("import-history")?.hidden).toBe(true);
    expect(document.getElementById("import-result")?.textContent).toContain("2");
    notify({ kind: "reader_error", message: "Editor missing" });
    expect(document.querySelector(".alert")?.textContent).toContain("Editor missing");
  });
  it("has no remote asset URLs", () => {
    const html = readFileSync(new URL("../panel.html", import.meta.url), "utf8");
    const css = readFileSync(new URL("../panel.css", import.meta.url), "utf8");
    expect(html + css).not.toMatch(/https?:\/\//);
  });
});

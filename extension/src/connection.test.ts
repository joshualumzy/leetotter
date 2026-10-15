import { afterEach, describe, expect, it, vi } from "vitest";
import { CompanionConnection } from "./connection";

class FakeSocket {
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  sent: string[] = [];
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.(); }
}

afterEach(() => vi.useRealTimers());

describe("companion connection", () => {
  it("reports a dropped application event while the socket is closed", () => {
    const socket = new FakeSocket();
    const connection = new CompanionConnection("ws://127.0.0.1:8765", () => socket, () => {});
    connection.start();
    expect(connection.send({ type: "code_snapshot" })).toBe(false);
    socket.readyState = 1;
    expect(connection.send({ type: "code_snapshot" })).toBe(true);
    connection.stop();
  });
  it("returns to connected after a rejected command once the companion sends a healthy message", () => {
    const socket = new FakeSocket();
    const states: string[] = [];
    const connection = new CompanionConnection("ws://127.0.0.1:8765", () => socket, (state) => states.push(state.kind), () => {});
    connection.start();
    socket.onmessage?.({ data: '{"type":"status","ts":"2026-10-15T00:00:00Z","state":"connected"}' });
    socket.onmessage?.({ data: '{"type":"error","ts":"2026-10-15T00:00:01Z","message":"session_pause requires an active session"}' });
    expect(states.at(-1)).toBe("error");
    socket.onmessage?.({ data: '{"type":"import_status","ts":"2026-10-15T00:00:02Z","imported":false}' });
    expect(states.at(-1)).toBe("connected");
    connection.stop();
  });
  it("reconnects after the companion starts or restarts", () => {
    vi.useFakeTimers();
    const sockets: FakeSocket[] = [];
    const states: string[] = [];
    const connection = new CompanionConnection(
      "ws://127.0.0.1:8765",
      () => { const socket = new FakeSocket(); sockets.push(socket); return socket; },
      (state) => states.push(state.kind),
    );
    connection.start();
    expect(sockets).toHaveLength(1);
    sockets[0].onclose?.();
    expect(states.at(-1)).toBe("offline");
    vi.advanceTimersByTime(250);
    expect(sockets).toHaveLength(2);
    sockets[1].onopen?.();
    expect(JSON.parse(sockets[1].sent[0]).type).toBe("hello");
    sockets[1].onmessage?.({ data: '{"type":"status","ts":"2026-10-15T00:00:00Z","state":"connected"}' });
    expect(states.at(-1)).toBe("connected");
    sockets[1].onclose?.();
    vi.advanceTimersByTime(250);
    expect(sockets).toHaveLength(3);
    connection.stop();
  });

  it("shows unknown server messages as errors", () => {
    const sockets: FakeSocket[] = [];
    const states: {kind: string; message?: string}[] = [];
    const connection = new CompanionConnection("ws://127.0.0.1:8765", () => {
      const socket = new FakeSocket(); sockets.push(socket); return socket;
    }, (state) => states.push(state));
    connection.start();
    sockets[0].onmessage?.({ data: '{"type":"mystery","ts":"2026-10-15T00:00:00Z"}' });
    expect(states.at(-1)).toEqual({ kind: "error", message: "Unknown message type: mystery" });
    connection.stop();
  });

  it("sends an application ping every 20 seconds while connected", () => {
    vi.useFakeTimers();
    const socket = new FakeSocket();
    const connection = new CompanionConnection("ws://127.0.0.1:8765", () => socket, () => {});
    connection.start();
    socket.onopen?.();
    vi.advanceTimersByTime(20_000);
    expect(socket.sent.map((raw) => JSON.parse(raw).type)).toEqual(["hello", "ping"]);
    connection.stop();
  });

  it("caps the retry delay while the companion stays down", () => {
    vi.useFakeTimers();
    const sockets: FakeSocket[] = [];
    const connection = new CompanionConnection("ws://127.0.0.1:8765", () => {
      const socket = new FakeSocket(); sockets.push(socket); return socket;
    }, () => {});
    connection.start();
    for (const delay of [250, 500, 1_000, 2_000, 4_000, 4_000]) {
      sockets.at(-1)?.onclose?.();
      const count = sockets.length;
      vi.advanceTimersByTime(delay - 1);
      expect(sockets).toHaveLength(count);
      vi.advanceTimersByTime(1);
      expect(sockets).toHaveLength(count + 1);
    }
    connection.stop();
  });
});

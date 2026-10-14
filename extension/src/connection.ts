import { makeMessage, parseMessage } from "./protocol";

export type ConnectionState = { kind: "offline" | "connected" } | { kind: "error"; message: string };

type Socket = {
  onopen: ((...args: any[]) => void) | null;
  onclose: ((...args: any[]) => void) | null;
  onerror: ((...args: any[]) => void) | null;
  onmessage: ((...args: any[]) => void) | null;
  send(data: string): void;
  close(): void;
};

export class CompanionConnection {
  private socket: Socket | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private delay = 250;
  private active = false;

  constructor(
    private readonly url: string,
    private readonly createSocket: (url: string) => Socket,
    private readonly onState: (state: ConnectionState) => void,
  ) {}

  start(): void {
    if (this.active) return;
    this.active = true;
    this.onState({ kind: "offline" });
    this.connect();
  }

  stop(): void {
    this.active = false;
    if (this.retry) clearTimeout(this.retry);
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.retry = null;
    this.heartbeat = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  private connect(): void {
    if (!this.active) return;
    let socket: Socket;
    try { socket = this.createSocket(this.url); }
    catch { this.disconnected(); return; }
    this.socket = socket;
    socket.onopen = () => {
      socket.send(JSON.stringify(makeMessage("hello")));
      this.heartbeat = setInterval(() => socket.send(JSON.stringify(makeMessage("ping"))), 20_000);
    };
    socket.onmessage = (event) => {
      try {
        const message = parseMessage(event.data);
        if (message.type === "status") {
          this.delay = 250;
          this.onState({ kind: "connected" });
        } else if (message.type === "error") {
          console.error("Companion error:", message.message);
          this.onState({ kind: "error", message: message.message });
        } else {
          throw new Error(`Unexpected server message type: ${message.type}`);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        console.error("WebSocket protocol error:", message);
        this.onState({ kind: "error", message });
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.heartbeat) clearInterval(this.heartbeat);
      this.heartbeat = null;
      this.disconnected();
    };
  }

  private disconnected(): void {
    if (!this.active) return;
    this.onState({ kind: "offline" });
    this.retry = setTimeout(() => {
      this.retry = null;
      this.connect();
    }, this.delay);
    this.delay = Math.min(this.delay * 2, 4_000);
  }
}

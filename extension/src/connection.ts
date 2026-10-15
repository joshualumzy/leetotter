import { makeMessage, parseMessage, type Message } from "./protocol";

export type ConnectionState = { kind: "offline" | "connected" } | { kind: "error"; message: string };

type Socket = {
  readyState?: number;
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
  private errored = false;

  constructor(
    private readonly url: string,
    private readonly createSocket: (url: string) => Socket,
    private readonly onState: (state: ConnectionState) => void,
    private readonly onMessage?: (message: Message) => void,
  ) {}

  start(): void {
    if (this.active) return;
    this.active = true;
    this.onState({ kind: "offline" });
    this.connect();
  }

  send(message: unknown): boolean {
    if (!this.socket || this.socket.readyState !== 1) return false;
    try { this.socket.send(JSON.stringify(message)); return true; }
    catch { this.socket.close(); return false; }
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
        if (this.errored && message.type !== "error" && message.type !== "status") {
          // A rejected command is not a broken connection: the next healthy message clears it.
          this.errored = false;
          this.onState({ kind: "connected" });
        }
        if (message.type === "status") {
          this.errored = false;
          this.delay = 250;
          this.onState({ kind: "connected" });
        } else if (message.type === "error") {
          console.error("Companion error:", message.message);
          this.errored = true;
          this.onState({ kind: "error", message: message.message });
        } else if (message.type === "import_status" || message.type === "import_result" || message.type === "session_state" || message.type === "settings_state") {
          this.onMessage?.(message);
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

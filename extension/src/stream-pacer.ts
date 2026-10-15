export type PaceFrame = { text: string; wordCount: number; added: string[]; finished: boolean; waiting: boolean };

export class StreamPacer {
  private source = "";
  private shown = "";
  private count = 0;
  private last: number | null = null;
  private credit = 0;
  private final: string | null = null;

  get visible(): string { return this.shown; }

  push(text: string): void { this.source += text; }
  finish(text: string): void {
    this.final = text;
    if (text.startsWith(this.source)) this.source = text;
  }
  replace(text: string): void {
    this.source = text; this.shown = ""; this.count = 0; this.last = null; this.credit = 0; this.final = null;
  }
  tick(now: number): PaceFrame {
    if (this.last === null) {
      this.last = now;
      return { text: this.shown, wordCount: this.count, added: [], finished: false, waiting: false };
    }
    const pending = this.source.slice(this.shown.length);
    const available = pending.match(/\S+\s*/g) || [];
    const complete = this.final === null && available.length && !/\s$/.test(pending) ? available.slice(0, -1) : available;
    const rate = Math.max(16, complete.length / 2);
    this.credit += Math.max(0, now - this.last) * rate / 1000;
    this.last = now;
    const added = complete.slice(0, Math.floor(this.credit));
    this.credit -= added.length;
    if (!complete.length) this.credit = Math.min(this.credit, 1);
    this.shown += added.join("");
    this.count += added.length;
    if (this.final !== null && this.shown.length >= this.source.length) this.shown = this.final;
    const finished = this.final !== null && this.shown === this.final;
    return { text: this.shown, wordCount: this.count, added, finished, waiting: !finished && !complete.length && this.final === null };
  }
}

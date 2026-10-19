import type { ProgressHealth, ProgressHeader } from "./protocol";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function recapDate(date: string): string {
  const value = new Date(date);
  if (Number.isNaN(value.getTime())) return date;
  return `${value.getUTCDate()} ${MONTHS[value.getUTCMonth()]} ${value.getUTCFullYear()}`;
}

export function progressSummary(header: ProgressHeader): string {
  return `${header.on_your_own} on your own · ${header.coming_back} coming back · ${header.to_do} to do`;
}

export function strongSummary(header: ProgressHeader, categories: number): string {
  const strong = `${header.strong} of ${categories} strong`;
  return header.rusty ? `${strong} · ${header.rusty} rusty` : strong;
}

export function categoryCount(done: number, total: number): string {
  return `${done} of ${total}`;
}

export function earlierLabel(count: number): string {
  return `${count} earlier`;
}

export function untriedLine(counts: { not_tried: number; imported_only: number }): string {
  const parts = [];
  if (counts.not_tried) parts.push(`${counts.not_tried} not tried`);
  if (counts.imported_only) parts.push(`${counts.imported_only} solved before Leetotter, not yet tested`);
  return parts.join(" · ");
}

export function healthLine(health: ProgressHealth): string {
  const parts = [];
  if (health.graded) parts.push(`graders agree on ${Math.round((1 - health.disagreement_share) * 100)}%`);
  if (health.ungraded) parts.push(`${health.ungraded} ${health.ungraded === 1 ? "attempt" : "attempts"} ungraded`);
  return parts.length ? `Grading health: ${parts.join(" · ")}` : "Grading health: nothing graded yet";
}

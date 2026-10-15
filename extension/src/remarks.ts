import type { RemarkPool } from "./panel-view";

type Context = { name: string | null; minutesLeft: number | null; now: Date };
type Line = (context: Context) => string | null;
const named = (line: string): Line => ({ name }) => name ? line.replace("{name}", name) : null;
const plain = (line: string): Line => () => line;
const pools: Record<Exclude<RemarkPool, "offline">, Line[]> = {
  start: [
    plain("I'm here when you're ready."),
    plain("A little time to practise?"),
    named("Hi {name}! How long have you got?"),
    ({ now }) => now.getHours() < 11 ? "Morning! Kopi's ready." : null,
    ({ now }) => [0, 6].includes(now.getDay()) ? "A weekend float sounds lovely." : null,
  ],
  active: [
    plain("I'm watching quietly."),
    plain("Take your time."),
    named("I'm right here, {name}."),
    ({ minutesLeft }) => minutesLeft !== null && minutesLeft >= 0 ? `${minutesLeft} min left.` : null,
  ],
  accepted: [plain("Accepted! Keep polishing."), plain("Shell cracked!"), named("Cracked it, {name}!")],
  pause_button: [plain("Zzz… wake me when you're back."), plain("Floating in place."), named("Rest easy, {name}.")],
  pause_focus: [plain("Napping till you're back on the problem."), plain("I'll be here when the tab is active."), named("See you soon, {name}.")],
  pause_check: [plain("The page reader's check failed.")],
  pause_disconnect: [plain("The tutor connection was lost.")],
  overrun: [plain("Time's up. Your call."), plain("One more, or float ashore?"), named("Your call, {name}.")],
  ended: [plain("A good place to rest."), plain("That session is tucked away."), named("See you next time, {name}.")],
};
let last = "";
export function remark(pool: RemarkPool, context: Context, random = Math.random): string {
  if (pool === "offline") return "The tutor service isn't running.";
  const available = pools[pool].map(line => line(context)).filter((line): line is string => Boolean(line));
  const choices = available.filter(line => line !== last);
  const selected = (choices.length ? choices : available)[Math.floor(random() * (choices.length ? choices.length : available.length))];
  last = selected;
  return selected;
}

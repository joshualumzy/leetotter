import type { RemarkPool } from "./panel-view";
import { touchForDate } from "./otter";

type Context = { name: string | null; now: Date };
type Line = (context: Context) => string | null;
const named = (line: string): Line => ({ name }) => name ? line.replace("{name}", name) : null;
const plain = (line: string): Line => () => line;
const touchLine: Line = ({ now }) => {
  const touch = touchForDate(now);
  if (touch.includes("morning")) return "Morning! Kopi's ready.";
  if (touch.includes("afternoon")) return "Sunny afternoon float.";
  if (touch.includes("night")) return "Late one tonight.";
  if (touch.includes("weekend")) return "A weekend float sounds lovely.";
  if (touch === "lny") return "A little bow for the new year.";
  if (touch === "xmas") return "A festive float today.";
  if (touch === "halloween") return "A little magic in the air.";
  return null;
};
const pools: Record<Exclude<RemarkPool, "offline">, Line[]> = {
  start: [
    plain("I'm here when you're ready."),
    plain("A little time to practise?"),
    named("Hi {name}! Ready to practise?"),
    touchLine,
  ],
  active: [
    plain("I'm here to help whenever you need me."),
    plain("I'm watching quietly."),
    plain("Take your time."),
    named("I'm right here, {name}."),
    touchLine,
  ],
  thinking: [plain("Thinking this through…"), plain("Let me look at this…"), plain("Finding a useful next step…")],
  accepted: [plain("Accepted! Keep polishing."), plain("Shell cracked!"), named("Cracked it, {name}!")],
  pause_button: [plain("Zzz… wake me when you're back."), plain("Floating in place."), named("Rest easy, {name}.")],
  pause_focus: [plain("Napping till you're back on the problem."), plain("I'll be here when the tab is active."), named("See you soon, {name}.")],
  pause_check: [plain("The page reader's check failed.")],
  pause_check_page: [plain("I'll wait here for a problem.")],
  pause_disconnect: [plain("The tutor connection was lost.")],
  pause_closed: [plain("Welcome back."), plain("I'm still here."), named("Good to see you, {name}.")],
  pause_restart: [plain("Ready when you are."), plain("Here again, whenever you like."), named("Welcome back, {name}.")],
  time_up: [plain("Time's up. Extend, debrief, or skip."), plain("The cap's up; your call."), named("Your call, {name}.")],
  debrief: [plain("Let's look back over that one."), plain("Tell me what happened, and we'll untangle it."), named("Let's retrace it, {name}.")],
  ended: [plain("A good place to rest."), plain("That session is tucked away."), named("See you next time, {name}.")],
  name_first: [named("Nice to meet you, {name}!"), named("Hello, {name}!"), named("Glad to meet you, {name}!")],
  name_settings: [named("Got it, {name}."), named("Noted, {name}."), named("Okay, {name}.")],
};
const lastByPool = new Map<RemarkPool, string>();
export function remark(pool: RemarkPool, context: Context, random = Math.random): string {
  if (pool === "offline") return "The tutor service isn't running.";
  const available = pools[pool].map(line => line(context)).filter((line): line is string => Boolean(line));
  const choices = available.filter(line => line !== lastByPool.get(pool));
  const selected = (choices.length ? choices : available)[Math.floor(random() * (choices.length ? choices.length : available.length))];
  lastByPool.set(pool, selected);
  return selected;
}

# Leetotter: Spec

This is the build spec. `design.html` (published at https://claude.ai/artifact/NuRZpv5EcrmcRdmnsihAcG) explains the system at a high level; this file says how to build it, what can go wrong, and in what order to build it. Where they disagree, fix the disagreement rather than picking one silently.

## 1. Scope

**In scope:** practising the NeetCode 150 and related LeetCode problems, in Python, on the LeetCode and NeetCode web editors, on one Mac (Apple M2, 16 GB).

**Out of scope:** system design practice (a future extension), other devices or sync, other coding languages, a timed mock-interview mode, rating buttons, notifications outside a session, tracking practice done outside a session.

## 2. Fixed rules and agent judgment

The system is meant to evolve as it learns about you, so most numbers in this spec are **starting defaults that the agents tune**. Only a few things are fixed.

**Fixed (changed only by you):**
- the goal and deadline
- what counts as mastered (§9) and the evidence weights (§8), so that progress is always measured the same way
- the guardrails: help is offered only after your yes; it watches only during sessions; hypotheses must cite evidence

**Tuned by the agents, from defaults:** when placement ends, offer waiting times, how much help to give and when, follow-ups after a solution, how often to ask about complexity, problem order and mix, check-in frequency, and session pacing when you overrun.

**How tuning works:**
- Tunable values live in the `policy` table, each with its current value, default, and a history of changes.
- The Reflector proposes changes after a session, each with a reason and the events behind it.
- Code applies them within sane bounds, for example waiting times between 1 and 20 minutes.
- Each change is listed in the session wrap-up, so you can see how it's evolving and push back.
- Whether a change worked is judged against the fixed measure: did you progress on unseen problems?

## 3. Components

```
Chrome extension (TypeScript, Manifest V3)
├── page readers      content scripts for leetcode.com and neetcode.io
├── side panel        chat, push-to-talk, plan and choices, Progress tab
└── background worker relays everything to the companion over one WebSocket

Companion (Python, runs on localhost)
├── coordinator       session state machine; decides which agent runs, with what context
├── agents            Observer, Speak gate, Tutor, Reflector, Planner, Card builder, Checker
├── model client      one OpenAI-compatible client; per-role model config, rate limiter, fallback
├── speech            mlx-whisper (small) for speech to text, kokoro-onnx for voice
├── runner            sandboxed Python execution for verifying card solutions
└── store             one SQLite file
```

**The extension never calls the model API or stores learner data.** It reads pages, shows UI, captures audio, and forwards events. If the companion isn't running, the side panel says so plainly ("Tutor service isn't running. Start it with …") and does nothing else.

**One WebSocket, typed messages.** Every message has `type`, `session_id` (when in a session) and `ts`. Unknown message types are an error, logged and shown, never ignored.

## 4. Reading the pages

Each page reader produces the same events regardless of site:

| Event | Payload |
|---|---|
| `problem_opened` | site, problem slug, title |
| `code_changed` | diff against the previous snapshot (debounced to at most one every 3 s while typing) |
| `code_snapshot` | full code (on problem open, every 60 s while active, before every run/submit) |
| `run_result` | passed/failed per test case, error type and message, runtime |
| `submit_result` | accepted or not, failing case, runtime and memory |
| `focus_changed` | tab visible/hidden, window focused/blurred |

**How:** a script in the page's main world reads the editor's model directly and watches the site's own run/submit network responses, which is sturdier than scraping the DOM. This must be checked per site at the start of the phase that builds that reader:

- [ ] LeetCode: confirm the editor (Monaco) and that its model is reachable from the main world.
- [ ] LeetCode: confirm the run/submit result responses and their shape.
- [ ] NeetCode: find out which editor it uses, and how its run/submit results can be observed.

**Fail loudly.** Every reader runs a self-check when a problem page opens: can it find the editor, read the code, and identify the problem? If not, the side panel shows "Can't read the editor on this page", and the session refuses to start or pauses. It never tutors without being able to read the code.

**History import** (once at setup, re-runnable): read your solved problems and dates from your logged-in LeetCode account, and NeetCode's completion state. Stored as `prior_solve` events with weak evidence weight. To verify: which LeetCode endpoint lists accepted submissions; whether NeetCode exposes completion data.

## 5. Sessions

### State machine

```
idle ──start(minutes)──▶ planning ──plan accepted──▶ active
active ──problem solved / given up──▶ between ──choice──▶ active | break | ended
active ──10 min no activity──▶ paused ──activity──▶ active
paused ──60 min total away──▶ ended (wrap-up waits for next panel open)
any ──stop──▶ ended ──▶ Reflector runs in the background
```

- **Watching happens only in `active`.** Outside a session the page readers send nothing.
- **"No activity"** means no code changes, runs or messages. A hidden tab or blurred window counts toward the pause timer.
- **Time budget is soft.** When it runs out, the panel asks "finish this one or stop?". Overruns are logged.
- **Off-plan problems:** if you open a problem that isn't in the plan, it becomes the active problem and is logged as `off_plan`.

### Between problems

After a solve, or once the tutor has walked you to the solution:
1. **The complexity question (optional).** The Tutor decides whether to ask, the way an interviewer would: time, space, or both. You can skip it.
2. **A wrap-up** of one or two lines: the key insight, what tripped you up.
3. **Choices**, generated by the Planner from the time left: next problem (with reason), take a break, one more past your time, stop. Clicking a problem opens it on the site where it's free.

### Session start

1. You say how many minutes you have.
2. If a forecast check-in is due (see §9), it comes first.
3. The Planner proposes problems, each with a reason and whether it's diagnosing or training. You can accept, swap or skip; swaps are logged as preferences.
4. In the placement phase, the panel says so.

## 6. Help

### How offers are made

The Speak gate decides whether to make an offer. The tutor never volunteers a hint without your yes.

| Situation (from the Observer) | Offer |
|---|---|
| Active but going in circles: repeated failing runs with the same error, undo/redo loops, rewriting the same block | Soft chime + short spoken offer (silent panel message in quiet mode) |
| No activity, tab focused | Silent panel message only |
| Going a wrong way but still making progress | No offer |

- An offer stays in the panel until you answer or dismiss it. At most one re-offer per problem, a few minutes later.
- **Waiting times are tuned policy (§2).** Defaults: about 4 minutes for circles, 6 minutes for stalls. They adjust from history: ignored offers followed by a solve lengthen them; accepted offers that led to quick progress shorten them.

### How much help

There's no fixed hint ladder in code. The Tutor gets:
- the problem card
- your current code and recent observations
- the time spent, the time left in the session, and the plan
- your history with this pattern, and the framings that worked before

From that it chooses the next move: a question, a trace, an analogy, a code snippet, or eventually the full solution. Its instructions say:
- help should match how long and how hard you've struggled
- weigh the value of more struggle against the time left in the session
- after a solution is shown, add only the explanation you still need, with no repeated Socratic process

**Each help turn is logged** with its kind (`question`, `trace`, `analogy`, `snippet`, `partial_logic`, `full_solution`) so evidence weights can use it.

### Replies

Replies are text in the panel. Voice is used for offers, and for replies only when "speak replies" is on. Then the Tutor writes a separate short spoken version, which never contains code.

## 7. Agents

Each agent is one prompt template plus a context builder plus an output validator. **Every structured output is checked against a schema.** On failure: one retry with the error shown to the model, then a loud failure (logged, shown in the panel if it affects you). Invalid output is never stored.

| Agent | Trigger | Input | Output | Model |
|---|---|---|---|---|
| Observer | each `code_changed`/`run_result` batch, at most every 5 s | card summary, last N diffs, recent observations, recent results | `{approach_id or "new", state: progressing/circling/stalled/done, note}` | qwen3.5:9b, thinking off |
| Speak gate | Observer reports circling/stalled | observation history, learned thresholds, time since last offer, quiet mode | `{offer: bool, channel: voice/panel, reason}` | qwen3.5:9b, thinking off |
| Tutor | your message, an accepted offer, a solve | see §6 | reply text, optional spoken text, help kind, optional complexity question | GLM 5.3 Flash |
| Reflector | session ended | full session event log, current learner model, teaching memory | session summary, hypotheses added/changed (each citing event ids), teaching-attempt outcomes, user-facing bullets | GLM 5.3 Flash |
| Planner | session start, between problems | learner summary (computed by code, §8), goal and forecast, minutes, unfinished problems, preferences | ordered problem list, each with reason and `diagnose`/`train`; or choices | GLM 5.3 Flash |
| Card builder | offline batch; on demand for drill problems | problem statement | card (§10) | GLM 5.3 Flash |
| Checker | after Card builder; when the Tutor analyses a new approach | card or approach | agree/disagree per label, with reasons | qwen3.8:27b |

## 8. Learner model

**Numbers come from code, judgment from the model.**

**Code computes**, for each (pattern, skill) pair, with skill being `solve` or `analysis`:
- **mastery:** a weighted average of evidence outcomes
- **confidence:** total evidence weight, decaying with a half-life of about 30 days since each piece of evidence
- **last seen**

Evidence weights (fixed, §2; changed only with your OK):

| Evidence | Weight |
|---|---|
| Unseen problem, solved without help | 1.0 |
| Unseen problem, solved after questions/traces only | 0.7 |
| Solved after snippets or partial logic | 0.4, outcome counts as partial |
| Full solution shown | 0.5, outcome counts as failure |
| Repeat of a seen problem | the above × 0.5 |
| Imported prior solve | 0.2 |
| Complexity answer, right with right reasoning | 1.0 (analysis) |
| Right answer, wrong or missing reasoning | 0.3 (analysis) |

Evidence spreads to prerequisite patterns at half weight.

**The Reflector writes**:
- hypotheses about you, such as "visited marking: suspected weak"
- teaching-memory entries
- the session summary

Every hypothesis cites event ids. A hypothesis that cites nothing is rejected by the validator.

**Your notes** (preferences, corrections, goal) override both. A correction like "I was tired on Tuesday" marks that session's evidence at reduced weight.

**Placement** has no fixed length. The Planner ends it when it judges it has enough evidence across patterns to plan well, and says so.

## 9. Goal and forecast

- **Goal:** the NeetCode 150, with a deadline set at first run and changeable at any time.
- **Mastered** (fixed, §2): every pattern has mastery ≥ 0.8 and confidence ≥ threshold for both `solve` and `analysis`, from unseen problems, and each of the 150 has an accepted submission of your own (any approach).
- **Pace:** learned from completed sessions: sessions per week, minutes per session, and problems per hour.
- **Forecast:** remaining work divided by pace, with a range from the spread in your weekly practice. Hidden for the first 2–3 weeks.
- **Check-in:** at session start, only when the forecast's likely date passes the deadline. How often it raises it is tuned policy (default: at most once a week unless it gets worse). It offers concrete options:
  - move the date by N days
  - add about X problems a week, said as extra sessions at your usual length
  - narrow to the most-tested patterns

## 10. Problem cards

```yaml
slug, title, site_free: leetcode | neetcode, difficulty, neetcode_category
patterns: [ids], analysis_concepts: [ids]
approaches:
  - id, name, idea, code (Python), time, space
    kind: insight | trick | core
    interview_expected: usually | sometimes | rarely
    jump_from_previous: small | medium | large
explanations: [short framings of the core idea]
common_mistakes: [{description, symptom}]
related: [slugs]
tests: [{input, expected}]
```

**Build pipeline:**
1. The Card builder writes the card, including about 20 test cases with edge cases.
2. **Run every approach against every test** in the runner (subprocess, time and memory limits, no network). All approaches must agree.
3. **Cross-check on random inputs**: the simplest approach acts as an oracle for the others on about 200 random inputs.
4. **The Checker reviews** the complexity and labels. If they disagree, regenerate that part once. If they still disagree, the card is stored with `needs_review` and the Tutor is told which labels are uncertain.
5. Cards that fail steps 2–3 twice are not used, and are listed in a build report.

The pattern graph is built from the cards' `patterns` and `analysis_concepts`. Its seeds are NeetCode's categories, split into finer patterns by the Card builder. Prerequisite edges are proposed by the model and checked by the Checker.

New approaches you find during a session are added to the card after the Checker agrees.

## 11. Store (SQLite)

| Table | Holds | Written by |
|---|---|---|
| `events` | every signal, observation, offer, help turn, result; append-only | code |
| `sessions` | start, end, budget, plan, summary, user-facing bullets | coordinator, Reflector |
| `evidence` | (pattern, skill, weight, outcome, event_id) | code |
| `hypotheses` | text, status, cited event ids | Reflector |
| `teaching` | pattern, framing, support level, outcome, later recall | Reflector |
| `notes` | preferences, corrections, goal | you, via the panel |
| `policy` | tunable values: current, default, bounds, change history with reasons | Reflector (proposes), code (applies within bounds) |
| `cards`, `patterns`, `edges` | the map | Card builder, Checker |
| `model_calls` | role, model, latency, tokens, validation result; full prompt text kept for the last 10 sessions only | model client |

About 1 MB per session. The schema is versioned with migrations from the first release.

## 12. Models

```toml
[roles]
observer = "qwen3.5:9b"      # thinking disabled
gate     = "qwen3.5:9b"      # thinking disabled
tutor    = "x-test-1"        # GLM 5.3 Flash
reflector= "x-test-1"
planner  = "x-test-1"
cards    = "x-test-1"
checker  = "qwen3.8:27b"

[fallback]
"x-test-1" = "qwen3.8:27b"
```

- **Base URL and key** come from environment variables. They're never in the repo or the store.
- **Rate limit: 90 requests per minute**, shared by everything through one limiter. The live roles get priority, and card building only uses spare capacity.
- **Timeouts:** Observer and gate 10 s, Tutor 60 s (streamed), Reflector and Planner 180 s.
- **Fallback is loud.** If a model fails twice in a row (or returns "model not found"), its roles switch to the fallback, and the panel shows "GLM 5.3 Flash isn't responding; using qwen3.8:27b". The switch is logged.
- To verify at phase 1: the endpoint supports disabling thinking for `qwen3.5:9b`; whether JSON or structured-output mode is supported per model; the actual time-to-first-token of GLM 5.3 Flash on this endpoint.

## 13. Speech

- **Push-to-talk:** a hotkey, or holding the mic button, records in the side panel. The audio goes to the companion, mlx-whisper transcribes it, and the text appears in the input box for you to edit and send. Audio is discarded after transcription.
- **Voice:** kokoro-onnx, used for offers, and for replies only when "speak replies" is on. Audio plays in the side panel.
- To verify: microphone permission from a Chrome side panel. If the panel can't prompt for it, grant it once through an extension page.

## 14. Failure handling

| Failure | Behaviour |
|---|---|
| Companion not running | Panel shows how to start it; no session |
| Page reader can't read the editor | Session won't start or pauses; panel says why |
| Model returns invalid output | One retry with the error, then a visible failure; nothing invalid stored |
| Model unavailable | Switch to the fallback, visibly |
| Rate limit hit | Live roles queue briefly; card building backs off; never silently dropped |
| Speech recognition mishears | You edit the text before sending (by design) |
| Reflector fails after a session | Retried at next companion start; the session log is untouched |
| Profile drifts | Hypotheses must cite events; wrap-up bullets are shown every session for you to correct |
| A site changes its page | Its reader's self-check fails loudly; the other site keeps working |

## 15. Privacy

Everything is stored locally. Sent to the model API: problem text, your code, your transcribed messages, and summaries of your learner data. Never sent: audio, or anything from outside a session.

## 16. Build order

Each phase ends with something you can use.

1. **Foundations.** Companion skeleton, SQLite schema, model client (roles, rate limiter, fallback, validation), and the phase-1 checks from §12.
   *Done when:* a test script calls every role's model and validates a structured reply.
2. **Problem cards.** The Card builder pipeline, run over the NeetCode 150, plus the pattern graph.
   *Done when:* ≥ 95% of the 150 have verified cards, and a build report lists the rest.
3. **Text tutor on LeetCode.** First, a throwaway prototype of the side panel and the otter mascot, covering every panel state (watching, offer, reply, paused, wrap-up, errors from §14), so the look is settled before the extension is built. Then: extension, LeetCode page reader, side panel chat, sessions (start, stop, soft time budget), and the Tutor on request only.
   *Done when:* you can run a real session and get Socratic help that follows your code.
4. **Memory and planning.** Event log to evidence, the Reflector, the Planner, history import, placement, between-problem choices, wrap-ups, and the Progress tab.
   *Done when:* a second session's plan clearly reflects the first.
5. **Live watching.** The Observer, the Speak gate, offers (panel and chime), quiet mode, pause and auto-end, learned thresholds.
   *Done when:* it offers help when you're truly stuck, and stays quiet otherwise, across a week of use.
6. **Voice.** Push-to-talk with Whisper; Kokoro for offers and "speak replies".
7. **NeetCode and goals.** The NeetCode page reader, goal and deadline, forecast and check-ins.

## 17. Open technical checks

Listed in their phases above; collected here:
- [ ] Base URL for the model provider (key via environment variable)
- [ ] Disabling thinking for `qwen3.5:9b`; structured-output support per model; GLM 5.3 Flash latency (phase 1)
- [ ] LeetCode editor access and run/submit responses (phase 3)
- [ ] LeetCode accepted-submissions endpoint; NeetCode completion data (phase 4)
- [ ] Microphone permission in the side panel (phase 6)
- [ ] NeetCode's editor and results (phase 7)

# Leetotter: Spec

This is the build spec. `design.html` (published at https://claude.ai/artifact/NuRZpv5EcrmcRdmnsihAcG) explains the system at a high level; this file says how to build it, what can go wrong, and in what order to build it. Where they disagree, fix the disagreement rather than picking one silently.

## 1. Scope

**In scope:** practising the NeetCode 150 and related LeetCode problems, in Python, on the LeetCode and NeetCode web editors, on one Mac (Apple M2, 16 GB).

**Out of scope:** system design practice (a future extension), other devices or sync, other coding languages, a timed mock-interview mode, rating buttons, notifications outside a session, tracking practice done outside a session.

## 2. Fixed rules and agent judgment

The system is meant to evolve as it learns about you, so most numbers in this spec are **starting defaults that the agents tune**. Only a few things are fixed.

**Fixed (changed only by you):**
- the goal and deadline
- what counts as mastered (§9), the grade table and the pinned FSRS parameters (§8), so that progress is always measured the same way
- the guardrails: help is offered only after your yes; it watches only during sessions; hypotheses must cite evidence

**Tuned by the agents:**
- **Judgment, in the agents' prompts** (no numbers): when placement ends, how much help to give and when, follow-ups after a solution, problem order and mix, and session pacing when you overrun.
- **Numeric policy values**, each starting from a default and adjusted from your history within bounds:

| Policy value | Default | Bounds | Learned from |
|---|---|---|---|
| Offer wait when circling | 4 min | 1 min to 1 min less than the pause time | ignored vs accepted offers (§6) |
| Offer wait when stalled | 6 min | same | same |
| Re-offer delay | 3 min | 1–10 min | whether re-offers get accepted |
| Pause after no activity | 10 min | 5–30 min | how long your real thinking gaps are before you resume |
| End after time away | 60 min | 30–180 min | how often you come back after a pause |
| Share of problems that get the complexity question | 50% | 0–100%, with the floor in §5 | how your `analysis` memory is doing |
| Forecast hidden for | 3 weeks of practice | 2–6 weeks | how stable your weekly pace is |
| Minimum days between check-ins | 7 | 3–14 | whether check-ins lead to a changed plan or are dismissed |

The bounds keep a bad proposal harmless; the offer waits stay below the pause time so an offer can always fire before a pause.

**How tuning works:**
- Tunable values live in the `policy` table, each with its current value, default, and a history of changes.
- The Reflector proposes changes after a session, each with a reason and the events behind it.
- Code applies them within the bounds above.
- Each change is listed in the session wrap-up, so you can see how it's evolving and push back.
- Whether a change worked is judged against the fixed measure: did your memory strength on unseen problems grow (§8)?

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

**History import** (once at setup, re-runnable): read your solved problems and dates from your logged-in LeetCode account, and NeetCode's completion state. Stored as `prior_solve` events, which inform the Planner but aren't reviews (§8). To verify: which LeetCode endpoint lists accepted submissions; whether NeetCode exposes completion data.

## 5. Sessions

### State machine

```
idle ──start(minutes)──▶ planning ──plan accepted──▶ active
active ──problem solved / given up──▶ between ──choice──▶ active | break | ended
active ──no activity for the pause time──▶ paused ──activity──▶ active
break ──you resume──▶ active
paused or break ──away for the end time in total──▶ ended (wrap-up waits for next panel open)
any ──stop──▶ ended ──▶ Reflector runs in the background
```

- **Watching happens only in `active`.** Outside a session the page readers send nothing.
- **Pause and end times** are policy values (§2): defaults 10 and 60 minutes.
- **A break** is one you chose: the panel shows it, nothing counts toward the pause, and you resume when ready.
- **"No activity"** means no code changes, runs or messages. A hidden tab or blurred window counts toward the pause timer.
- **Time budget is soft.** When it runs out, the panel asks "finish this one or stop?". Overruns are logged.
- **Off-plan problems:** if you open a problem that isn't in the plan, it becomes the active problem and is logged as `off_plan`.

### Between problems

After a solve, or once the tutor has walked you to the solution:
1. **The complexity question (optional).** The Tutor decides whether to ask, the way an interviewer would: time, space, or both. You can skip it. How often it's asked is tuned policy, but it can't be reduced for a pattern whose `analysis` isn't mastered yet, and it's asked when that pattern's analysis recall is low; otherwise analysis evidence would dry up.
2. **A wrap-up** of one or two lines: the key insight, what tripped you up.
3. **Choices**, generated by the Planner from the time left: next problem (with reason), take a break, one more past your time, stop. Clicking a problem opens it on the site where it's free.
4. **Follow-up after a solution was shown:** the Planner schedules one, its judgment: the same problem a day or more later, an unseen one with the same idea, or both. The Reflector notes which kind works for you.

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

- An offer stays in the panel until you answer or dismiss it. At most one re-offer per problem, after the re-offer delay (§2).
- **Waiting times are tuned policy (§2).** Defaults: 4 minutes for circles, 6 minutes for stalls. They adjust from history: ignored offers followed by a solve lengthen them; accepted offers that led to quick progress shorten them.

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

**Each help turn is logged** with its kind (`question`, `trace`, `analogy`, `snippet`, `partial_logic`, `full_solution`) so the attempt can be graded (§8).

### Replies

Replies are text in the panel. Voice is used for offers, and for replies only when "speak replies" is on. Then the Tutor writes a separate short spoken version, which never contains code.

## 7. Agents

Each agent is one prompt template plus a context builder plus an output validator. **Every structured output is checked against a schema.** On failure: one retry with the error shown to the model, then a loud failure (logged, shown in the panel if it affects you). Invalid output is never stored.

| Agent | Trigger | Input | Output | Model |
|---|---|---|---|---|
| Observer | each `code_changed`/`run_result` batch, at most every 5 s | card summary, the last 10 diffs, your recent messages, recent observations, recent results | `{approach_id or "new", state: progressing/circling/stalled/done, note}` | qwen3.5:9b, thinking off |
| Speak gate | Observer reports circling/stalled | observation history, learned thresholds, time since last offer, quiet mode | `{offer: bool, channel: voice/panel, reason}` | qwen3.5:9b, thinking off |
| Tutor | your message, an accepted offer, a solve | see §6 | reply text, optional spoken text, help kind, optional complexity question | GLM 5.3 Flash |
| Reflector | session ended | full session event log, current learner model, teaching memory | session summary, hypotheses added/changed (each citing event ids), teaching-attempt outcomes, user-facing bullets, one focus for next time | GLM 5.3 Flash |
| Planner | session start, between problems | learner summary (computed by code, §8), goal and forecast, minutes, unfinished problems, preferences | ordered problem list, each with reason and `diagnose`/`train`; or choices | GLM 5.3 Flash |
| Grader | problem solved or given up; complexity answer given | card approaches (with complexity and `interview_expected`), your final code and runs, the help turns for this problem, your complexity answer and reasoning | `{approach_id or "new", best_approach_reached_without_help, complexity_verdict: right / right_after_followup / wrong / wrong_reasoning / no_reasoning}`; code turns these plus the logged help kinds into the grade (§8) | GLM 5.3 Flash |
| Card builder | offline batch; on demand for drill problems | problem statement | card (§10) | GLM 5.3 Flash |
| Checker | after Card builder; when the Tutor analyses a new approach | card or approach | agree/disagree per label, with reasons | qwen3.8:27b |

## 8. Learner model

**Numbers come from code, judgment from the model.** Models only label what happened on each attempt (unseen or not, how much help, which approach, right or wrong). Code turns those labels into the numbers below.

### Memory strength (FSRS)

Each (pattern, skill) pair, with skill being `solve` or `analysis`, is tracked with **FSRS**, the well-tested memory model from spaced-repetition research that Anki uses. For each pair it gives:
- **stability:** how many days until your chance of recalling it drops to 90%
- **recall now:** your current chance of recalling it, from the time since the last attempt and the stability

A clean solve after a gap grows stability, and a longer gap grows it more. Several attempts on the same day add little. A failure collapses it. Being away lowers "recall now" but not stability.

**Each attempt at an unseen problem is one review**, graded by a fixed table (§2):

| What happened | Grade |
|---|---|
| Solved without help, with an approach at least as good as the one interviewers usually expect | Good |
| Solved after questions or traces only; or solved without help, but with a worse approach when the card marks a better one as usually expected | Hard |
| Needed snippets or partial logic, was shown the full solution, or gave up | Again |
| The session ended mid-problem | no review; the problem stays unfinished |
| Complexity answer right, with right reasoning | Good (`analysis`) |
| Complexity answer right after a follow-up question | Hard (`analysis`) |
| Complexity answer wrong, or right with wrong reasoning | Again (`analysis`) |

- An attempt is graded by the best approach you reached yourself in it, so writing brute force and then optimising it yourself counts as Good.
- The approach rule only applies when the card's `interview_expected` label isn't `needs_review`.
- If you give a complexity answer without reasoning, the Tutor asks for the reasoning before it's graded.
- Easy is never used: no signal for it is reliable enough.
- An `analogy` help turn counts like questions or traces. Being asked for your reasoning isn't a follow-up question.
- Help, approach and verdict labels come from the Grader (§7) and the logged help turns, never from the Observer's live guesses.
- **Several patterns per problem:** a `solve` review goes to every pattern in the card's `patterns`, all needed for the solution; an `analysis` review goes to the same patterns.
- **The pattern set is frozen** once built from the 150 (§10). Drills and card corrections map onto existing patterns; changing the set needs your OK, since it moves the measure.
- **Recall now** uses the current time against the last review's timestamp, as `py-fsrs` computes retrievability. A pattern's analysis recall counts as low below 0.9, FSRS's own retention target.
- **You see every grade.** The between-problem wrap-up shows each review's grade with its reason, and you can correct it (for example "I didn't need that hint", or "that was a guess"). The correction changes the review log, and FSRS state is recomputed.
- Drill problems count as unseen. Repeats of problems you've seen and imported prior solves are **not** reviews; they are inputs to the Planner and Reflector. The learner summary also lists prerequisite patterns implied by your strong patterns, as a plain list with no numbers.
- **Unseen** means you haven't attempted the problem in a Leetotter session before. Imported past solves don't make a problem seen; if you clearly remember a specific solution, the Reflector can note it.

**FSRS settings:** the published default parameters of one pinned `py-fsrs` version, stored in the database with that version, so an upgrade can't move the measuring stick. No learning or relearning steps (they're for minute-scale flashcard drills). Fitting the parameters to your own history (FSRS's optimizer) happens only with your OK, since it changes the measure.

**The review log is the source of truth.** FSRS state is always recomputed from it, so a correction removes or changes reviews rather than patching numbers.

### Judgment

**The Reflector writes**:
- hypotheses about you, such as "visited marking: suspected weak" or "explains the idea well, struggles to implement it"
- teaching-memory entries
- the session summary

Every hypothesis cites event ids. A hypothesis that cites nothing is rejected by the validator. Anything in a session can inform hypotheses, including your explanations; only the review grades feed the numbers.

**Your notes** (preferences, corrections, goal) override both. The panel has an "exclude this session from measurement" action (for example after a bad day), which removes that session's reviews from the FSRS recomputation.

**Placement** has no fixed length and uses light help, since its job is to find where you are. The Planner ends it when it judges it has enough evidence across patterns to plan well, and says so.

**The Planner revisits by recall now:** patterns most likely to be rusty come first, for example after a break. How it weighs that against new material is its judgment.

## 9. Goal and forecast

- **Goal:** the NeetCode 150, with a deadline set at first run and changeable at any time.
- **Mastered** (fixed, §2): for every pattern, for both `solve` and `analysis`: stability of at least 30 days, at least 2 Good reviews, and the latest review isn't Again (§8). And each of the 150 has an accepted submission of your own (any approach), written in a session without the full solution being shown; hints are fine. Imported submissions don't count.
  - 30 days means you'd very likely still recall it a month later. It's reached with two Good reviews at least about two weeks apart.
  - 2 Good reviews, because one clean solve could be luck.
  - A break alone doesn't remove mastered; the next attempt decides, and the Planner revisits rusty patterns first.
  - Before these values are used, they're checked against the real library on realistic schedules (§17).
- **Pace:** learned from completed sessions: sessions per week, minutes per session, and problems per hour.
- **Forecast:** simulated in code. Each unmastered (pattern, skill) is played forward with FSRS, using your observed pace, your observed share of Good, Hard and Again, and the problems left to do. Running it many times gives a range of finish dates. Calendar time matters, not just problem count, since stability needs spaced practice. Hidden for a policy-set period (§2, default 3 weeks).
- **Progress states:** strong = meets the mastered conditions for that (pattern, skill); weak = has reviews but doesn't; suspected weakness = an open Reflector hypothesis; no evidence = no reviews yet.
- **Check-in:** at session start, only when the forecast's likely date passes the deadline. How often it raises it is tuned policy (default: at most once a week unless it gets worse). It offers concrete options:
  - move the date by N days
  - add about X problems a week, said as extra sessions at your usual length
  - narrow to the most-tested patterns

  The options come from the same simulation.

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
random_input: Python code generating one valid random input (used for the cross-check)
```

**Build pipeline:**
1. The Card builder writes the card, including about 20 test cases with edge cases.
2. **Run every approach against every test** in the runner (subprocess, time and memory limits, no network). All approaches must agree.
3. **Cross-check on random inputs**: the simplest approach acts as an oracle for the others on about 200 random inputs.
4. **The Checker reviews** the complexity and labels. If they disagree, regenerate that part once. If they still disagree, the card is stored with `needs_review` and the Tutor is told which labels are uncertain.
5. Cards that fail steps 2–3 twice are not used, and are listed in a build report.

The pattern graph is built from the cards' `patterns` and `analysis_concepts`. Its seeds are NeetCode's categories, split into finer patterns by the Card builder. Prerequisite edges are proposed by the model and checked by the Checker.

New approaches you find during a session are added to the card after the Checker agrees. If you say something on a card is wrong, the correction goes through the same checks (runner and Checker) before it's applied; if they disagree, the card is marked `needs_review` and you're told why.

**Problems without a card** (a drill, or any off-plan problem outside the 150) get one built on demand. The Tutor waits for it rather than solving the problem live, and the panel says notes are being prepared. Like all card building, this uses spare rate-limit capacity, which session setup normally has.

## 11. Store (SQLite)

| Table | Holds | Written by |
|---|---|---|
| `events` | every signal, observation, offer, help turn, result; append-only | code |
| `sessions` | start, end, budget, plan, summary, user-facing bullets | coordinator, Reflector |
| `reviews` | (pattern, skill, grade, event_id): the log that FSRS state is recomputed from | code |
| `fsrs` | pinned library version and parameters | set at install; changed only with your OK |
| `hypotheses` | text, status, cited event ids | Reflector |
| `teaching` | pattern, framing, support level, outcome, later recall | Reflector |
| `notes` | preferences, corrections, goal | you, via the panel |
| `policy` | tunable values: current, default, bounds, change history with reasons | Reflector (proposes), code (applies within bounds) |
| `cards`, `patterns`, `edges` | the map | Card builder, Checker |
| `model_calls` | role, model, latency, tokens, validation result; full prompt text kept for the last 10 sessions only | model client |

About 1 MB per session. The schema is versioned with migrations from the first release.

If the store ever grows large, old sessions are compacted: keystroke detail is dropped; runs, submissions, help turns, transcripts, final code and any event cited as evidence are kept. This isn't built until it's needed.

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
| Planner fails at session start | The panel says so and offers a code-ranked list instead (lowest recall now first); the failure is logged |
| Grader fails | The attempt is stored ungraded and shown in the wrap-up for you to grade; it's never guessed |
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
4. **Memory and planning.** Event log to reviews and FSRS memory strength, the Reflector, the Planner, history import, placement, between-problem choices, wrap-ups, and the Progress tab.
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
- [ ] LeetCode "similar questions" and topic-tag data for drills (phase 4)
- [ ] With the real `py-fsrs` library, simulate realistic schedules (about 7 problems a week with gaps of 2–7 days, plus a two-week and a three-month break, at 50%, 70% and 90% clean solves) and confirm that "mastered" (§9) is reachable and behaves sensibly. Include the limited supply of unseen problems per pattern, with and without drills. If not, stop and bring the results to the user (phase 4)
- [ ] Microphone permission in the side panel (phase 6)
- [ ] NeetCode's editor and results (phase 7)

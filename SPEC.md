# Leetotter: Spec

This is the build spec. `design.html` (published at https://claude.ai/artifact/NuRZpv5EcrmcRdmnsihAcG) is the product page: what Leetotter does and how it works, in plain words. This file says how to build it, what can go wrong, and in what order to build it. Where they disagree, fix the disagreement rather than picking one silently.

## 1. Scope

**In scope:** practising the NeetCode 150, plus unseen problems beyond it as transfer checks, in Python, on one Mac (Apple M2, 16 GB). NeetCode's web editor first; LeetCode's later.

**Out of scope:** system design practice (a future extension), other devices or sync, other coding languages, a deadline or finish-date forecast, speech (you dictate with MacWhisper; nothing speaks back), a timed mock-interview mode, rating buttons, notifications outside a session, tracking practice done outside a session.

**Why it exists.** Practising on NeetCode with a strong AI in the browser sidebar already works. Its gaps are what Leetotter closes: the AI has no memory of what it taught you or how you did; nothing suggests what to solve next (following NeetCode's list keeps you on one problem type for a long stretch); problems and weak spots are never revisited; there's no measure of retention or of how your skill changes; and there's no interviewer to discuss complexity with. Beyond closing those gaps, it should add as little friction as possible. Every feature is judged against this.

**The aim:** be a tutor that practises like an interview. Get you to solve the 150 on your own, as evenly across categories as possible, and keep you there, so you're always ready for an interview. Leetotter's value is memory, planning and measurement; solving help itself is what any strong model can give. It should be sustainable: anything after a solve feels like a short conversation, never homework.

**The guiding principle** for every judgment call (by the agents, and in this design): maximize learning value and interview success while minimizing cognitive load.

## 2. Fixed rules, settings and judgment

**Fixed (changed only by you):**
- what counts as 100% (interview-ready, §9), the grade table and the pinned FSRS parameters (§8), so progress is always measured the same way
- the guardrails: hints come only after your yes; it watches only during sessions; hypotheses must cite evidence

**Settings** (yours to change in the panel; every nudge and what followed is logged, so they can be revisited from real data later):

| Setting | Default | Why this value |
|---|---|---|
| Independent solving time before any nudge on a problem | 15 min | Your choice: time to read, ideate and settle on a strategy. Interview-prep guides budget about 10 min to understand and plan, so this leaves some slack |
| Quiet time and gap | 5 min | One number for two jobs: after the solving time, 5 quiet minutes count as stuck; and after any nudge or help, 5 minutes pass before the next nudge. Guides suggest asking for a hint after 2–5 min truly stuck; you think silently and it sees only edits, so the top of that range |
| Chime | on | Off leaves a silent panel message |

**Judgment, in the agents' prompts** (no numbers): when placement ends, how much help to give and of what kind, which follow-up question to ask after a solve (if any), problem order and mix, and pacing when you overrun.

## 3. Components

```
Chrome extension (TypeScript, Manifest V3)
├── page readers      content scripts for neetcode.io (first) and leetcode.com (later)
├── side panel        timer, chat, nudges, problem suggestions, recap, Progress tab
└── background worker relays everything to the companion over one WebSocket

Companion (Python, runs natively on localhost; starts at login)
├── coordinator       session state machine, nudge rules; decides which agent runs, with what context
├── agents            Observer, Tutor, Grader, Reflector, Planner, Reference writer, Checker
├── model client      one OpenAI-compatible client; per-role model config, rate limiter
└── store             one SQLite file
```

**The companion starts at login** through a macOS launch agent, so the extension always finds it. It's light when idle. If it isn't running, the side panel says so plainly ("Tutor service isn't running. Start it with …") and does nothing else.

**Nothing executes model-written code.** Leetotter never runs your code (NeetCode does, when you press Run or Submit) and keeps no solution code of its own (§10), so there's no code runner and no sandbox.

**The extension never calls the model API or stores learner data.** It reads pages, shows UI, and forwards events.

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
| `focus_changed` | problem tab active or not, Chrome window focused or not |

**How:** a script in the page's main world reads the editor's model directly and watches the site's own run/submit network responses, which is sturdier than scraping the DOM. This must be checked per site before the phase that builds that reader:

- [ ] NeetCode: which editor it uses, whether its model is reachable from the main world, and how run/submit results can be observed (smoke test, before phase 3).
- [ ] LeetCode: confirm the editor (Monaco), its model, and the run/submit responses (phase 4).

**Fail loudly.** Every reader runs a self-check when a problem page opens: can it find the editor, read the code, and identify the problem? Is its run/submit response hook installed? If not, the side panel names the check that failed (for example "Can't read the editor on this page"), and the session refuses to start or pauses. It never tutors without being able to read the code. And if a run or submit request goes out with no result seen within 30 s, the panel shows "Can't see run results on this page" and the session pauses, so a solve is never silently missed.

**History import** (on first run only): your solved problems and dates from NeetCode and from your logged-in LeetCode account, as far as each exposes them. Stored as `prior_solve` events. They aren't reviews, since how much help you had is unknown (§8). They mark problems as seen, so they're never used as unfamiliar transfer checks, and they tell placement where to start. To verify: whether NeetCode exposes completion data; which LeetCode endpoint lists accepted submissions.

## 5. Sessions

### State machine

```
idle ──start(timer)──▶ suggesting ──go──▶ active
active ──"done" after an accept, move on, or full solution shown──▶ between ──go on the next suggestion, or open a problem──▶ active
between ──pause or end choice──▶ paused | ended
suggesting | between ──swap──▶ the same state, with a new suggestion
active | between ──pause button, focus lost (active only), or reader self-check fails──▶ paused ──resume──▶ the state it paused from
any ──end──▶ ended ──▶ the Reflector writes the recap
```

- **Watching happens only in `active`.** While paused, the page readers send only `focus_changed` and `submit_result`, so a focus pause can resume and a solve is never missed; outside a session they send nothing.
- **The timer** counts session time except paused time. It's shown in the panel with pause and end buttons. The solving time and quiet time (§6) count active time only.
- **Paused** happens when you press pause, when the reader's self-check fails, or, in `active` only, when the problem tab isn't the active tab or Chrome isn't the focused window. Clicking into the side panel keeps the window focused. Focus pauses resume on their own when you come back, and self-check pauses when the check passes again; a pause you pressed waits for you to resume. A pause never ends the session by itself; if you leave it paused, the panel offers resume or end next time you open it. A Tutor reply already streaming finishes and is shown.
- **On resume,** the reader re-runs its self-check and sends a fresh `code_snapshot` before anything else, so help and grading never use stale code. An accepted submission seen while paused counts as the solve, taking effect on resume.
- **Ending** keeps everything graded so far. A problem left mid-way when the session ends gets no review and stays unfinished; ending is never giving up.
- **Solved** means an accepted submission. **Given up** means you pressed "move on" on an unsolved problem; that is evidence and is graded (§8).
- **After an accept you can keep improving** your solution (for example, optimising your brute force): the panel shows a "done" button, and you stay in `active` until you press it, open another problem, or end. The `solve` grade is written when you leave the problem, so later accepted submissions count.
- **"That works. Can you make it faster?"** When the accepted approach is below what interviewers usually expect (§10), the Tutor says so the way an interviewer would, in one line that doesn't reveal the approach, and you can talk it through: unlike an interview, the point is to learn. Help in that conversation counts by the usual rules (§8). You can press done at any time; the problem then comes back later.
- **Once the full solution is shown,** code writes the Again review at that moment (§8), with no Grader call, so it holds even if you end right after. You can type it in and submit it, which doesn't change the grade, or move on.
- **Time budget is soft.** When the timer runs out, the panel asks "finish this one or stop?". Overruns are logged.
- **Off-plan problems:** if you open a problem other than the suggested one, it becomes the active problem and is logged as `off_plan`. The problem you left is graded if you had an accepted submission on it; otherwise it stays unfinished, with no grade.

### Session start

1. You set the timer and press go.
2. The Planner suggests **one problem**, by title only. Its reason is kept internally, never shown, since a reason like "checking two pointers" gives the approach away.
3. You **go** with it or **swap** it for another suggestion. Swaps are logged as preferences.
4. In placement (§8), the panel says so.

### Between problems

After a solve, or once the Tutor has walked you to the solution:
1. **Sometimes, one follow-up question,** the way an interviewer would ask it: the time and space complexity; the complexity of another approach; how else it could be done and the trade-offs; a changed constraint (sorted input, a stream, tight memory); a better approach worth knowing, offered only when it teaches a reusable idea (prefix sums, a monotonic stack), never a one-off trick; or what in the problem signals this approach, which trains the pattern recognition interviews test. There's no set number: the Tutor weighs the load so far (how hard the problem was, how long you spent, how long the discussion ran) against whether there's something real to learn, so after a long, tough problem or a trivial one there's often nothing, and a comfortable solve with an idea left on the table may get a couple. It leans toward complexity where the category's `analysis` is weak or untested (§8). You answer in your own words; there's no multiple choice, since a guess could pass as knowing. **One rule in code:** on a first attempt at an unseen problem in a category whose `analysis` isn't strong, if you solved it without being shown the solution, the question is a complexity question (the Tutor picks the wording: your approach or another one). Otherwise analysis evidence would depend on chance. After a long, tough problem the Tutor may postpone it to the next first attempt in that category; the postponement is logged. You can skip it. If you give a complexity answer without reasoning, it asks for the reasoning before it's graded.
2. **Optionally, a line of reflection** (the key insight, or what tripped you up), when it's worth saying. No grades are shown here.
3. **The next suggestion,** from the Planner and the time left: go or swap, plus pause and end, and when time is up, one more past your time. Going opens the problem on NeetCode.

### Session end: the recap

When a session ends, the Reflector reviews it (§8) and writes a **very short recap**: a few bullets on what it noticed, only when there's something worth saying; a session that went smoothly may get none. While it's being written, the panel shows the otter preparing notes, so you know it's coming. It appears when ready, or the next time you open the panel if you've closed it. You can correct or dismiss any bullet; corrections become notes that override the Reflector (§8).

## 6. Help

### Nudges

A nudge is a soft chime plus an "I need help" button in the panel. The nudge itself never contains a hint; help starts only when you press the button or ask.

The coordinator applies these rules in code, from the Observer's states (§7) and the event stream:
- **No nudges during the solving time** of a problem (setting, default 15 min of active time).
- **At its end, one check: have you gotten anywhere?** If you haven't written anything meaningful, or the Observer doesn't judge you to be progressing, a stuck episode starts and you get a nudge then.
- **After it, a stuck episode starts** when the tab has been focused with no activity (no code changes, runs or messages) for the quiet time (setting, default 5 min, counted from the end of the solving time), or when the Observer reports `circling` or `dead_end`.
- **One nudge per stuck episode,** and the gap (the same setting, default 5 min) after any nudge or help before the next nudge.
- **An episode ends** on any activity, if it started from idleness; when the Observer reports `progressing` again, if it started from `circling` or `dead_end`; and always when you solve or ask for help. While an episode is open, no new one starts. Paused time doesn't count toward quiet time or the gap. The nudge stays visible until you press it or the episode ends, then fades.
- **Asking for help yourself counts as a nudge:** it closes the episode. Quiet time and the gap both count from the Tutor's last reply, so you get a full quiet period to think about a hint before any nudge.

### Pressing "I need help"

- **The first press** gets the smallest useful hint in the chat: just enough to get you unstuck, never the solution. You can reply to it like any chat message.
- **Each further press** gets the next, more concrete hint right away (see "How much help"), with no questions first. Like NeetCode's own hint tabs, it trusts you to use hints sparingly; the grade already records how much help you used. The button stays in the panel while you work on the problem.

### How much help

There's no fixed hint ladder in code. The Tutor works live from the problem and your code, the way a strong model in a chat does. It gets:
- the problem statement and the problem's reference (§10)
- your current code and recent observations
- the time spent, the time left in the session
- your history with this problem and category, the framings that worked before, and open Reflector hypotheses

**It diagnoses before it helps.** Its instructions list the gaps to tell apart, from your code and what you say:

| Gap | What helps |
|---|---|
| Misread the problem (a missed constraint, a different question) | Re-read it with an example |
| Missing a tool (a heap, union-find, Dijkstra) | Teach the tool briefly, then hand the problem back |
| Weak intuition (no idea where to start) | Build the idea up |
| Wrong intuition (committed to a path that can't work) | Steer off it, then build intuition for a new path |
| Stuck optimising (brute force works, the better idea won't come) | "What work is repeated?" |
| Can't write the code (idea right, implementation won't come) | Help with structure and Python, not the idea |
| Bugs and edge cases (nearly right) | Help you find it yourself: "try the smallest input" |

Brute force is never a wrong path. Going straight for the optimal solution is fine; only when it has left you with nothing working does the Tutor suggest getting a brute force working first. The Tutor follows your approach, even when it isn't one the reference lists.

From the diagnosis it chooses the next move, getting more concrete only as needed: a question, a trace or illustrative example, an analogy, a code snippet, and eventually the full solution. Its instructions also say:
- help should match how long and how hard you've struggled
- weigh the value of more struggle against the time left in the session
- after a solution is shown, add only the explanation you still need, with no repeated Socratic process

**Each help turn is logged** with its gap and kind so the attempt can be graded (§8): `question`, `trace`, `analogy`, `teach_tool` (explaining a data structure or algorithm you didn't know), `syntax` (Python mechanics, such as how `heapq` is called, never the algorithm), `snippet`, `partial_logic`, `full_solution`. The Tutor labels each turn; the Grader checks the labels against the turns' text and corrects any that are wrong, and corrections are logged.

**Leetotter's Tutor is the only helper during a session.** It replaces any other AI assistant you'd use while solving: outside help is invisible to Leetotter and would make your progress look better than it is. (Chrome shows one side panel at a time anyway.)

**Replies** are text in the panel, and nothing is spoken. You dictate with MacWhisper into the chat box like any text field.

## 7. Agents

Each agent is one prompt template plus a context builder plus an output validator. **Every structured output is checked against a schema.** On failure: one retry with the error shown to the model, then a loud failure (logged, shown in the panel if it affects you). Invalid output is never stored.

| Agent | Trigger | Input | Output | Model |
|---|---|---|---|---|
| Observer | once when the solving time ends (for the §6 check), then each `code_changed`/`run_result` batch, at most every 5 s | reference summary, the diffs since its last call plus the current code, your recent messages, recent observations, recent results | `{approach_id or "new", state: progressing / circling / dead_end, note}`; the approach and note go to the Tutor as recent observations. `circling` only once a loop has held (the same failure across several runs, rewriting the same block); `dead_end` only for an approach that can't work, never for brute force | qwen3.8:27b, thinking off |
| Tutor | your message, a press of "I need help", a solve | see §6 | reply text, gap, help kind, optional follow-up question | GLM 5.3 Flash |
| Grader (two, independently) | two separate calls: the `solve` grade when you leave a problem you solved or moved on from (not after a full solution, which code grades), and the `analysis` grade when a complexity answer is given (if one is). A skipped question never holds up the `solve` grade | the problem statement, the reference's approaches (with complexity and `interview_expected`), all your submissions and runs on the problem, the help turns for this problem with their labels, your complexity answer and reasoning | `solve` call: `{best_self_approach_id or "new", meets_expected: bool, corrected_help_labels}`; `analysis` call: `{complexity_verdict: right / right_after_followup / wrong / wrong_reasoning / no_reasoning}`. `meets_expected` says whether that approach's time complexity is no worse than the best approach the reference marks `usually`, with space breaking ties; the Grader judges a "new" approach the same way. Code turns this plus the logged help kinds into the grade (§8). Two graders on different model families judge each attempt independently (§8) | GLM 5.3 Flash and DeepSeek V4.1 Flash |
| Reflector | session ended | full session event log, current learner model, teaching memory | session summary, hypotheses added/changed (each with an optional category and skill, and citing event ids that must exist), teaching-attempt outcomes, recap bullets (possibly none) | GLM 5.3 Flash |
| Planner | session start, between problems, swap | learner summary (computed by code, §8, including each category's recent results by difficulty, such as "Trees: Medium Good, Good; Hard Again, 2 weeks ago"), imported history, time left, unfinished problems, preferences | one suggested problem with an internal reason | GLM 5.3 Flash |
| Reference writer | offline batch for the 150; on demand for other problems | problem statement | reference (§10) | GLM 5.3 Flash |
| Checker | after the Reference writer; when the Grader meets a new approach | reference or approach | agree/disagree per label, with reasons | DeepSeek V4.1 Flash |

## 8. Learner model

**Numbers come from code, judgment from the model.** Models only label what happened on each attempt (how much help, which approach, right or wrong). Code turns those labels into grades and the numbers below.

### Two memories, both FSRS

**FSRS** is the well-tested memory model from spaced-repetition research that Anki uses. For each item it gives:
- **stability:** how many days until your chance of recalling it drops to 90%
- **recall now:** your current chance of recalling it, from the time since the last review and the stability

A clean solve after a gap grows stability, and a longer gap grows it more. Several reviews on the same day add little. A failure collapses it. Being away lowers recall now but not stability.

Leetotter keeps two kinds of item:

1. **Each problem.** *Can you still solve this one?* Every graded attempt is a review, repeats included. A repeat you solve cleanly from memory is Good, so its next retry is pushed further out; that's how suspected memorisation becomes spacing rather than being ignored. This schedules retries; it isn't part of the finish line (§9).
2. **Each (category, skill),** with skill being `solve` or `analysis`. *Can you do new ones?* Only first attempts at problems you hadn't seen before count, since a repeat can't show transfer. This drives the Planner's transfer checks and the Progress map's indicators. It's a heuristic: FSRS is built for repeated reviews of one item, and established skill-mastery models (knowledge tracing, Elo-style ratings) need far more data than one learner produces, so none would be better calibrated here. Every first attempt is logged with its difficulty, so the rule can be compared with a simple fitted model once there are a few hundred attempts.

### Grades

Each graded attempt is one review, by a fixed table (§2):

| What happened | Grade |
|---|---|
| Solved without help, with an approach at least as good as the one interviewers usually expect | Good |
| Solved after questions, traces, analogies or a taught tool only; or solved without help, but with a worse approach when the reference marks a better one as usually expected | Hard |
| Needed snippets or partial logic, was shown the full solution, or gave up | Again |
| The session ended mid-problem | no review; the problem stays unfinished |
| Complexity answer right, with right reasoning | Good (`analysis`) |
| Complexity answer wrong or incomplete at first, then right after one guiding question from the Tutor that doesn't state the answer | Hard (`analysis`) |
| Complexity answer wrong, or right with wrong reasoning, or right only after the Tutor stated it | Again (`analysis`) |

`syntax` help doesn't lower a grade: an interviewer would tell you how `heapq` is called.

- A `solve` grade goes to the problem, and, on a first attempt at an unseen problem, also to its category's `solve`. For the category, a Good on an Easy problem counts as Hard: a category review is a different problem each time, and an easy one says less about the skill. (Established skill models all correct for item difficulty; this is the simple version of that.)
- An `analysis` grade goes to the category's `analysis`, only on a first attempt (you may remember a seen problem's complexity), and only when the `solve` grade wasn't Again and the Tutor hadn't stated the complexity earlier in the attempt. The Tutor can still ask on repeats, ungraded. Questions about another approach's complexity are graded the same way; questions about alternatives and changed constraints are discussion, not graded.
- **"What interviewers usually expect"** means the best approach the reference marks `usually`, not the absolute optimum: a one-off trick is never required (§10).
- An attempt is graded by the best approach you reached yourself, so writing brute force and then optimising it yourself counts as Good.
- Only help given before that solution counts toward the grade. Help afterwards doesn't lower it.
- An answer with no reasoning even after the Tutor asks for it is treated like skipping the question: no review. Being asked for your reasoning isn't a follow-up question.
- **Unchecked references** (`needs_review` labels, or not written yet): its labels can't be relied on, so a `solve` grade is at most Hard, and no `analysis` grade is given. It still counts toward coverage (§9): the graders judge `meets_expected` from the problem itself, so nothing waits on a check.
- Every reference marks at least one approach `usually` (§10).
- FSRS's Easy rating is never used: no signal for it is reliable enough.
- Help, approach and verdict labels come from the Graders (§7) and the logged help turns, never from the Observer's live guesses.
- **Two graders.** GLM 5.3 Flash and DeepSeek V4.1 Flash each grade every attempt independently. If they agree, that's the grade. If they disagree, the lower grade is stored (so a problem comes back sooner rather than being credited too early), and the disagreement is noted in that problem's history. The share of disagreements shows in the Progress tab as a health signal: if it's high, something in grading needs fixing. Two models from different families catch each other's random mistakes and quirks, though not a blind spot they share.
- **Unseen** means the problem has no graded attempt and no imported solve. An unfinished attempt doesn't make it seen, so its first graded attempt still counts as first. A problem you studied somewhere the import can't see counts as unseen; that makes the transfer measure slightly optimistic, accepted in exchange for never asking. Whether a review is a first attempt is stored with the review when it's written, so corrections and session exclusions never turn a later attempt into a first one.
- **Grades aren't shown after a problem.** Wherever they're shown, they're in plain words, so they can't be confused with problem difficulty: Good is "solid", Hard is "shaky", Again is "missed". The FSRS names stay in code. The Progress tab keeps a per-problem history with each grade and its reason, where you can correct one (for example "I didn't need that hint", or "that was a guess"). The correction changes the review log, and FSRS state is recomputed.

**FSRS settings:** the published default parameters of one pinned `py-fsrs` version, stored in the database with that version, so an upgrade can't move the measuring stick. No learning or relearning steps (they're for minute-scale flashcard drills). Fitting the parameters to your own history (FSRS's optimizer) happens only with your OK, since it changes the measure. **Recall now** is computed by `py-fsrs` from the current time and the last review. A problem counts as due, and a category as rusty (§9), below 0.9 recall now, FSRS's default desired retention, stored with the pinned parameters.

**The review log is the source of truth.** FSRS state is always recomputed from it, so a correction removes or changes reviews rather than patching numbers.

### Judgment

**The Reflector writes**:
- hypotheses about you, such as "visited marking: suspected weak", "explains the idea well, struggles to implement it", or a gap that keeps recurring ("edge cases in linked-list problems")
- teaching-memory entries: which framings and kinds of help worked
- the session summary, and the recap (§5)

Every hypothesis cites event ids. A hypothesis that cites nothing is rejected by the validator. Anything in a session can inform hypotheses; only the review grades feed the numbers.

**Your notes** (preferences, corrections, including corrections to recap bullets) override both. The panel has an "exclude this session from measurement" action (for example after a bad day), which removes that session's reviews from the FSRS recomputation.

### Planning

- **Placement** has no fixed length and aims for breadth: sampling every category before going deep, starting from your imported history. Help is offered as usual; it's recorded and lowers the grade, so it doesn't spoil the measurement. The Planner ends placement when it judges it has enough evidence across categories to plan well, and says so.
- **What gets retried (a rule in code, so retries can't swamp new problems):** a problem comes back for its own sake only when it isn't yet done on your own (§9): you needed real help, or solved it only with brute force. Beyond that, a category whose own `solve` recall now has dropped below 0.9 (rusty) gets one refresh, by default a variation: an unseen problem with the same pattern, which also counts as transfer evidence; a done problem is used for the refresh only when the Planner judges it worth redoing. Problems you've done on your own aren't retried just because their recall dropped; their memory still updates whenever they come up.
- **Balance:** among the problems these rules allow, the weakest categories come first. How it weighs them is the Planner's judgment.
- **Mixing:** for a category that's new to you or very weak, the first one or two problems can come back to back; after that, categories are mixed. Mixing makes you practise working out *which* approach applies, which is the core interview skill, and it's one of the better-supported findings in learning research (interleaving). Leetotter never names a problem's category or pattern before you've solved it.
- **Your imported solves** tell the Planner what you've seen, but don't count as tested: Leetotter tests them itself, by the same problem (which counts toward coverage) or a variation (which counts toward transfer).
- **Transfer checks:** once a category looks strong on its problems, the Planner mixes in unseen problems from that category to check you can solve new ones, not just remember old ones.
- **Problems from outside NeetCode,** once in a while, for variety and realism, since interviewers don't stick to NeetCode lists. They never come from nothing: each serves a category and a pattern (favouring patterns you haven't yet shown on a new problem), and they come in especially when practice has turned repetitive, in place of another retry of a familiar idea. A first attempt at one counts toward its category like any unseen problem (§8). They're extra attempts beyond the 150, so the Planner keeps them modest. They need the LeetCode page reader (phase 4), unless the smoke test finds suitable problems on NeetCode itself.
- **Weak analysis** is tested on unfamiliar problems in that category, with a complexity question.
- **After a full solution was shown,** the problem's Again grade brings it back soon; the Planner may also suggest an unseen problem with the same idea.

## 9. Progress and readiness

Interviews give you new problems, so the finish line is solving new ones, with the 150 as the way there. Re-solving every one of the 150 from memory isn't required: at FSRS defaults that would take two clean solves of each problem two weeks apart, 300+ attempts before any misses.

- **100%, or interview-ready** (fixed, §2), means both:
  1. **Coverage:** every one of the 150 is **done on your own**: its latest graded attempt reached an approach at least as good as what interviewers usually expect (`meets_expected`), with no snippet, partial logic or full solution (questions, examples, analogies or a taught tool are fine), and an accepted submission written in a session. A brute-force-only solve doesn't count. A problem where you needed real help comes back a few days later, and only a later solve on your own counts. Imported submissions don't count.
  2. **Transfer:** every category is **strong** for both `solve` and `analysis`: stability of at least 21 days, latest review not Again, and at least one Good review on a Medium or Hard problem (by the reference's difficulty), so a run of easy problems can't make a category strong.
  - **Why 21 days:** with the pinned `py-fsrs` 6.3.2 defaults, one clean solve gives 2.3 days and two clean solves a week apart give 21.4. So 21 is the shortest bar that needs spaced, repeated evidence rather than one lucky solve. It's a judgment, not a published standard, and the simulation in §16 checks it before it's used.
  - **The goal** (not a deadline, and nothing forecasts against it): close to 100% in about six months at about 7 problems a week, about 180 attempts. The estimate for this bar is 200–250 (the 150, redos of problems you needed help on, and transfer checks), so six months lands close, and full readiness at that pace takes about 7–8 months, depending on how often you need help.
  - **Nothing announces it.** The Progress tab shows where you stand; reaching 100% isn't celebrated or pushed.
  - A break alone doesn't lower it; the next attempt decides.
- **Per-problem memory** (§8) schedules retries and spaces out problems you remember; it isn't part of the finish line.
- **The Progress tab** is a map of NeetCode's categories, laid out like NeetCode's roadmap if the smoke test confirms that's practical. A header shows how many of the 150 are done on your own and how many categories are strong. Each category shows:
  - its score: the share of its problems done on your own (it doesn't drop while you're away)
  - two indicators, for new problems (`solve`) and complexity (`analysis`): strong (as above), weak (has reviews but isn't strong), suspected weakness (an open Reflector hypothesis), no evidence (no reviews yet)
  - a small "rusty" marker when the category's own `solve` recall now is below 0.9 (§8); with the 21-day bar, that's roughly three weeks away from it
- Clicking a category shows a small trend line of its score over time, with its two indicators' history beneath, so you can see your skill changing, not just where it stands. It's computed from the review log, so nothing extra is tracked.
- Clicking a category also lists its patterns, each marked practised on a new problem or not yet, and its problems, each with its grade history.

## 10. Problem references

The NeetCode 150 is among the best-documented material in programming, and it's well inside the models' training. So there's no detailed answer book and no solution code: the Tutor and Grader work live from the problem and your code. Each problem gets a short **reference**, whose job is consistency, not knowledge: the same approach gets the same verdict every time, so progress is measured on a fixed scale.

```yaml
slug, title, site_free: neetcode | leetcode, difficulty, neetcode_category
patterns: [ids], analysis_concepts: [ids]
approaches:
  - id, name, idea (one line), time, space
    interview_expected: usually | sometimes | rarely   # at least one approach is `usually`
    trick: bool   # true for a one-off trick that only works for this problem; a trick is never `usually`
```

Categories are NeetCode's, so problems beyond the 150 are mapped onto one of them. `patterns` are the reusable, interview-relevant ideas a problem uses (sliding window, fast and slow pointers, monotonic stack…); `analysis_concepts` are finer tags for complexity. The pattern set comes from the 150's references and stays as it is: the NeetCode 150 was built to cover what interviewers test. A problem from outside NeetCode is mapped onto an existing category and existing patterns, and the Planner only picks outside problems that fit them. Patterns aren't measured; they steer the Planner and show in the Progress tab (§9).

**Writing a reference:**
1. The Reference writer writes it from the problem statement.
2. **The Checker reviews** each approach's complexity, `interview_expected` label and `trick` flag. If they disagree, regenerate that part once. If they still disagree, the reference is stored with `needs_review`, the Tutor is told which labels are uncertain, and grades follow the unchecked rule (§8).
3. **Nothing waits on you.** The Progress tab shows how many references are unchecked, next to the graders' disagreement share, and marks each affected problem. It's a health signal: a rising count means reference writing needs fixing at the source. You can settle a reference if you want; you never have to.

**Trick or pattern** is judged per approach, per problem, by whether the idea comes back elsewhere. Fast and slow pointers are a reusable pattern for a linked-list cycle or its middle (not a trick, and may be `usually`), but the same idea applied to Find the Duplicate Number is a one-off trick. Boyer–Moore majority voting is a trick. A reusable pattern is worth a follow-up even when it isn't required; a trick is never required or pushed.

**New approaches** you find during a session are added to the reference after the Checker agrees. If you say something in a reference is wrong, the correction goes through the Checker before it's applied; if they disagree, the reference is marked `needs_review` and you're told why.

**Problems without a reference** (a transfer check, or any off-plan problem outside the 150) get one written on demand: one or two model calls. For a transfer check, the Planner starts it when it suggests the problem. You never wait: the Tutor helps from the problem statement meanwhile, and the panel says the reference isn't checked yet. Like all reference writing, this uses spare rate-limit capacity.

**Revisit with evidence:** if real sessions in phase 3 show the Tutor giving wrong guidance, add verification back for what it gets wrong. If references for less famous problems beyond the 150 prove wrong, add web search for writing those.

## 11. Store (SQLite)

| Table | Holds | Written by |
|---|---|---|
| `events` | every signal, observation, nudge, help turn, result; append-only | code |
| `sessions` | start, end, budget, summary, recap bullets | coordinator, Reflector |
| `reviews` | (item: problem or (category, skill), grade, meets_expected, first_attempt, difficulty, grader disagreement, event_id): the log that FSRS state is recomputed from | code |
| `fsrs` | pinned library version and parameters, the 21-day strong threshold | set at install; changed only with your OK |
| `hypotheses` | text, optional category and skill, status, cited event ids | Reflector |
| `teaching` | category, framing, help kind, outcome, and the grade of that problem's next review | Reflector |
| `notes` | preferences, corrections | you, via the panel |
| `settings` | the settings in §2 | you, via the panel |
| `references` | the problem references | Reference writer, Checker |
| `model_calls` | role, model, latency, tokens, validation result; full prompt text kept for the last 10 sessions only | model client |

About 1 MB per session. The schema is versioned with migrations from the first release.

If the store ever grows large, old sessions are compacted: keystroke detail is dropped; runs, submissions, help turns, transcripts, final code and any event cited as evidence are kept. This isn't built until it's needed.

## 12. Models

All models are served by your provider's OpenAI-compatible endpoint; none run on the Mac.

```toml
[roles]
observer   = "qwen3.8:27b"     # thinking disabled
tutor      = "x-test-1"        # GLM 5.3 Flash
reflector  = "x-test-1"
planner    = "x-test-1"
grader     = ["x-test-1", "x-test-2"]   # two graders, independently
reference_writer = "x-test-1"
checker    = "x-test-2"        # DeepSeek V4.1 Flash
```

Models are assigned by what each job needs; there are no fallback models.

References are written once, mostly in a batch, so quality matters more than speed there: the two strongest models write and check them, from different model families so the check is independent.

- **Base URL and key** come from environment variables, never the repo or the store. The key is kept in the macOS Keychain, and the companion's launch script exports it, so it never sits in a file an AI coding agent might read.
- **Rate limit: 90 requests per minute**, shared by everything through one limiter. The live roles get priority, and reference writing only uses spare capacity.
- **Timeouts:** Observer 20 s (nudges work on a minutes timescale, so its judgment matters more than its speed), Tutor 60 s (streamed), Grader 60 s, Planner 180 s at session start and 30 s between problems (so a solve is never followed by a long wait), Reflector 180 s.
- **An unavailable model is reported, not worked around.** If a model fails twice in a row (or returns "model not found"), the panel names the model and what it affects (for example "GLM 5.3 Flash isn't responding: tutoring and suggestions are unavailable; grading continues with one grader"), and it's logged. You fix it in the configuration. What happens to each role meanwhile is in §13.
- To verify at phase 1: `x-test-2` is reachable and gives valid structured output; the endpoint supports disabling thinking for `qwen3.8:27b`, and its response time; whether JSON or structured-output mode is supported per model; the actual time-to-first-token of GLM 5.3 Flash on this endpoint.

## 13. Failure handling

| Failure | Behaviour |
|---|---|
| Companion not running | Panel shows how to start it; no session |
| Page reader can't read the editor | Session won't start or pauses; panel says why |
| Model returns invalid output | One retry with the error, then a visible failure; nothing invalid stored |
| Model unavailable | The panel names the model and what it affects. Tutor down: help is unavailable, and you can keep solving or pause. Grader down: as "Grader fails" below. Planner down: as "Planner fails" below. Reflector down: as "Reflector fails" below. Reference writer or Checker down: reference writing pauses, and a reference needed meanwhile is used as unchecked (§8) |
| Rate limit hit | Live roles queue briefly; reference writing backs off; never silently dropped |
| Observer fails | No nudges from circling or dead ends until it recovers; quiet-time nudges still work; the check at the end of the solving time uses only whether you've written anything meaningful; the panel says so |
| Checker fails | Reference writing pauses, and the panel says so; a reference needed meanwhile is used as unchecked (§8) |
| Reflector fails after a session | The recap panel says so; retried at next companion start; the session log is untouched. If a retry is still pending or failing at session start, it runs before the Planner; if it fails again, the panel says last session's review is missing and planning goes ahead without it |
| Planner fails at session start or between problems | The panel says so and suggests a code-ranked problem instead (one not yet done on your own in the weakest category, else an unseen problem in that category); the failure is logged |
| A grader fails | Retried automatically (at the next problem, then at the next companion start). If one grader still fails, the other's grade is used and the history notes it; if both fail, the attempt stays ungraded, the panel says so, and it's listed in the Progress tab. A grade is never guessed |
| Profile drifts | Hypotheses must cite events; recap bullets can be corrected |
| A site changes its page | Its reader's self-check fails loudly; the other site keeps working |

## 14. Privacy

Everything is stored locally, in one SQLite file (§11). On your Mac, Leetotter runs only the Chrome extension and the companion; the models run on the provider's servers. Sent to the model API: problem text, your code, your messages, and summaries of your learner data. Never sent: anything from outside a session. The API key lives in the Keychain, not in the store.

## 15. Build order

Each phase ends with something you can use.

1. **Foundations.** Companion skeleton, SQLite schema, model client (roles, rate limiter, validation), the launch agent and Keychain launch script, and the phase-1 checks from §12.
   *Done when:* a test script calls every role's model and validates a structured reply, and the companion starts at login.
2. **Problem references.** The Reference writer and Checker, run over the NeetCode 150.
   *Done when:* ≥ 95% of the 150 have checked references, and the rest are marked unchecked.
3. **Text tutor on NeetCode.** First, a throwaway prototype of the side panel and the otter mascot, covering every panel state (watching, nudge, reply, paused, between problems, preparing the recap, the recap, errors from §13), so the look is settled before the extension is built. Then: extension, NeetCode page reader, side panel chat, sessions (timer, pause, end, focus pauses), and the Tutor on request only. There's no Planner yet: you open a problem yourself, and it becomes the active one.
   *Done when:* you can run a real session and get help that diagnoses where you're stuck and follows your code.
4. **LeetCode page reader.** The same reader for leetcode.com, so variations and problems from outside NeetCode are available from the start of planning (you've already seen about half the 150).
   *Done when:* a session works on a LeetCode problem as it does on NeetCode.
5. **Memory and planning.** Grading, both FSRS memories, the Reflector and recap, the Planner, history import, placement, between-problem follow-ups, and the Progress tab.
   *Done when:* a second session's suggestions clearly reflect the first.
6. **Live watching.** The Observer and nudges.
   *Done when:* it nudges when you're truly stuck, and stays quiet otherwise, across a week of use.
7. **Beyond the 150.** Transfer checks and variations with on-demand references.

## 16. Open technical checks

Listed in their phases above; collected here:
- [ ] NeetCode smoke test (before phase 3): whether the problem page shows its category or pattern (which would give the approach away); the editor and reading its code; run/submit results; completion data for import; whether problems beyond the 150 are available there; the roadmap's structure; whether MacWhisper's dictation takes focus from Chrome (which would pause the session)
- [ ] Provider base URL (key via Keychain); disabling thinking for `qwen3.8:27b` and its response time; structured-output support per model; GLM 5.3 Flash latency (phase 1)
- [ ] LeetCode accepted-submissions endpoint (phase 5)
- [ ] **Simulation, before phase 5 (pure code, no model calls).** With the real `py-fsrs` library, simulate pretend learners: paces of 5, 7, 10 and 15 problems a week with gaps of 2–7 days, plus a two-week and a three-month break; 50%, 70% and 90% clean solves on new problems, with success on practised items following FSRS's recall estimate, lower on Hard problems, and set rates of brute-force-only and help-needed solves; 75 of the 150 already seen through import. Use the retry rule and variation mix from §8. Check the 100% bar (§9): how long until it's reached, against the goal of about six months at 7 a week; whether retries crowd out new problems; whether every category can reach strong. Check that every category, including small ones like Tries, can reach strong for `solve` and `analysis` with transfer checks and the follow-up question at a realistic rate, and compare 21 days with nearby values. Bring the results to the user before the bar is used
- [ ] LeetCode's editor and results (phase 4); LeetCode "similar questions" and topic-tag data for variations (phase 7)

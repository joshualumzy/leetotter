# Leetotter: Spec

This is the build spec. `design.html` (published at https://claude.ai/artifact/NuRZpv5EcrmcRdmnsihAcG) is the product page: what Leetotter does and how it works, in plain words. This file says how to build it, what can go wrong, and in what order to build it. Where they disagree, fix the disagreement rather than picking one silently.

## 1. Scope

**In scope:** practising the NeetCode 150, plus unseen problems beyond it as transfer checks, in Python, on one Mac (Apple M2, 16 GB). NeetCode's web editor only.

**Out of scope:** other practice sites, system design practice (a future extension), other devices or sync, other coding languages, a deadline or finish-date forecast, speech (you dictate with MacWhisper; nothing speaks back), a timed mock-interview mode, rating buttons, notifications outside a session, tracking practice done outside a session.

**Why it exists.** Practising on NeetCode with a strong AI in the browser sidebar already works. Its gaps are what Leetotter closes: the AI has no memory of what it taught you or how you did; nothing suggests what to solve next (following NeetCode's list keeps you on one problem type for a long stretch); problems and weak spots are never revisited; there's no measure of retention or of how your skill changes; and there's no interviewer to discuss complexity with. Beyond closing those gaps, it should add as little friction as possible. Every feature is judged against this.

**The aim:** be a tutor that practises like an interview. Get you to solve the 150 on your own, as evenly across categories as possible, and keep you there, so you're always ready for an interview. Leetotter's value is memory, planning and measurement; solving help itself is what any strong model can give. It should be sustainable: anything after a solve feels like a short conversation, never homework.

**The guiding principle** for every judgment call (by the agents, and in this design): maximize learning value and interview success while minimizing cognitive load.

## 2. Fixed rules, settings and judgment

**Fixed (changed only by you):**
- what counts as 100% (all 150 done on your own, §9) and as a strong category, the grade table and the pinned FSRS parameters (§8), so progress is always measured the same way
- the per-problem time cap (Easy 20, Medium 35, Hard 50 minutes, §5)
- the guardrails: hints come only after your yes; it watches only during sessions; hypotheses must cite evidence

**Settings** (yours to change in the panel, where tapping the otter in the header opens them):

| Setting | Default | Why this value |
|---|---|---|
| Your name | none | Asked once, on first run, and skippable. Used only in the otter's remarks, now and then; never sent to the models |
| Theme | match my Mac | The panel follows the Mac's light or dark setting. Light and Dark fix it. (Following NeetCode's own theme was dropped: you set the two independently, and reading NeetCode's theme off the page isn't worth the extra reader work.) |

**Reset** (a button in the same settings, "Reset all my progress"): erases everything personal and returns the panel to the first-run flow (your name, then the history import). It takes two steps: a warning that this can't be undone, then typing RESET. It erases `events`, `sessions`, `reviews`, `hypotheses`, `teaching`, `notes`, `model_calls` and `settings` (which includes the name and the history-import flag) in one transaction, so it either finishes or changes nothing. It keeps what isn't personal: `references`, `fsrs`, the catalog, and the API key in the Keychain.

**Judgment, in the agents' prompts** (no numbers): how much help to give and of what kind, which follow-up question to ask after a solve (if any), and problem order and mix.

## 3. Components

```
Chrome extension (TypeScript, Manifest V3)
├── page readers      content script for neetcode.io
├── side panel        timer, chat, problem suggestions, Recaps tab, Progress tab
└── background worker relays everything to the companion over one WebSocket

Companion (Python, runs natively on localhost; starts at login)
├── coordinator       session state machine, time cap; decides which agent runs, with what context
├── agents            Tutor, Grader, Reflector, Planner, Reference writer, Checker
├── model client      one OpenAI-compatible client; per-role model config, rate limiter
└── store             one SQLite file
```

**The companion starts at login** through a macOS login service (installed by `scripts/install.sh`, inspected with `scripts/status.sh`), so the extension always finds it. It's light when idle. If it isn't running, the side panel says so plainly ("Tutor service isn't running. Start it with …") and does nothing else.

**Nothing executes model-written code.** Leetotter never runs your code (NeetCode does, when you press Run or Submit) and keeps no solution code of its own (§10), so there's no code runner and no sandbox.

**The extension never calls the model API or stores learner data.** It reads pages, shows UI, and forwards events.

**One WebSocket, typed messages.** Every message has `type`, `session_id` (when in a session) and `ts`. Unknown message types are an error, logged and shown, never ignored.

## 4. Reading the pages

The page reader produces these events:

| Event | Payload |
|---|---|
| `problem_opened` | problem slug, title |
| `code_snapshot` | full code (on problem open, every 60 s while active, before every run/submit, on a help press and at time-up) |
| `problem_statement` | slug and the problem description text, read from NeetCode's Question tab (it is in the page only while that tab shows, so it can arrive after `problem_opened` and is re-sent if the text changes; best-effort, its absence is not a reader failure) |
| `run_result` | passed/failed per test case, error type and message, runtime |
| `submit_result` | accepted or not, failing case, runtime and memory |
| `focus_changed` | problem tab active or not, Chrome window focused or not |

**How:** a script in the page's main world watches NeetCode's own run/submit network traffic, and reads the code from the request bodies and from the editor. This was checked before the phase that builds the reader:

- [x] NeetCode (smoke test, 2026-10-15). The editor is Monaco, bundled and not exposed: there is no `window.monaco`, only a `MonacoEnvironment` settings object, so the model can't be read directly. The full, current code is in `localStorage["<problem-slug>_python_tabs"]`, a JSON list of editor tabs each with a `code` field, and it updates as you type (a test comment appeared in it before any Run). The reader watches writes to that key, or polls it, and falls back to `.view-lines` (`innerText`), which holds only the visible lines (15 in a short window), so it can't be the main source. Run is `POST neetcode.io/api/runCodeFunctionHttp`: the body `{data: {problemId (slug), rawCode, lang, testCases[]}}` carries the exact code, and the response is a Judge0-style list, one entry per test case, with `status.id` (3 accepted, 4 wrong answer; other ids for errors), `stdout`, `stderr`, `compile_output`, `time`, `memory` and `last_executed_test_case {input, expected_output, user_output}`. Submit is `POST neetcode.io/api/executeCodeFunctionHttp`: the body is `{data: {problemId, rawCode, lang}}`, and the response is one object with `status`, `test_case_count`, `correct_test_case_count`, and, on a failure, `last_executed_test_case` too (an accepted one adds speed and memory charts and `submissionIndex`). The reader therefore takes the code and results from these requests. The reader expects one tab in that list; if there are several, its self-check fails rather than guess. To confirm in phase 3: error and timeout statuses; whether the response for a long solution differs; that a failure passes through `correct_test_case_count` (a pass on 3 and fail on the 4th gives 3).

**Fail loudly.** Every reader runs a self-check when a problem page opens: can it find the editor, read the code, and identify the problem? Is its run/submit response hook installed? If not, the side panel names the check that failed (for example "Can't read the editor on this page"), and the session refuses to start or pauses. It never tutors without being able to read the code. And if a run or submit request goes out with no result seen within 30 s, the panel shows "Can't see run results on this page" and the session pauses, so a solve is never silently missed.

**History import** (on first run only): your solved problems from NeetCode (the list shows a solved check per problem, with no dates). Stored as `prior_solve` events. They aren't reviews, since how much help you had is unknown (§8). They mark problems as seen, so they're never used as unfamiliar transfer checks, and they tell the Planner where to start. To verify: which request or page element carries NeetCode's solved status (the list page shows it).

## 5. Sessions

### State machine

```
idle ──start──▶ suggesting ──go──▶ active
active ──"done" (after an accept, a full solution or the debrief), or skip──▶ between ──go on the next suggestion, or open a problem──▶ active
between ──pause or end choice──▶ paused | ended
suggesting | between ──swap──▶ the same state, with a new suggestion
active | between ──pause button, focus lost (active only), or reader self-check fails──▶ paused ──resume──▶ the state it paused from
any ──end──▶ ended ──▶ the Reflector writes the recap
```

- **Watching happens only in `active`.** While paused, the page readers send only `focus_changed` and `submit_result`, so a focus pause can resume and a solve is never missed; outside a session they send nothing.
- **The timer** counts session time except paused time. It's shown in the panel with pause and end buttons. The time cap (below) counts active time only, so a focus pause stops its countdown.
- **Paused** happens when you press pause, when the reader's self-check fails, or, in `active` only, when the problem tab isn't the active tab or Chrome isn't the focused window. Clicking into the side panel keeps the window focused. Focus pauses resume on their own when you come back, and self-check pauses when the check passes again; a pause you pressed waits for you to resume. A pause never ends the session by itself; if you leave it paused, the panel offers resume or end next time you open it, whenever that is.
- **One session, one tab.** A session belongs to the NeetCode tab it was started or resumed in; only that tab is watched, and the panel is opened by you, from the toolbar icon, on a NeetCode tab. In any other NeetCode tab the panel says a session is running in another tab and offers a button to go to it, and starting or resuming there is refused, so two tabs never run in parallel. Closing the session's tab pauses the session (reason: tab closed; time stops). It does not end it, since a tab can be closed by accident. To carry on, open NeetCode in a new tab and resume there: the session moves to that tab. Pausing because the extension lost its connection to the companion (reason: disconnected) is the one pause the extension resumes by itself, and only when it reconnects while the session's tab is still open. If Chrome itself was closed, the tab is gone, so it waits for you like any other.
- **If the Mac shuts down or the companion dies mid-session,** nothing is lost and no time is counted for the gap. On the next start the session comes back paused (reason: restart), with its time up to the last thing it recorded, and the panel offers resume or end, however long it has been. It is never ended for you.
- **Ending is always yours:** the header keeps ⏸ (pause: you're coming back soon; ▶ when resuming is yours) and a labelled "End session" button, so a new user never has to guess what a bare stop icon means. Neither replaces the other.
- **Coming back to a paused session** (the tab was closed, the Mac restarted, or you left it paused): instead of a running timer, the panel shows a card, "Pick up where you left off?", with the problem and the time so far (for example "Daily Temperatures, 23 min") and two buttons: Resume, and "End and start a new one", which ends that session and shows the normal start card. A Tutor reply already streaming finishes and is shown.
- **On resume,** the reader re-runs its self-check and sends a fresh `code_snapshot` before anything else, so help and grading never use stale code. An accepted submission seen while paused counts as the solve, taking effect on resume.
- **Ending** keeps everything graded so far. A problem left mid-way when the session ends gets no review and stays unfinished; ending is never giving up.
- **Solved** means an accepted submission. **Skip** means "not now": it adds no grade and never removes one already written (the full-solution or debrief Again); a problem with no grade stays unfinished (it returns to the Planner), so skipping counts neither for nor against you. Skipping just under the cap therefore avoids an Again; that is intended.
- **After an accept you can keep improving** your solution (for example, optimising your brute force): the panel shows a "done" button, and you stay in `active` until you press it, open another problem, or end. The `solve` grade is written when you leave the problem, so later accepted submissions count.
- **"That works. Can you make it faster?"** When the accepted approach is below what interviewers usually expect (§10), the Tutor says so the way an interviewer would, in one line that doesn't reveal the approach, and you can talk it through: unlike an interview, the point is to learn. Help in that conversation counts by the usual rules (§8). You can press done at any time; the problem then comes back later.
- **Once the full solution is shown before an accept,** code writes the Again review at that moment (§8), at once (not held for a pending reference) and with no Grader call, so it holds even if you end right after. You can type it in and submit it, which doesn't change the grade, or carry on. After an accept, being shown a better solution (for example the optimal one, once you asked for it) changes nothing: the solve is graded by the best approach you reached yourself.
- **Time cap per problem** (code; Easy 20, Medium 35, Hard 50 minutes of active time). The panel shows a quiet countdown. The cap counts the problem's active time within the session, so leaving it and coming back doesn't restart it. At zero a card with two buttons (no chat box) offers 10 more minutes, once, or the debrief, and may show "18 of 20 test cases passed" from the last Submit response; after the extension runs out it offers the debrief again. It never appears after an accept. Skip is always available: on the suggestion card (swap), mid-problem, on the time-up card and in the debrief.
  - **Grade:** an accepted solve before the final time-up, or while the time-up card is showing with no choice made, grades by the usual rules (help still lowers it). Once you choose the debrief, or skip or end, nothing changes the grade. Choosing the debrief without having solved writes the Again (shown as "missed") at once, not held for a pending reference; neither the debrief nor code typed afterwards changes it. Skipping, or ending the session, with the card showing leaves the problem unfinished, with no grade. When the Again was written, the problem comes back after a similar one (§8, "after a miss").
- **Debrief** (a sub-mode of `active`, entered from the time-up card; `done` or skip leaves it for `between`). The Tutor works out what went wrong, builds intuition and can walk through the solution; typing the code in afterwards is optional. Its input is a compact timeline built by code, at most about 10 entries: runs and submits with result, help presses and chat messages in gist, each with its time so long gaps show; full code only for the first real attempt and the final code, short diffs otherwise; over 10 entries, all runs are kept and the rest merged. No tool calls and no extra summarising call.
- **Off-plan problems:** if you open a problem other than the suggested one, it becomes the active problem and is logged as `off_plan`. The problem you left is graded if you had an accepted submission on it; otherwise it stays unfinished, with no grade.

### Session start

1. You press start; the timer counts up and you end the session whenever you like.
2. The Planner, started as soon as the panel opens (unless it opens on a paused session's resume-or-end choice, which needs no suggestion), suggests **one problem**, by title only, with its time cap ("Medium, 35 min cap"). Its reason is kept internally, never shown, since a reason like "checking two pointers" gives the approach away; the lines it does say to you (what it understood about a steer) never reveal more about the problem than you asked for.
3. You **go** with it, **swap** it for another suggestion, or end. Swaps are logged as preferences.

### Between problems

After a solve, or once the Tutor has walked you to the solution:
1. **Sometimes, one follow-up question,** the way an interviewer would ask it: the time and space complexity; the complexity of another approach; how else it could be done and the trade-offs; a changed constraint (sorted input, a stream, tight memory); a better approach worth knowing, offered only when it teaches a reusable idea (prefix sums, a monotonic stack), never a one-off trick; or what in the problem signals this approach, which trains the pattern recognition interviews test. There's no set number: the Tutor weighs the load so far (how hard the problem was, how long you spent, how long the discussion ran) against whether there's something real to learn, so after a long, tough problem or a trivial one there's often nothing, and a comfortable solve with an idea left on the table may get a couple. It leans toward complexity where the category's `analysis` is weak or untested (§8). You answer in your own words; there's no multiple choice, since a guess could pass as knowing. **One rule in code:** on a first attempt at an unseen problem in a category whose `analysis` isn't strong, if you solved it without being shown the solution, the question is a complexity question (the Tutor picks the wording: your approach or another one). Otherwise analysis evidence would depend on chance. The rule asks at most once per category per session, so an early session isn't a complexity quiz after every solve. Postponing it when the session's load is high (§8) is the Tutor's judgment, not enforced by code: the postponement is logged, and the question moves to the next first attempt in that category. You can skip it. If you give a complexity answer without reasoning, it asks for the reasoning before it's graded.
2. **The next suggestion,** from the Planner: go or swap, plus pause and end. The Planner starts when you leave the problem and doesn't wait for the Graders: the problem you just left stays out of its list while its grade is pending (§8), it sees this session's record of it (the help used and how it ended), and its other numbers are the grades already written, so a Good or Hard from the last problem reaches it one problem late, which costs little (an Again is written at once). If it hasn't answered after 60 s, the code-ranked fallback (§13) is shown instead; once a suggestion is on screen, a late Planner answer is discarded. Going opens the problem on NeetCode.

### Session end: the recap

When a session ends, the Reflector reviews it (§8) and writes a **recap**: a light consolidation card with a few short bullets, or none when the session went smoothly. Every hypothesis change is one of the bullets, so a session with hypothesis changes always has bullets (and therefore the feedback box). A bullet is about this session or "carried over" (from an open hypothesis or the learner model); carried-over bullets appear only when something changed (recurred, improved, new evidence). While it's being written, the panel shows the otter preparing notes, so you know it's coming. It lives in the **Recaps tab**, beside the Progress tab (§9): the tab shows an unread dot while a ready recap with bullets is unread, and opening the recap marks it read. A smooth session's recap is still listed ("Smooth session. Nothing to flag."), so it's clear the session was reviewed, but it doesn't light the dot. Nothing interrupts the start of the next session (no recap card above Start); the dot is the only signal. The tab lists recaps newest first, each with its date, its bullets and, when it has bullets, the feedback box below. While one is being written the tab shows the otter preparing notes; if the Reflector failed, the tab says so with a retry. The Planner reads an open hypothesis that a session's recap created or changed only once you have opened that recap (§8), so a recap you never opened doesn't steer you; hypotheses that predate it are unaffected.

**Feedback.** Only when the recap has bullets, under it: one optional free-text box ("Anything off or missing?") and a plain close (closing means seen, not feedback). On submit, the text is stored at once as a note (it has an id) and the panel says "Noted" (if it is about a grade, it points to the Progress-tab grade correction; notes never change grades). One extra Reflector call then takes the note, the recap and the open hypotheses and outputs hypothesis changes (drop, reword, new), validated like every model output; the call has no event log, so a hypothesis it creates may cite the note instead of an event. A drop is a rejection (§8). The card shows what changed ("Dropped: visited marking"). If the call fails, the panel says so and the note stays; the next session's Reflector reads it as input (re-dropping a dropped hypothesis is a no-op). There are no per-bullet controls.

## 6. Help

### Asking for help

Hints come only after your yes: the "I need help" button is always in the panel, or you ask in the chat. While you solve, nothing prompts you; the only unprompted element is the time cap (§5). After an accept the Tutor may ask a follow-up question (§5).

Each Tutor message in the chat has a small "Seems wrong" action, the way to flag a reference you can't see (§10). Tapping it opens an optional one-line box; submitting stores your line as a note that names the message id and marks that problem's reference `needs_review`. The Tutor doesn't weigh in and no model reacts.

### Pressing "I need help"

- **The first press** gets the smallest useful hint in the chat: just enough to get you unstuck, never the solution. You can reply to it like any chat message.
- **Each further press** gets the next, more concrete hint right away (see "How much help"), with no questions first. Like NeetCode's own hint tabs, it trusts you to use hints sparingly; the grade already records how much help you used. The button stays in the panel while you work on the problem.

### How much help

There's no fixed hint ladder in code. The Tutor works live from the problem and your code, the way a strong model in a chat does. It gets:
- the problem statement and the problem's reference (§10)
- your current code and recent run results
- the time spent and the time left on the problem's cap
- your history with this problem and category, the framings that worked before, and open Reflector hypotheses
- the conversation on this problem so far (bounded: older messages are left out, and the Tutor is told so)
- **past links** (below): a few of your earlier problems that are close to this one

**Past links.** The Tutor can connect this problem to what you've already done, the way a human tutor says "this is like the one we did last week". Code builds the list, so the model never searches or remembers on its own. It holds the earlier problems you worked on inside Leetotter (never the current problem, never a session you excluded from measurement; unfinished attempts count, with their honest outcome) that share this problem's category or a pattern in its reference (§10). Order: shared pattern first, then shared category; within those, problems you solved on your own or where a hint worked, then the most recent. It leaves out ones that ended in a full solution unless nothing else fits. Each entry is a plain fact: the title, a one-line description of what the problem asks (the reference's `summary`, so a title you've forgotten still jogs your memory), what happened (solved alone, or with which help, and the approach you used when it's known), and the idea discussed when there was one ("we worked out that sorting lets two pointers move inward"). That idea comes from the Reflector's teaching memory (§8), whose entries carry the problem they came from (§11); a problem from the current session has no idea line until the Reflector runs at the session's end. A past problem whose `summary` is missing appears by title only, the gap is logged, and the Tutor is never blocked by it.
- **When.** Unprompted, a link appears only in a reply to a help request (the button, or a chat message asking for help) or after you've solved the problem; never in a reply labelled `none`, and never in a chat answer you didn't ask for help with, since hints come only after your yes (§2). Unprompted it uses **at most one**, only when it strongly fits, written as "like Valid Anagram (the one where you counted letters)". Most replies have no link. **When you ask** ("have I seen something like this?", "what did we do before that's similar?"), there's no cap of one: it gives every close match in the list, each with its description and what they share.
- **Code checks the link.** The Tutor's output has a field `linked` (the slugs it cites, from the list). A slug that isn't in the list is invalid output (retried once, then a loud failure, §7); more than one when you didn't ask is invalid too. A non-empty `linked` makes the help kind at least `analogy`; a link that spells out the earlier idea so that it applies here is `partial_logic`. The Grader's check of help labels (§8) uses the same rule.
- **A link is help.** Pointing you at an earlier problem hands you part of the idea, so it counts toward the help recorded on this problem like any other help. The Tutor's prompt says to start with a reminder and a question and go further only when asked (pattern names included). After you've solved the problem it can name the technique and draw the comparison freely. If the list doesn't have what you did there, it says so and never makes the memory up.
- **Never imported solves.** A problem you only imported as solved isn't in the list: Leetotter knows that you solved it, not how, how long it took or what help you had, so there's nothing true to say about it.
- **Only the list.** It never invents a past session. With an empty list, the prompt section is left out and the Tutor behaves as before.

**Conversation.** The Tutor is a coding-interview tutor first, but nothing stops it chatting briefly: a friendly remark, a Python question, or a comparison of approaches after you've solved the problem. There's no explicit ban on other talk, which would make it feel robotic; its instructions keep it on helping you learn.

**It diagnoses before it helps.** Its instructions list the gaps to tell apart, from your code and what you say. The list isn't exhaustive; for a gap that isn't on it, the Tutor describes the gap in its own words and logs it as `other`, so the Reflector can notice one that keeps recurring:

| Gap | What helps |
|---|---|
| Misread the problem (a missed constraint, a different question) | Re-read it with an example |
| Missing a tool (a heap, union-find, Dijkstra) | Teach the tool briefly, then hand the problem back |
| Doesn't recognise the pattern (knows the tool, doesn't see that it applies here) | Point at the signal in the problem that calls for it, without naming the pattern, then let you connect it |
| Weak intuition (no idea where to start) | Build the idea up |
| Wrong intuition (committed to a path that can't work) | Steer off it, then build intuition for a new path |
| Stuck optimising (brute force works, the better idea won't come) | "What work is repeated?" |
| Can't write the code (idea right, implementation won't come) | Help with structure and Python, not the idea |
| Bugs and edge cases (nearly right) | Help you find it yourself: "try the smallest input" |

Brute force is never a wrong path. Going straight for the optimal solution is fine; only when it has left you with nothing working does the Tutor suggest getting a brute force working first. The Tutor follows your approach, even when it isn't one the reference lists.

From the diagnosis it chooses the next move. Its default is to build your intuition with questions, the way a good tutor would, and to go direct only where questions can't help (a tool you've never seen, Python mechanics). It gets more concrete only as needed: a question, a trace or illustrative example, an analogy, a code snippet, and eventually the full solution. Its instructions also say:
- help should match how long and how hard you've struggled
- weigh the value of more struggle against the time left on the problem's cap
- after a solution is shown, add only the explanation you still need, with no repeated Socratic process

**Each help turn is logged** with its gap and kind so the attempt can be graded (§8). A gap that fits none of the table's rows is `other`, with a one-line `gap_note` in the Tutor's own words. A chat reply that gives no help ("thanks, got it") is labelled `none` for both gap and kind and doesn't count as help; a press of "I need help" is never `none`, and a first press is never `full_solution`. The kinds are `question`, `trace`, `analogy`, `teach_tool` (explaining a data structure or algorithm you didn't know), `syntax` (Python mechanics, such as how `heapq` is called, never the algorithm), `snippet`, `partial_logic`, `full_solution`. The Tutor labels each turn; the Grader checks the labels against the turns' text and corrects any that are wrong, and corrections are logged.

**Leetotter's Tutor is the only helper during a session.** It replaces any other AI assistant you'd use while solving: outside help is invisible to Leetotter and would make your progress look better than it is. (Chrome shows one side panel at a time anyway.) NeetCode's own hints, solutions and NeetBot aren't used and aren't tracked.

**Replies** are text in the panel, and nothing is spoken. You dictate with MacWhisper into the chat box like any text field.

## 7. Agents

Each agent is one prompt template plus a context builder plus an output validator. **Every structured output is checked against a schema.** On failure: one retry with the error shown to the model, then a loud failure (logged, shown in the panel if it affects you). Invalid output is never stored.

| Agent | Trigger | Input | Output | Model |
|---|---|---|---|---|
| Tutor | your message, a press of "I need help", a solve, the debrief chosen at time-up | see §6; for the debrief, the timeline in §5 | reply text, gap, help kind, optional follow-up question, and the past problems it linked (`linked`, §6) | DeepSeek V4.1 Flash |
| Grader (two, independently) | two separate calls: the `solve` grade when you leave a problem you solved (not after a full solution shown before an accept or a debrief chosen at time-up, which code decides), and the `analysis` grade when a complexity answer is given (if one is). A skipped question never holds up the `solve` grade | the problem statement, the reference's approaches (with complexity and `interview_expected`), all your submissions and runs on the problem, the help turns for this problem with their labels, your complexity answer and reasoning | `solve` call: `{accepts: [{submission_event_id, approach_id or "new", meets_expected: bool}], corrected_help_labels}`, one entry per accepted submission; `analysis` call: `{complexity_verdict: right / right_after_followup / right_after_stated / wrong / wrong_reasoning / no_reasoning, complexity_stated_earlier: bool}` (whether the Tutor stated the complexity before the question, read from the help turns). `meets_expected` says whether that approach's time complexity is no worse than the best approach the reference marks `usually`, with space breaking ties; the Grader judges a "new" approach the same way. Code turns this plus the logged help kinds into the grade (§8). Two graders on different model families judge each attempt independently (§8) | GLM 5.3 Flash and DeepSeek V4.1 Flash |
| Reflector | session ended; recap feedback (one extra call, §5) | the just-ended session's event log (debrief included; old logs are not re-read), current learner model, open hypotheses, teaching memory, your notes, rejected hypotheses; for feedback, the note and the recap | session summary, hypotheses added/reworded/dropped (each with an optional category and skill, citing event ids that must exist, or for the feedback call the note's id; each change becomes a recap bullet), teaching-attempt outcomes, recap bullets (possibly none) | GLM 5.3 Flash |
| Planner (two small calls) | session start, between problems, swap, and your message while a suggestion card is shown. A chat message goes to the **Reader** first, then the **Picker**; the other triggers are the Picker alone | Reader: your message, the chat since the card appeared, the card's title and difficulty, the active steer, the 18 category names, and your problems from this session and the two before it (§8, Steering). The Picker: learner summary (computed by code, §8, including each category's recent results by difficulty, such as "Trees: Medium Good, Good; Hard Again, 2 weeks ago"), the open Reflector hypotheses (§8), which are what tells it why you struggled, imported history, unfinished problems, preferences, today's date, this session's problems so far (each with its difficulty, active time, help used and outcome), the eligible problems that fit what you asked for (§8, filtered by code), the suggestion on the card with the chat since it appeared, and the active steer, if any (§8) | Reader: what it read (ask, steer, clear, cannot or unrelated), a line in the otter's voice, the categories and difficulty levels you meant (separately for "more" and "fewer"), a note with your own words and, for a named problem, its title. Picker: one problem slug with an internal reason | GLM 5.3 Flash |
| Reference writer | offline batch for the 150; on demand for any problem without a reference | problem statement | reference (§10) | GLM 5.3 Flash |
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
2. **Each (category, skill),** with skill being `solve` or `analysis`. *Can you do new ones?* Only first attempts at problems you hadn't seen before count, since a repeat can't show transfer. This drives the Planner's transfer checks and the Progress tab's strong level. It's a heuristic: FSRS is built for repeated reviews of one item, and established skill-mastery models (knowledge tracing, Elo-style ratings) need far more data than one learner produces, so none would be better calibrated here. Every first attempt is logged with its difficulty, so the rule can be compared with a simple fitted model once there are a few hundred attempts.

### Grades

Each graded attempt is one review, by a fixed table (§2):

| What happened | Grade |
|---|---|
| Solved without help, with an approach at least as good as the one interviewers usually expect | Good |
| Solved after questions, traces, analogies or a taught tool only; or solved without help, but with a worse approach when the reference marks a better one as usually expected | Hard |
| Needed snippets or partial logic, was shown the full solution before any accept, or chose the debrief at the time cap without having solved it | Again |
| Skipped, or the session ended mid-problem (also with the time-up card showing) | no grade added (one already written stays); without one the problem stays unfinished |
| Complexity answer right, with right reasoning | Good (`analysis`) |
| Complexity answer wrong or incomplete at first, then right after one guiding question from the Tutor that doesn't state the answer | Hard (`analysis`) |
| Complexity answer wrong, or right with wrong reasoning, or right only after the Tutor stated it | Again (`analysis`) |

`syntax` help doesn't lower a grade: an interviewer would tell you how `heapq` is called.

- A `solve` grade goes to the problem, and, on a first attempt at an unseen problem, also to its category's `solve`. For the category, a Good on an Easy problem counts as Hard: a category review is a different problem each time, and an easy one says less about the skill. (Established skill models all correct for item difficulty; this is the simple version of that.)
- An `analysis` grade goes to the category's `analysis`, only on a first attempt (you may remember a seen problem's complexity), and only when the `solve` grade wasn't Again and the Tutor hadn't stated the complexity earlier in the attempt. The Tutor can still ask on repeats, ungraded. Questions about another approach's complexity are graded the same way; questions about alternatives and changed constraints are discussion, not graded.
- **"What interviewers usually expect"** means the best approach the reference marks `usually`, not the absolute optimum: a one-off trick is never required (§10).
- An attempt is graded by the best approach you reached yourself, so writing brute force and then optimising it yourself counts as Good. With several accepted submissions, code grades each one by the table, using only the help given before that submission, and keeps the best result. A later accept that needed a snippet therefore never lowers an earlier one reached alone, and a later accept reached alone replaces a weaker first one.
- Only help given before that solution counts toward the grade. Help afterwards doesn't lower it.
- An answer with no reasoning even after the Tutor asks for it is treated like skipping the question: no review. Being asked for your reasoning isn't a follow-up question.
- **A solve is graded only against a checked reference.** While a problem's reference is `needs_review` (Reference writer and Checker still disagreeing after one regeneration), not written yet, or unavailable because the Reference writer or Checker is down, the attempt is stored ungraded and its `solve` and `analysis` grades are pending (except the full-solution and debrief Agains, §5, which are written at once). It is retried through the ungraded-attempt retry (§13); no model's side is taken and nothing is capped. Dispute status is read when grading runs, so a dispute that arises mid-attempt (§10), including a reference you flag as wrong, counts. A pending grade is dated at the attempt: FSRS is recomputed in attempt order, and `first_attempt` comes from attempt order, not write order (a pending problem can be opened again, giving two pending attempts). The Planner doesn't offer a problem while its grade is pending; a problem with a `needs_review` reference is otherwise eligible. Resolution is in §10.
- Every reference marks at least one approach `usually` (§10).
- FSRS's Easy rating is never used: no signal for it is reliable enough.
- Help, approach and verdict labels come from the Graders (§7) and the logged help turns.
- **Two graders.** GLM 5.3 Flash and DeepSeek V4.1 Flash each grade every attempt independently. If they agree, that's the grade. If they disagree, the lower grade is stored (so a problem comes back sooner rather than being credited too early), and the disagreement is noted in that problem's history. The share of disagreements shows in the Progress tab as a health signal: if it's high, something in grading needs fixing. Two models from different families catch each other's random mistakes and quirks, though not a blind spot they share.
- **Unseen** means the problem has no graded or pending attempt and no imported solve, so a pending attempt makes it seen by attempt order. An unfinished attempt makes it seen only if you ran or submitted code or asked for help on it, so a later graded attempt is then not a first one; a problem you only opened and left stays unseen. A problem you studied somewhere the import can't see counts as unseen; that makes the transfer measure slightly optimistic, accepted in exchange for never asking. Whether a review is a first attempt is stored with the review when it's written, so corrections and session exclusions never turn a later attempt into a first one.
- **Grades aren't shown after a problem.** Wherever they're shown, they're in plain words, so they can't be confused with problem difficulty: Good is "solid", Hard is "shaky", Again is "missed". The FSRS names stay in code. The Progress tab keeps a per-problem history with each grade and its reason (latest shown, earlier ones collapsed, §9), where you can correct one (for example "I didn't need that hint", or "that was a guess"). The correction changes the review log, and FSRS state is recomputed. Correcting a `solve` grade to missed also removes that attempt's `analysis` review (above: no `analysis` grade after an Again); correcting one away from missed adds none.

**FSRS settings:** the published default parameters of one pinned `py-fsrs` version, stored in the database with that version, so an upgrade can't move the measuring stick. No learning or relearning steps (they're for minute-scale flashcard drills). Fitting the parameters to your own history (FSRS's optimizer) happens only with your OK, since it changes the measure. **Recall now** is computed by `py-fsrs` from the current time and the last review. A problem counts as due, and a category as rusty (§9), below 0.9 recall now, FSRS's default desired retention, stored with the pinned parameters.

**The review log is the source of truth.** FSRS state is always recomputed from it, so a correction removes or changes reviews rather than patching numbers.

### Judgment

**The Reflector writes**:
- hypotheses about you, such as "visited marking: suspected weak", "explains the idea well, struggles to implement it", or a gap that keeps recurring ("edge cases in linked-list problems")
- teaching-memory entries: which framings and kinds of help worked
- the session summary, and the recap (§5)

Every hypothesis cites event ids that exist; one created by the recap-feedback call, which has no event log, may cite the feedback note's id instead (notes have ids the validator accepts as citations). A hypothesis that cites nothing is rejected by the validator. Every change to a hypothesis (add, reword, drop) is a recap bullet with its own one-line text, which code renders, so no hypothesis steers the Planner without having been shown once. A drop through recap feedback is a rejection: status `rejected`, with the note id. Only rejected hypotheses are restricted: the Reflector can re-add one only by naming the rejected id, and its recap line then says it is back; a silent re-add under new wording can't be detected, and that is accepted. The Reflector reads only the session that just ended, so a rejected hypothesis can come back only on evidence from after the rejection. Anything in a session can inform hypotheses; only the review grades feed the numbers.

**Your notes** (preferences, corrections, recap feedback) are input to the Reflector's hypotheses (recap feedback through its extra call, validated, §5); they never change grades or numbers, which are corrected only through the Progress-tab correction. Steers (below) only shift suggestions and never touch the learner model. The panel has an "exclude this session from measurement" action (for example after a bad day), which removes that session's reviews from the FSRS recomputation.

### Planning

- **Breadth first, where evidence is thin (rules in code).** Two passes. In each, code chooses the category at random with equal chances, never one already tried this session while another is left, so nothing follows NeetCode's list order.
  - **First, every category gets one attempt.** While some categories have no graded or pending attempt yet, each new problem comes from one of them. Imported solves are not evidence, so a category you only imported is as untested as one you've never seen. When the chosen category holds imported solves, the problem is one of them: the cheapest check of whether it stuck.
  - **Then, every category gets one new problem.** A retested import or a repeat is not a first attempt (§8), so a category tested only that way has no `solve` memory of its own: it could never turn rusty, get a refresh or move toward strong, and you could forget it unnoticed. While some categories have no category `solve` review yet, each new problem is an unseen problem from one of them, from the whole pool when the 150 has none left (beyond the 150, below). A category with no unseen problem available is left out. A pending attempt counts as an attempt in both passes.
  - A due retry can still come instead, under the coverage rules below, and a steer or a direct ask overrides the choice. The Picker chooses the problem within the category. Help is offered as usual; it's recorded and lowers the grade, so it doesn't spoil the measurement.
- **What gets retried (rules in code, so retries can't swamp new problems).** The two memories have separate jobs: problem memory decides when a problem you've attempted comes back, category memory decides when a category needs a new problem.
  - **A problem not yet done on your own** (§9: you needed any help, or solved it only with brute force) comes back a few days later, once its own recall has dropped below 0.9.
  - **A problem done on your own** comes back when its recall drops below 0.9, but only once every one of the 150 has a graded attempt. Until then new ground comes first: bringing done problems back from the start took about half of all practice in the simulation (§16).
  - **A rusty category** (its own `solve` recall below 0.9) gets one unseen problem, by default a variation with the same pattern, which also counts as transfer evidence; from the whole pool when the 150 has none left in it. Once both breadth passes are done, while a rusty category has an unseen problem available, each new problem comes from the rustiest one (lowest category `solve` recall) not yet tried this session. Never a problem you've done: a repeat isn't a first attempt, so it can't update the category's memory and the category would stay rusty.
- **Coverage keeps moving (rules in code):**
  - **No two retries in a row while any eligible one of the 150 is untried.** A retry is a problem with a graded attempt in Leetotter (one not yet done on your own, or a done one that is due); an untried problem is one of the 150 with none, which includes imported solves still to be tested; only eligible untried problems count (not one whose grade is pending). So until all of the 150 have been tried, at most half the suggestions are retries and new ground moves every session; after that, retries may run back to back, so the last problems to finish aren't held back by extras. A due retry that waits one more problem loses nothing, since FSRS handles a late review.
  - **A problem you skipped isn't suggested again in the same session.**
  - **After a miss, practise the idea elsewhere first.** After an Again (including a full solution shown before an accept or a debrief chosen at time-up), the Planner prefers an unseen problem from the whole pool that shares one of its patterns before the retry comes back, so you don't fail the same problem twice in a row for the same reason. With none available, the problem comes back when it's due.
- **Session load (the Planner's judgment, no numbers):** manage the load of the session as a whole. Judge how demanding each problem so far turned out to be (its difficulty, how long you struggled, how much help you needed), and choose the next one so demanding problems don't pile up back to back. A hard problem you breezed through needs no break; a medium you fought for 40 minutes may. A retry or an Easy is often the lighter choice, where the rules allow.
- **Balance:** among the problems these rules allow, the weakest categories come first, and within a category a problem that exercises a weakness the open hypotheses name is preferred (a hypothesis is a suspicion, so it steers the choice but never overrides the rules above). How it weighs them is the Planner's judgment.
- **Mixing:** for a category that's new to you or very weak, the first one or two problems can come back to back; after that, categories are mixed. Mixing makes you practise working out *which* approach applies, which is the core interview skill, and it's one of the better-supported findings in learning research (interleaving). Leetotter never names a problem's category or pattern before you've solved it, except what you asked for yourself: after "more DP" you know the next problem's category, but the Planner still never volunteers the pattern.
- **Your imported solves** tell the Planner what you've seen, but don't count as tested: Leetotter tests them itself, by the same problem (which counts toward coverage) or a variation (which counts toward transfer).
- **Transfer checks:** once a category looks strong on its problems, the Planner mixes in unseen problems from that category to check you can solve new ones, not just remember old ones.
- **Problems beyond the 150,** once in a while, for variety and realism, since interviewers don't stick to NeetCode lists. They never come from nothing: each serves a category and a pattern (favouring patterns you haven't yet shown on a new problem), and they come in especially when practice has turned repetitive, in place of another retry of a familiar idea. A first attempt at one counts toward its category like any unseen problem (§8). They come from NeetCode itself, so they sit in NeetCode's own categories and use the same editor, with no new reader (phase 4). The pool is every NeetCode problem that opens without a paywall, starting with the extra 100 in the 250 list (rule in code: a category's other problems are offered only once its 250 extras have none left unseen). The simulation (§16) found the pool must be large for small categories to reach strong, so the Planner draws on the whole pool, held only to the coverage rules and the balance across categories. NeetCode's lists grey out locked problems and mark them with a padlock, so the Planner reads that and suggests only free ones; if one still can't be opened, the reader's self-check says so (§4).
- **Steering.** You can steer the Planner in plain words, in the chat box, whenever a suggestion card is shown (at session start, between problems, after a swap; after any follow-up question there): "more dynamic programming", "I'm confident with arrays, fewer of those", "focus on trees and graphs". The chat goes to the Planner only while a card is shown. Typed mid-problem, the Tutor says it will take it at the next suggestion; it doesn't store steers. The Planner turns what you said into a **steer**, stored in `notes`, and says what it understood. A steer only shifts which problem is suggested. It never changes grades, reviews, the learner model or the Progress tab: "I'm confident with arrays" doesn't make arrays look stronger, so Progress stays an honest measurement and a steer is only a request about practice. Steering toward what you like risks steering away from weak spots, and the guard is the honest Progress tab plus the Planner saying what the evidence shows.
  - **The Planner interprets what you say; it isn't a lookup.** Words can't be mapped to a setting, so the Planner reads them in context (what it had just offered, your history, the steer already active) and decides what you meant: a request for the next problem, a focus, or going back to normal. Phrases such as "a DP one next", "let's work on DP for now", "focus on trees and graphs" and "back to normal" are illustrations of the range, not entries in a table. The Reader records its reading as flat structured data (the focus as catalog categories, since those are a fixed list of 18, plus difficulty levels, each for "more" and for "fewer" so one steer can say both; a note carrying your own words, judged by the Planner rather than enforced by code; and how long it lasts: this session or until you clear it), which code validates against the schema only: categories and levels must be real, and nothing has to agree with anything else. Patterns are not part of a steer: they are free text, and a fixed list is what a model reads reliably. The Picker still sees each candidate's patterns and prefers a fit ("DFS" reads as Graphs and Trees; the Picker prefers a depth-first-search problem inside them). The Reader never sees the problem list, so its line cannot name the problem the Picker chooses. It does see the problems you worked on in this session and the two sessions before it (title, date, how each ended: solid, shaky, missed, grade pending or unfinished), so "the one I missed yesterday" names a problem; you've seen those already, so nothing is given away. When part of the request fits no parameter, the steer is still saved with the note alone. Code does the rest: it narrows the eligible problems to your focus (a direct ask's focus overrides the active steer), and the Picker chooses from that pool. A problem you name is found by code in the catalog by title; a near miss gets "did you mean …?". When the words are ambiguous it takes the shorter reading (this session) and says what it understood, so you can correct it, instead of asking how long. When it can't act on a message by picking a problem (reading "cannot"), it says the true reason in its own words: an unknown word is quoted and asked about; a request that needs something the Reader doesn't see (older sessions, scores, the problem list) is answered as missing information, never as a word it failed to understand; either way it offers what it can do. The Reader is told what it can and cannot see; no list of refused phrasings exists. Code decides none of the meaning; it enforces the limits around it.
  - **One steer at a time.** A new steer replaces the active one; when you add to it ("and fewer arrays"), the Planner writes one steer that combines both. So there is never a list of steers to manage.
  - **Always visible, one tap to clear.** While a steer is active, every suggestion card shows it in one small line ("Steering: more dynamic programming · Clear"); with no steer, the line isn't there. Clear removes the steer and replaces the card's suggestion with an unsteered one (not logged as a swap, since the card was steered). You change a steer by saying so in the chat. When a steer ends on its own (the session ends), the line simply goes.
  - **Eligibility stays in code.** The rules above decide which problems the Planner may choose; a problem you name yourself ("the one I missed yesterday") is always allowed, as you could open it off-plan anyway. A steer never makes an ineligible problem eligible and never holds back retries: they keep their place under the retry rule, with one in the steered focus preferred when one is due. A steer does override the balance preference (weakest category first).
  - **A direct ask always wins.** "Give me a DP question next" is an immediate request, not a stored steer, and it is honoured on the spot. If nothing eligible can satisfy it (every dynamic programming problem is done on your own and none is unseen), the Planner says so plainly and offers the nearest eligible alternative. The same goes for a stored steer that nothing eligible can meet: the Planner says so once, suggests without it, and the line stays until you clear it or it ends.
  - **Failures are loud.** If the Planner can't read your words (or they name nothing in the catalog), no steer is stored and it says it didn't catch it; a message that isn't about practice ("hey, what's up?") gets only the otter's reply, with no problem picked and the card unchanged. If the Reader fails after its retry, the panel says your steer wasn't saved, and the code-ranked fallback (§13) still applies the stored steer, as a filter on the focus within eligibility. If only the Picker fails, what the Reader read stands (the steer is saved, an ask is honoured), and code ranks the problems that fit it and says it chose them itself.
- **Weak analysis** is tested on unfamiliar problems in that category, with a complexity question.
- **After a full solution was shown before an accept,** the problem's Again grade brings it back soon, after a same-pattern problem when one is available (above).

## 9. Progress and readiness

Interviews give you new problems, so the finish line is solving new ones, with the 150 as the way there. Problems you've done still come back when their memory fades, but only once all of the 150 have been tried (§8, "What gets retried"): keeping all 150 fresh from the start would take about half of all practice, and the simulation (§16) found that then the 150 were never all done at once, since a missed review makes a problem not done again.

- **100%** (fixed, §2) means **all 150 are done on your own**: a problem's latest graded attempt is Good (§8): solved without help, with an approach at least as good as what interviewers usually expect (`meets_expected`), and an accepted submission written in a session. Any help counts against it, guiding questions, traces, analogies and a taught tool included, since in an interview you may not get them; `syntax` help is fine (§8). A brute-force-only solve doesn't count. A problem you didn't do on your own comes back a few days later, and only a later solve on your own counts. Imported submissions don't count.
- **Strong** (fixed, §2) is a harder level that a category reaches on top of that. It is the evidence that you can solve *new* problems: the category is strong for both `solve` and `analysis`, meaning a stability of at least 21 days, a latest review that isn't Again, and at least one Good review on a Medium or Hard problem (by the reference's difficulty), so a run of easy problems can't make a category strong. Strong is shown per category (§9 below) and isn't required for 100%.
  - **Why 21 days:** with the pinned `py-fsrs` 6.3.2 defaults, one clean solve gives 2.3 days and two clean solves a week apart give 21.4. So 21 is the shortest value that needs spaced, repeated evidence rather than one lucky solve. The simulation (§16, `docs/simulation-report.md`) found 14 days no better and 28 and 35 slower and less reliable, so it stays at 21.
  - **How long it takes** (the simulation, with 20 pretend learners per row at 7 problems a week): all 150 done on your own takes about 30 weeks for a learner who solves about 90% of new Mediums cleanly, and about 55 weeks at 70%. Every category strong at once is reached only by the 90% learner (about 29 weeks, 200 attempts); at 70% about 12 of the 18 categories can be strong at once. So 100% is within reach at any realistic level, and strong everywhere is the aspiration.
  - **What happens at 100%:** nothing stops. Memory keeps fading, so categories turn rusty and can drop from strong back to solid, and the Planner keeps refreshing and testing new problems. The header's bar simply fills and the header then leads with how many categories are strong and how many are rusty, so there is always something that moves and something to keep up. It isn't celebrated or pushed; the only acknowledgement is one line in that session's recap.
  - A break alone doesn't lower either; the next attempt decides.
- **Per-problem memory** (§8) schedules retries and spaces out problems you remember; it isn't part of the finish line.
- **The Progress tab** is a map of NeetCode's categories, laid out in NeetCode's category order (Arrays & Hashing, Two Pointers, Sliding Window, Stack, and so on). The header is one bar across the 150 in three parts, "62 on your own · 40 coming back · 48 to do" (to do is untried, imported solves still to be tested included), so you can see coverage moving, plus how many categories are strong. Each category's row shows only:
  - its level, in plain words (never letter grades): **not started** (no attempts), **building** (started, with some problems not yet done on your own), **solid** (every problem in it done on your own), **strong** (solid, and strong for both new problems and complexity, as above)
  - its count of problems done on your own, such as "7 of 11" (a count, not a percentage, so a small category like Tries doesn't jump by a third at a time; it doesn't drop while you're away, only when a problem that comes back is missed)
  - a small "rusty" tag on a solid or strong category whose own `solve` recall now is below 0.9 (§8); for a strong one, that's roughly three weeks away from it. Building categories don't get it: early memory fades within days, so after a break the tag would cover the map, and the count already tells the story
- **Clicking a category** opens the detail:
  - **one line on what's next**, written by code from the level's criteria, never by a model: what's missing for the next level, such as "To reach solid: 4 problems still to do on your own" or "To reach strong: a clean solve on a new Medium or Hard; complexity not yet tested". Each missing criterion has its own template; the 21-day stability, which isn't something you can do now, is said honestly as time ("another clean solve on a new problem, a week or more from now"). It speaks only of evidence and never names a pattern, which could give away the approach of a problem the Planner suggests next. For a strong category it says when it would turn rusty ("strong; holds for about 3 weeks without practice").
  - its problems once you've attempted them in Leetotter, each with a plain status: **on your own**; **needed help, coming back** (snippets, partial logic, a full solution, or chose the debrief at the time cap); **brute force only, coming back**; **started, unfinished** (skipped or ended mid-problem, no grade). A small mark shows an attempt whose grade is pending (§8). Problems not yet attempted here appear only as counts ("4 not tried · 2 solved before Leetotter, not yet tested"), never by name, since a listed name would reveal the category of a problem the Planner suggests. Each status opens its latest grade and reason; earlier grades stay collapsed behind an "n earlier" link, so a problem done many times doesn't crowd the list.
- Grading health (the graders' disagreement share, ungraded attempts, and the "N references need review" list, §10) is one quiet line at the bottom of the tab; the review count also shows as a badge on the tab's label.

## 10. Problem references

The NeetCode 150 is among the best-documented material in programming, and it's well inside the models' training. So there's no detailed answer book and no solution code: the Tutor and Grader work live from the problem and your code. Each problem gets a short **reference**, whose job is consistency, not knowledge: the same approach gets the same verdict every time, so progress is measured on a fixed scale.

```yaml
slug, title, difficulty, neetcode_category
summary: one plain sentence on what the problem asks, never naming an approach or a pattern; code rejects one that contains an approach name or a pattern's display name, case-insensitively (used by past links, §6)
patterns: [ids], analysis_concepts: [ids]
approaches:
  - id, name, idea (one line), time, space
    interview_expected: usually | sometimes | rarely   # at least one approach is `usually`
    trick: bool   # true for a one-off trick that only works for this problem; a trick is never `usually`
```

Categories are NeetCode's, and every problem beyond the 150 already sits in one. `patterns` are the reusable, interview-relevant ideas a problem uses (sliding window, fast and slow pointers, monotonic stack…); `analysis_concepts` are finer tags for complexity. The pattern set comes from the 150's references and stays as it is: the NeetCode 150 was built to cover what interviewers test. A problem beyond the 150 is tagged with existing patterns only; the Planner doesn't pick one that fits none. Patterns aren't measured; they steer the Planner and aren't shown in the Progress tab, since a pattern shown next to a category could give away a suggested problem's approach (§9).

**Writing a reference:**
1. The Reference writer writes it from the problem statement; for the offline batch of the 150, from the title and slug, since they're famous problems well inside the models' training and the Checker catches mistakes.
2. **The Checker reviews** each approach's complexity, `interview_expected` label and `trick` flag. If they disagree, regenerate that part once. If they still disagree, the reference is stored with `needs_review` and the Tutor is told which labels are uncertain; grades of that problem wait (below).
3. **Disputes wait on you; tutoring doesn't.** While a reference is `needs_review`, solves of that problem have a pending grade (§8) and the Tutor goes on helping. Disputed labels never drive "Can you make it faster?" or follow-up questions. The Checker returns agree/disagree per label, never an alternative value. The count of references needing review shows as a badge on the Progress tab's label, so it's visible from the chat, and the tab lists them with a copy button. The copied prompt is self-contained: the problem, the reference, the disputed labels (or, for a "Seems wrong" flag, the flagged Tutor message and your line) and the exact verdict format. You paste it into a separate AI (your own Claude Opus on the web), paste its reply into a box in the panel and press Apply. The verdict, per reference, is either the settled value of each disputed label (the reference's approach labels) or "reference is fine" (the Tutor misspoke). Code checks the reply against that schema; one that doesn't fit is refused with the reason and nothing changes. Applying writes any settled labels, clears `needs_review`, marks the reference `resolved_by_you` and logs the outcome; the companion then grades the waiting attempts with its own client (so the shared rate limiter holds, §12). No model is called to apply a verdict, and the verdict is taken as given.

**Trick or pattern** is judged per approach, per problem, by whether the idea comes back elsewhere. Fast and slow pointers are a reusable pattern for a linked-list cycle or its middle (not a trick, and may be `usually`), but the same idea applied to Find the Duplicate Number is a one-off trick. Boyer–Moore majority voting is a trick. A reusable pattern is worth a follow-up even when it isn't required; a trick is never required or pushed.

**New approaches** you find during a session are added to the reference after the Checker agrees; if it disagrees, that approach only stays out of the reference and the reference is not marked `needs_review`. Since you never see the reference, only the Tutor's words, "Seems wrong" on a Tutor message (§6) is how you flag it: the reference is marked `needs_review` like any dispute and grades wait as above. A flag marks no label uncertain, so the Tutor's follow-ups carry on. The copied prompt carries the flagged message, the reference and your line, and the separate AI decides whether the reference was wrong (corrected labels) or the Tutor misspoke (reference is fine); either way the badge clears once the verdict is applied.

**Problems without a reference** (a transfer check, or any off-plan problem outside the 150) get one written on demand: one or two model calls. For a transfer check, the Planner starts it when it suggests the problem. You never wait: the Tutor helps from the problem statement meanwhile, and the panel says the reference isn't ready yet; the solve's grade is pending until it is (§8). Like all reference writing, this uses spare rate-limit capacity.

**Revisit with evidence:** if real sessions in phase 3 show the Tutor giving wrong guidance, add verification back for what it gets wrong. If references for less famous problems beyond the 150 prove wrong, add web search for writing those.

## 11. Store (SQLite)

| Table | Holds | Written by |
|---|---|---|
| `events` | every signal, help turn, result; append-only | code |
| `sessions` | start, end, summary, recap bullets (hypothesis-change lines included) | coordinator, Reflector |
| `reviews` | (item: problem or (category, skill), grade, meets_expected, first_attempt, difficulty, grader disagreement, event_id): the log that FSRS state is recomputed from, in attempt order; an attempt with a pending grade is stored without one until graded | code |
| `fsrs` | pinned library version and parameters, the 21-day strong threshold | set at install; changed only with your OK |
| `hypotheses` | text, optional category and skill, status (a rejected one keeps the id of the note that rejected it), cited event ids or note id | Reflector |
| `teaching` | problem slug, cited event ids, category, framing, help kind, outcome, and the grade of that problem's next review | Reflector |
| `notes` | preferences, corrections, recap feedback and "Seems wrong" flags (the flagged message id and your line), each with an id (you, via the panel); the active steer, with how long it lasts and the Planner's reading (the Planner, from your words) | you, the Planner |
| `settings` | the settings in §2 | you, via the panel |
| `references` | the problem references, with `needs_review` / `resolved_by_you` | Reference writer, Checker, the verdict you apply |
| `model_calls` | role, model, latency, tokens, validation result; full prompt text kept for the last 10 sessions only | model client |

About 1 MB per session. The schema is versioned with migrations from the first release.

If the store ever grows large, old sessions are compacted: only runs, submissions, help turns, transcripts, final code and any event cited as evidence are kept. This isn't built until it's needed.

## 12. Models

All models are served by your provider's OpenAI-compatible endpoint; none run on the Mac.

```toml
[roles]
tutor      = "x-test-2"        # DeepSeek V4.1 Flash
reflector  = "x-test-1"
planner    = "x-test-1"
grader     = ["x-test-1", "x-test-2"]   # two graders, independently
reference_writer = "x-test-1"
checker    = "x-test-2"        # DeepSeek V4.1 Flash
```

Models are assigned by what each job needs; there are no fallback models.

References are written once (the 150 in a batch, other problems on demand), so quality matters more than speed there: the two strongest models write and check them, from different model families so the check is independent.

- **Base URL and key** come from environment variables, never the repo or the store. The key is kept in the macOS Keychain, and the companion's launch script exports it, so it never sits in a file an AI coding agent might read.
- **Rate limit: 90 requests per minute**, shared by everything through one limiter. The live roles get priority, and reference writing only uses spare capacity.
- **Reasoning effort:** the Planner's calls use `reasoning_effort = "low"` (`[reasoning_effort]` in `roles.toml`). Without it DeepSeek spent 700 to 4,000 hidden thinking tokens on a reply that is only a slug, taking 6 to 39 s for the same request; with it a pick takes about 2 to 6 s. Other roles are unchanged.
- **Timeouts:** Tutor 60 s (streamed), Grader 60 s, Planner 60 s (then the code-ranked fallback, §13; it starts when you leave the problem, §5), Reflector 180 s.
- **An unavailable model is reported, not worked around.** If a model fails twice in a row (or returns "model not found"), the panel names the model and what it affects (for example "DeepSeek V4.1 Flash isn't responding: tutoring, checking references and one of the two graders are unavailable"), and it's logged. You fix it in the configuration. What happens to each role meanwhile is in §13.
- Phase-1 checks (run 2026-10-15 with `python -m companion.check`): `x-test-2` is reachable and, like `x-test-1`, supports JSON schema, JSON object and plain output. (The Tutor moved to DeepSeek V4.1 Flash on 2026-10-15; see below.)
- **Tutor model and latency (measured 2026-10-15):** the Tutor runs on DeepSeek V4.1 Flash with thinking on; in a replay of a long real conversation it answered in about 6 s, against about 21–33 s on GLM 5.3 Flash, and sending the full conversation (about 4,300 tokens) cost nothing extra. The Tutor asks for a plain JSON object, not strict JSON schema, because the provider's strict schema mode strips line breaks from strings and flattens code blocks; the reply is still validated against the schema. Turning thinking off cut a GLM reply from about 9 s to about 1.5 s but gave a weaker hint and a doubtful gap label, so it stays on. The streamed text is a preview pulled from the partial JSON: if the reply fails validation, the preview is withdrawn and replaced by the retry (or by a loud failure), and nothing is stored or logged as a help turn until the reply validates. A request that gets no answer at all (a connection reset, a 5xx error) is retried once after 1.5 s when nothing has streamed; timeouts and 4xx errors aren't retried, and only the second failure counts toward "fails twice in a row" and is reported. The panel shows a waiting indicator until the first word.

## 13. Failure handling

| Failure | Behaviour |
|---|---|
| Companion not running | Panel shows how to start it; no session |
| Page reader can't read the editor | Session won't start or pauses; panel says why |
| Model returns invalid output | One retry with the error, then a visible failure; nothing invalid stored |
| Model unavailable | The panel names the model and what it affects. Tutor down: help is unavailable, and you can keep solving or pause. Grader down: as "Grader fails" below. Planner down: as "Planner fails" below. Reflector down: as "Reflector fails" below. Reference writer or Checker down: reference writing pauses, and a solve of a problem whose reference is missing has a pending grade (§8) |
| Rate limit hit | Live roles queue briefly; reference writing backs off; never silently dropped |
| Checker fails | Reference writing pauses, and the panel says so; solves needing a missing reference have a pending grade (§8) |
| Reflector fails after a session | The recap panel says so; retried in the background at next companion start; the session log is untouched. If the retry is still pending at session start, the Planner plans with the current hypotheses (the fallback after 60 s still applies); if it fails again, the panel says last session's review is missing |
| Recap feedback call fails | The panel says so; the note stays, and the next session's Reflector reads it as input |
| Planner fails, or hasn't answered after 60 s, at session start or between problems | The panel says so and suggests a code-ranked problem instead, from the same list of problems code allows next (§8): one not yet done on your own in the weakest category, else an unseen problem in that category; the failure is logged; a Planner answer arriving after the fallback is shown is discarded |
| A grader fails | Retried automatically (at the next problem, then at the next companion start). If one grader still fails, the other's grade is used and the history notes it; if both fail, the attempt stays ungraded, the panel says so, and it's listed in the Progress tab. Attempts whose grade is pending for a reference reason (§8) use this same retry once the reference is ready; after `resolve` the running companion grades them when told, or at its next start (§10). A grade is never guessed |
| Profile drifts | Hypotheses must cite events, and every change to them is shown in the recap, where you can say it's off |
| NeetCode changes its page | The reader's self-check fails loudly |

## 14. Privacy

Everything is stored locally, in one SQLite file (§11). On your Mac, Leetotter runs only the Chrome extension and the companion; the models run on the provider's servers. Sent to the model API: problem text, your code, your messages, and summaries of your learner data. Never sent: anything from outside a session, or your name (§2). The API key lives in the Keychain, not in the store.

## 15. Build order

Each phase ends with something you can use.

1. **Foundations.** Companion skeleton, SQLite schema, model client (roles, rate limiter, validation), the login service and Keychain launch script, and the phase-1 checks from §12.
   *Done when:* a test script calls every role's model and validates a structured reply, and the companion starts at login.
2. **Problem references.** The Reference writer and Checker, run over the NeetCode 150.
   *Done when:* ≥ 95% of the 150 have checked references, and the rest are marked `needs_review`.
3. **Text tutor on NeetCode.** First, a throwaway prototype of the side panel and the otter mascot, covering every panel state (watching, time-up card, debrief, reply, paused, between problems, preparing the recap, the recap, errors from §13), so the look is settled before the extension is built. Then: extension, NeetCode page reader, side panel chat, sessions (timer, pause, end, focus pauses, the time cap and debrief), and the Tutor on request only. There's no Planner yet: you open a problem yourself, and it becomes the active one.
   *Done when:* you can run a real session and get help that diagnoses where you're stuck and follows your code.
4. **The extra 100 on NeetCode.** The same reader on the NeetCode 250's extra problems, with references written on demand, so variations and transfer checks are available from the start of planning (you've already seen about half the 150).
   *Done when:* a session works on a 250-only problem as it does on the 150.
5. **Memory and planning.** Grading, both FSRS memories, the Reflector and recap, the Planner, history import, between-problem follow-ups, and the Progress tab.
   *Done when:* a second session's suggestions clearly reflect the first.

## 16. Open technical checks

Listed in their phases above; collected here:
- [x] NeetCode smoke test, done 2026-10-15 (results in §4): the problem page shows only the title, a solved check and the difficulty (Topics, Company Tags, Hints and Recommended Complexity are collapsed), the roadmap list shows a solved check per problem and a counter, and dictating with MacWhisper left Chrome as the active window. Still to confirm in phase 3: that a dictation pause never fires in a real session.
- [ ] Provider base URL (key via Keychain); structured-output support per model; Tutor latency (measured on DeepSeek V4.1 Flash, §12)
- [x] Simulation of the bar and the 21-day threshold (done 2026-10-15): results in `docs/simulation-report.md` and summarised in §9.
- [x] When done problems come back (simulated 2026-10-20, table I in `docs/simulation-report.md`, 7 a week): bringing each back whenever its recall fell below 0.9 took about half of all practice, and no learner ever had all 150 done at once (0.8 didn't fix it). Bringing them back only once all 150 have been tried kept the time to all 150 done (about 30 weeks at 90% clean, 55 at 70%); every category strong at once fell from 19 to 16 of 20 runs at 90% clean. Counting Tutor-helped solves as not done cost about one week.
- [x] Whether the Tutor's same-session gap labels should reach the Planner: no (2026-10-20). The Picker already sees each problem's help and outcome, which is what session load needs, and recurring gaps reach it through the Reflector's hypotheses.
- [ ] How a debrief relates to the Reflector's teaching notes.

# Delegating tickets

How tickets are built (the coordinator is a Sonnet session; DeepSeek V4.1 Flash builds through OpenCode; tests-first and reviews are Sonnet sub-agents).

## Briefs

Each ticket gets a brief file (outside the repo) made of the ticket text, the spec sections to read, and these common rules:

- Read the ticket, the cited SPEC.md sections, CLAUDE.md and the existing code. SPEC.md is the source of truth.
- Use the `tdd` skill: failing test first, one slice at a time, through public interfaces; expected values come from the spec, never recomputed by the code.
- Never read the Keychain, any key or secret, and never call the real provider. Tests use fakes; the user runs live checks.
- Leave no leftovers; no stubs, unused or commented-out code. Keep it simple and robust; failures are loud. Don't build what later tickets own.
- Before finishing, check these failure modes (most review findings so far were one of them): state that goes stale between sessions or problems; one failure repeating (timers, retries, broadcasts); a rule that blocks itself in an edge case (empty lists, the item just left, a grade just written); a signal leaking where it must not (steers, notes, pending grades); a stale async answer arriving late; background tasks never cleaned up or retried.
- Run the full suites. Do NOT commit, push or merge; the coordinator commits.
- Reply with files built, test results, decisions the ticket did not specify, and anything it could not do.

## Running a build

Guardrails are always on for every OpenCode run, not just ours: `~/.config/opencode/opencode.json` holds the permission rules (nothing outside the working directory; no push, commit, launchctl, security, install.sh) and pins `reasoningEffort: high`; the launcher `opencode-school.sh` points `LEETOTTER_DB_PATH` at a throwaway database. For a ticket, run `~/.config/opencode/ds-run.sh <name> <brief-file>` from inside the repo: it builds in its own worktree (`../leetotter-ds-<name>`, branch `ds/<name>`), copies the real database first (one copy, `before-ds-run.sqlite3` beside it, replaced each run) and reports loudly if it changed (the live companion service can cause a false alarm). Afterwards review the worktree diff, run the suites yourself, merge or cherry-pick from the branch, and remove the worktree and branch.

Run it in a herdr pane, not hidden. A run prints little until it ends, so judge liveness from evidence: the newest lines of `~/.local/share/opencode/log/opencode.log` (a live run logs `message=stream`; one stuck at `init` for minutes with 0% CPU is hung: kill it, remove its worktree and branch, rerun) and files changing in the worktree. Never call a run healthy from elapsed time or open connections alone.

If OpenCode itself stops a run partway (for example "Failed to execute statement"), resume the same session in the worktree with `opencode-school.sh run -c -m school/x-test-2 "<continue: check what is done against the brief, finish the rest>"` rather than starting over.

## Reviews

- Reviewers by the cost of a miss: two (correctness and failure modes; ticket and spec, with the acceptance criteria as checklist) for data-loss or exact-rule tickets; one for plumbing; none for UI. Briefs name the commits and the SPEC sections by heading. Small fixes are made directly with a test; fixes go back to the builder in one round only when more than a few files change.
- Plumbing and UI tickets: the coordinator reads the code and relies on the tests.
- Tickets whose behaviour is an exact rule in the spec (#39, #43, #44, #46, #47, #48, #50): a different model writes the tests for the rules only (eligibility, retry, grading), not wiring or socket tests, which the builder writes; the builder must make them pass and may not change them.

## Commits

The coordinator commits each ticket; `git add` specific paths when other work is in the tree, never `commit -a` while a build is running.

# Delegating tickets

How tickets are built (the coordinator is a Sonnet session; Codex builds; tests-first and reviews are Sonnet sub-agents).

## Codex builds

Each Codex ticket gets a brief file (outside the repo) made of the ticket text (GitHub may be unreachable from Codex), the spec sections to read, and these common rules:

- Read the ticket, the cited SPEC.md sections, CLAUDE.md and the existing code. SPEC.md is the source of truth.
- Use the `tdd` skill: failing test first, one slice at a time, through public interfaces; expected values come from the spec, never recomputed by the code.
- Never read the Keychain, any key or secret, and never call the real provider. Tests use fakes; the user runs live checks.
- Leave no leftovers; no stubs, unused or commented-out code. Keep it simple and robust; failures are loud. Don't build what later tickets own.
- Use only file and shell tools, never computer use. Work around ordinary errors (its shell has `python3`, not `python`).
- Before finishing, check these failure modes (most review findings so far were one of them): state that goes stale between sessions or problems; one failure repeating (timers, retries, broadcasts); a rule that blocks itself in an edge case (empty lists, the item just left, a grade just written); a signal leaking where it must not (steers, notes, pending grades); a stale async answer arriving late; background tasks never cleaned up or retried.
- Run the full suites. Do NOT commit, push or merge: the sandbox blocks writing to `.git`; the coordinator commits.
- Reply with files built, test results, decisions the ticket did not specify, and anything it could not do.

Launch (needs the `Bash(codex exec:*)` allow rule in `.claude/settings.local.json`):

```sh
codex exec -c 'notify=[]' -c 'plugins."unified-computer-use@openai-bundled".enabled=false' \
  -c 'sandbox_workspace_write.network_access=false' -s workspace-write "$(cat brief.md)" \
  < /dev/null > codex.log 2>&1; echo "exit $?" >> codex.log
```

- `< /dev/null` is required: without it Codex can hang on "Reading additional input from stdin...".
- Network off unless the ticket installs dependencies (omit the network flag then).
- Run it in the background and watch that the log grows past its header; alert if it is silent for 7-10 minutes.
- Loopback socket tests fail inside Codex's sandbox (it cannot bind 127.0.0.1); the coordinator runs the full suites outside it.

## Reviews

- Reviewers by the cost of a miss: two (correctness and failure modes; ticket and spec, with the acceptance criteria as checklist) for data-loss or exact-rule tickets; one for plumbing; none for UI. Briefs name the commits and the SPEC sections by heading. Small fixes are made directly with a test; send Codex a fix round only when more than a few files change. Fixes go back to Codex in one round when more than a few files change; small fixes are made directly.
- Plumbing and UI tickets: the coordinator reads the code and relies on the tests.
- Tickets whose behaviour is an exact rule in the spec (#39, #43, #44, #46, #47, #48, #50): a different model writes the tests for the rules only (eligibility, retry, grading), not wiring or socket tests, which the builder writes; the builder must make them pass and may not change them.

## Commits

The coordinator commits each ticket; `git add` specific paths when other work is in the tree, never `commit -a` while a build is running.

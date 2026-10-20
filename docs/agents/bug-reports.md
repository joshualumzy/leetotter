# Bug reports

The user saves bug reports from the panel (SPEC.md §2, "Bug reports"). Each is a folder in `~/Library/Application Support/Leetotter/bugs/<YYYYMMDD-HHMMSS>/` holding the user's line, the panel's recent messages, session state, recent events, recent model calls and the companion log tail.

When the user asks to fix the bugs:

1. List every bundle. Read the user's line first, then only the parts of the trace that line points to.
2. Check each against the current code before working on it: a bug may already be fixed (a bundle left behind). If so, say so and delete it.
3. Group bundles with the same cause and fix each cause once, with a test that fails before the fix.
4. Delete a bundle only after its fix is committed and the suites pass. Never delete one that wasn't fixed; if a bundle can't be reproduced or understood, leave it and tell the user.
5. End by telling the user, in plain words, what each bug was, which are fixed, how many bundles were deleted and which remain.

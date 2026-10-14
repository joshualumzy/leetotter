# Leetotter companion

Install with `uv sync`. The companion uses Python 3.14. Run the tests with
`uv run pytest`. `uv run python -m companion` creates or migrates the store,
then serves the extension on `127.0.0.1:8765` until stopped. Set
`LEETOTTER_PORT` to use another port. Only the fixed Leetotter extension origin
is accepted by the WebSocket server.

To build the Chrome extension:

```sh
cd extension
npm install
npm run build
npm test
```

If you changed `LEETOTTER_PORT`, set it for `npm run build` too. In Chrome,
open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
and select `extension/dist`. Open Leetotter from Chrome's side panel menu.
The unpacked extension has a fixed ID through the public manifest key; its
private key is not in this repository. The panel reports whether the local
companion is connected and reconnects automatically after a restart.

## NeetCode reader test

The extension injects a page script on neetcode.io to read the single Python
editor tab from localStorage and observe Run and Submit requests and responses.
An isolated script relays tagged events to the worker, which sends them to the
companion over the existing WebSocket. The reader checks for the problem title,
Monaco editor, one readable code tab, and installed network hook. Failures
appear by name in the panel. A Run or Submit without a response within 30
seconds shows "Can't see run results on this page". The companion validates
reader events but does not store them yet.

The worker keeps the reader's watch mode and problem-tab state in Chrome session
storage so a worker restart can restore the page mode. The NeetCode host
permission limits tab URL access; the extension does not request the broader
`tabs` permission.

## NeetCode catalog and history

The checked-in `companion/data/catalog.json` contains the 588 problems from
NeetCode's page script that have an editor link. To refresh it when online, run
`uv run python -m companion.catalog_update` from the repository root. The
updater fetches NeetCode's HTML, locates its `main.*.js`, parses problem
literals as text, checks expected counts and required fields, and replaces
the catalog. It never runs the downloaded JavaScript. If the site changes its
script format or counts, it stops instead of writing partial data.

On first use, open a `neetcode.io` tab and press **Import history** in the
side panel. The page bridge reads `localStorage["synced-progress-cache"]` in
that tab, validates its `completed` category lists, and sends their LeetCode
URLs to the local companion. The companion matches catalog links and stores
one `prior_solve` event per matched problem, with no session or review grade.
The panel shows the matched count and any unmatched URLs. A missing or malformed
cache shows a named error. The import records a one-time marker in SQLite;
afterward the button is hidden. Imported solves mark problems as seen, but do
not count as graded reviews.

**Known risk:** Scripts running on neetcode.io can forge reader events because
`window.postMessage` does not identify the sending script. This is accepted
for this local personal tool.

To check it by hand, start the companion, load `extension/dist` in Chrome, open
the side panel, and visit a NeetCode problem. Press **Reader test: start
watching**. The panel shows the latest event type. Type, Run, Submit, and
switch tabs or windows; the latest event should change to `code_changed`,
`run_result`, `submit_result`, or `focus_changed` respectively. Reloading a
problem while watching should show `problem_opened` and `code_snapshot`.
Press **Reader test: stop watching** when done. While idle the reader forwards
no events. Reader failures are shown in the panel and pause watching until the
check recovers. The real-site check is manual; tests use captured traffic.

For the manual provider smoke test, set `NUS_SOCLAAS_BASE_URL` and
`NUS_SOCLAAS_API_KEY` in the command's environment, then run
`uv run python -m companion.smoke`. The script prints the validated tutor reply.
The API key is never written to the database. Only a successful, validated
call is recorded.

The SQLite file defaults to
`~/Library/Application Support/Leetotter/companion.sqlite3`. Set
`LEETOTTER_DB_PATH` to choose another location.

## NeetCode 150 reference batch

From the repository root, with the provider environment configured, try three
unfinished problems first:

```sh
uv run python -m companion.reference_batch --limit 3
```

Then run the rest, or target one catalog slug:

```sh
uv run python -m companion.reference_batch
uv run python -m companion.reference_batch --slug two-sum
uv run python -m companion.reference_batch --retry-needs-review
```

The batch sends only each problem's title and slug. It writes one reference at
a time through the background writer and Checker roles, which use spare slots
in the model client's rate limiter. A rerun skips stored checked references. Stored
`needs_review` references are skipped unless `--retry-needs-review` is given.
Ctrl-C leaves completed references in SQLite. Each run prints its progress and
overwrites `docs/reference-batch-report.md` with counts and review/failure
reasons. An unavailable model pauses the batch; fix it before rerunning.

## Start the companion at login (macOS)

From the repository root, first store your provider API key in the macOS
Keychain. This command prompts for the key without putting it in the command
line:

```sh
security add-generic-password -a "$USER" -s NUS_SOCLAAS_API_KEY -w
```

Then install the launch agent with your provider's base URL:

```sh
./scripts/install.sh --base-url 'https://your-provider.example/v1'
```

The installer writes the non-secret base URL to
`~/Library/Application Support/Leetotter/provider.env`, starts the companion,
and configures launchd to start it at login and restart it after a crash.
Run the same install command again to update the URL, or omit `--base-url` to
keep the existing URL. The log is at
`~/Library/Logs/Leetotter/companion.log`. macOS may ask once whether to allow
access to the Keychain item; choose **Always Allow** so login starts can read
it. If the item is missing, the companion still starts and the log explains
why model calls will fail. The key is never written to the provider file or
launch agent plist.

To stop and remove the launch agent:

```sh
./scripts/uninstall.sh
```

Uninstall leaves the database, provider URL, log, and Keychain item in place.
For a file-only rehearsal, set `LEETOTTER_HOME` to an existing directory
outside your home and pass `--dry-run` to install or uninstall. Dry-run does
not invoke launchctl.

This setup implements the login-start requirement in [SPEC §3](SPEC.md#3-components),
the environment and Keychain configuration in [SPEC §12](SPEC.md#12-models),
and the key storage rule in [SPEC §14](SPEC.md#14-privacy).

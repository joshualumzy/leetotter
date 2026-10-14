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

For the manual provider smoke test, set `NUS_SOCLAAS_BASE_URL` and
`NUS_SOCLAAS_API_KEY` in the command's environment, then run
`uv run python -m companion.smoke`. The script prints the validated tutor reply.
The API key is never written to the database. Only a successful, validated
call is recorded.

The SQLite file defaults to
`~/Library/Application Support/Leetotter/companion.sqlite3`. Set
`LEETOTTER_DB_PATH` to choose another location.

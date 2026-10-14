# Leetotter companion

Install with `uv sync`. The companion uses Python 3.14. Run the tests with
`uv run pytest`. `uv run python -m companion` creates or migrates the store and
reports where it is.

For the manual provider smoke test, set `NUS_SOCLAAS_BASE_URL` and
`NUS_SOCLAAS_API_KEY` in the command's environment, then run
`uv run python -m companion.smoke`. The script prints the validated tutor reply.
The API key is never written to the database. Only a successful, validated
call is recorded.

The SQLite file defaults to
`~/Library/Application Support/Leetotter/companion.sqlite3`. Set
`LEETOTTER_DB_PATH` to choose another location.

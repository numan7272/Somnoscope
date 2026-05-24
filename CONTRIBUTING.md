# Contributing to Somnoscope

Thanks for wanting to improve Somnoscope. To keep the repo clean long-term — and to
make sure your fixes and features are actually understandable to others — we follow
a few non-negotiable rules.

## Open an issue first

**Every code change starts with an issue** — even an apparently trivial bug. Two reasons:

1. The issue history is the only place where we can later look up *why* something
   was done a certain way.
2. Duplicate work is avoided when intent is visible.

Issue templates live under [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/):

- **Bug Report:** for reproducible failures
- **Feature Request:** for new functions or modules
- For open-ended questions please use GitHub Discussions, not Issues

## Branch names

Format: `<type>/issue-<nr>-<shortname>`

| Type     | When                                              |
|----------|---------------------------------------------------|
| `fix`    | Bug fix                                           |
| `feat`   | New feature / module                              |
| `refac`  | Refactor without behavior change                  |
| `docs`   | Documentation only                                |
| `chore`  | Maintenance (dependencies, CI, scripts)           |

Examples:

```
fix/issue-12-mqtt-reconnect-loop
feat/issue-23-fitbit-ble-adapter
docs/issue-7-architecture-diagram
```

## Pull requests

- **One PR per issue.** If your PR closes multiple issues, the issues were probably
  sliced too small — no drama, just mention it.
- **The PR description follows the [template](.github/pull_request_template.md):**
  what changed, why, how it was tested.
- **`Closes #<nr>`** in the PR body so GitHub auto-links the issue on merge — but
  **does not automatically close it** until we've explicitly confirmed the fix
  (see next section).

## Close issues at merge, not at PR-open

While the PR is open, the bug is *not yet* fixed. We close issues manually with a
**comment summarizing the root cause and the chosen solution**. That note is the
most important artifact for the future.

Bad example:

> Fixed in #42. 👍

Good example:

> **Root cause:** The MQTT client applied no backoff on `on_disconnect` and
> flooded the broker with reconnect attempts.
>
> **Fix:** Added exponential backoff in `iot/mqtt_subscriber.py`, capped at 60s.
> Also added structured logging of reconnect attempts to make Phase 5 debugging
> easier.
>
> **Verified with:** local Mosquitto + forced disconnect via
> `mosquitto_pub -t '#' -m 'kill'`. Logfile now shows clean backoff.

## Code conventions

Detailed in [CLAUDE.md](CLAUDE.md). Short version:

- **Python 3.10+**, `asyncio` for all I/O paths
- **Type hints** everywhere (`str | None` instead of `Optional[str]`)
- **German docstrings** (Google style) — args, returns, side effects
- **Logging, not `print`** (exception: the CLI bootstrap in `main.py`)
- **No cloud calls** for health data — ever

> Note: docstrings and inline comments are in German because the original author
> works in German. PR descriptions, issue conversations, and the README are in
> English. If you're contributing and prefer English in docstrings, raise it in
> an issue first — we may switch over time.

## Tests

Phase 1 has no tests yet — starting in Phase 2 we expect `pytest` tests for every
new parser/adapter. Path: `tests/` (created when the first test lands).

## Setup before your first commit

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
python main.py                     # should run without errors
```

## Questions?

Open an issue or start a discussion — we'll respond as quickly as we can.

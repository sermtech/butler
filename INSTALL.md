# Installing Butler

For a coding agent (Claude Code, Codex, pi) asked "install Butler here", and
for a person. Everything else is in `README.md`.

## What you need first

The installer does not install these; it stops and names the fix.

- [uv](https://docs.astral.sh/uv/getting-started/installation/) on PATH. It
  creates the Python 3.13 environment.
- Node.js 20 or newer, with npm ([nodejs.org](https://nodejs.org/)).
- Any Python 3.10 or newer to run the installer itself (stdlib only).

## The one command

From the repository root:

```
python scripts/install.py
```

Options: `--data DIR` picks the data root (a folder OUTSIDE the repository,
default `~/Butler-Data`), and `--nemotron` also fetches the optional
multilingual speech model. It is safe to run again: every step is idempotent
and the first failure stops the run with exit code 1.

It creates `.venv`, installs the Python and node packages, downloads the
models, fonts and terminal view (checked against pinned SHA-256 values),
creates the data root with its `.butler` records folder, and saves
`BUTLER_DATA`. On Windows that is `setx`, so open a NEW terminal afterwards.
On macOS and Linux it edits no profile: it prints the `export BUTLER_DATA=...`
line for you to add (nothing is needed if the root is `~/Butler-Data`).

## Verify

```
python scripts/install.py --check
```

It must print `N of N ok` and exit 0. `--check` downloads nothing and changes
nothing. It checks model, font and terminal-view files for presence, not hash.

## Start it and confirm it runs

- Windows: `start.bat`
- macOS: `.venv/bin/python -X utf8 scripts/launch.py`

The launcher prints the addresses. On the same machine open
`http://127.0.0.1:8141` (or `curl http://127.0.0.1:8141/`). That is a
read-only check; do not call any route that writes. The certificate for the
phone-facing port is generated on first run.

## Rules for agents

- Never change an existing `BUTLER_DATA`. It points at someone's records. The
  installer refuses to; do not work around it.
- Never put the data root inside the repository.
- Never kill processes by name. If you must stop something, use the PID you
  started.
- If a Butler is already running on this machine (ports 8140-8142), do not
  start a second one: the launcher takes those ports back from whatever holds
  them, which would stop the owner's live Butler.
- Do not use any write route (settings, turns, terminal) to test an install.
- The models carry their own licenses. The wake-word models are CC BY-NC-SA 4.0
  (non-commercial). See "What the installer fetches" and "License" in
  `README.md`; the user accepts them by using Butler.

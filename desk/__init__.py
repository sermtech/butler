"""The desk agent package. This file exists for ONE mechanical reason.

DO NOT DELETE IT, and do not put anything in it.

`daemon/desk.py` is the daemon's supervisor for this process, and `desk/` is
this process. Both are reachable as the name `desk`, and without this file
`desk/` is a NAMESPACE package -- which Python treats as a portion to be
continued rather than as a match, so the search carries on down `sys.path` and
`daemon/desk.py`, a regular module, WINS. `butlerd.py` puts the repository root
at `sys.path[0]` and the interpreter still resolved `import desk` to
`daemon/desk.py`, whose own `from . import childproc` then failed with
"attempted relative import with no known parent package" and the daemon would
not boot.

A regular package at the repository root wins on the first path entry, which is
what this file makes `desk/` into. It matters now because `daemon/shortcuts.py`
imports `desk.actions` -- the ONE key table, deliberately not copied into the
daemon (`docs/shortcuts.md` D3) -- so the daemon reaches this package at import
time and no longer only at spawn time.

Kept EMPTY of behaviour on purpose: `desk/deskd.py` runs as a script and this
module is imported before anything it needs, so a side effect here would run in
the daemon as well as in the agent.
"""

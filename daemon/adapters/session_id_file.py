"""The session FILE of a harness that resumes by an opaque id (AC-44, AC-35).

Not an adapter and not registered: two adapters (`harness_claude`,
`harness_opencode`) share it, the way `harness_pi.reject_shell_metacharacters`
is shared.

Butler names every child's memory by a FILE it mints (`HarnessSpec.
session_file`, `chats.ChatStore.session_ref`): pi writes its whole session
there. Claude Code and opencode keep their sessions in their own stores and
hand back an id instead (`caps.native_resume`). So the adapter keeps that id
INSIDE the file Butler minted, and every caller-side rule written for pi's
file holds unchanged for them: a delete reaps it, a rewind moves it onto the
snapshot, a new conversation mints a new one, and no caller learns which kind
of harness it is talking to (AC-35: never branch on a harness name).

The file is one small JSON object, `{"session_id": "..."}`, written whole with
an atomic replace. A file that is there and unreadable RAISES (fail fast): a
silently fresh session would drop the conversation's context with nothing
saying so.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def read(path: str) -> str | None:
    """The stored id, or None when this conversation has none yet."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(
            f"the session file {p} is unreadable ({e}); it holds this "
            f"conversation's agent session id, so it is refused rather than "
            f"replaced by a fresh session that would forget the conversation"
        ) from None
    sid = data.get("session_id") if isinstance(data, dict) else None
    if not isinstance(sid, str) or not sid.strip():
        raise RuntimeError(
            f"the session file {p} holds no session_id: {data!r}")
    return sid


def write(path: str, session_id: str) -> None:
    """Store `session_id`, atomically. A no-op when it is already stored."""
    p = Path(path)
    if p.exists() and read(path) == session_id:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    os.replace(tmp, p)


def forget(path: str) -> None:
    """The harness said the stored session is GONE: drop the id, so the next
    child starts a fresh session instead of walking back into the dead one
    (`chats.names_a_missing_session` is the only caller's gate)."""
    Path(path).unlink(missing_ok=True)

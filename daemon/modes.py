"""The permission mode store. Server-side, atomic, broadcast. plan.md 4.6.

Server-side is the whole point (D-2): an earlier prototype ended up with one laptop
holding four diverging localStorage copies of a voice setting because the
app answered on four origins. Modes live in state/mode.json and nowhere
else; AC-8's grep gate enforces the client half.

Escalation is asymmetric and that asymmetry is enforced HERE rather than in
the dialogue layer, so no future caller can route around it. DOWN (more
restrictive) always works, from any device, unverified -- refusing a
lockdown is never the safe answer. UP requires a UI click, or from Phase 4 a
speaker-verified confirmation.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Ordered least to most permissive. The index IS the comparison.
ORDER = ("paranoid", "secretary", "trusted")
DEFAULT = "secretary"


class ModeError(ValueError):
    """A refused mode change. Loud on purpose: never silently ignored."""


class ModeStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._mode = self._load()
        self._listeners: list = []

    def _load(self) -> str:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return DEFAULT
        except (json.JSONDecodeError, OSError) as e:
            # Fail fast rather than guessing: a corrupt mode file could mean
            # a half-written escalation, and defaulting to something
            # permissive is the one wrong answer.
            raise ModeError(f"mode file {self.path} is unreadable: {e}") from e
        mode = raw.get("mode")
        if mode not in ORDER:
            raise ModeError(f"mode file names an unknown mode: {mode!r}")
        return mode

    @property
    def mode(self) -> str:
        return self._mode

    def rank(self, mode: str) -> int:
        if mode not in ORDER:
            raise ModeError(f"unknown mode {mode!r}; known: {list(ORDER)}")
        return ORDER.index(mode)

    def is_escalation(self, to: str) -> bool:
        return self.rank(to) > self.rank(self._mode)

    def set(self, to: str, *, authorized: bool = False) -> str:
        """Change the mode. Raises ModeError if this is an unauthorized raise.

        `authorized` is True only for a web UI click or, from Phase 4, a
        speaker-verified spoken confirmation. Voice alone never sets it.
        """
        if to == self._mode:
            return self._mode
        if self.is_escalation(to) and not authorized:
            raise ModeError(
                f"refusing to escalate {self._mode} -> {to} without "
                f"authorization; escalate from the web UI")
        self._write(to)
        self._mode = to
        for fn in list(self._listeners):
            fn(to)
        return to

    def _write(self, mode: str) -> None:
        """Atomic replace: a torn mode file is a security-relevant failure."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"mode": mode}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def on_change(self, fn) -> None:
        """Register a broadcast callback (the hub pushes `mode` to everyone)."""
        self._listeners.append(fn)


# `spoken_confirmation` -- the yes/no grammar behind the write grant's
# repeat-back -- was deleted on 2026-08-18 with AC-29, which the owner
# retired: the harness runs at full capability and nothing is ever repeated
# back to be confirmed. The modes themselves stay (AC-8, AC-10) and become
# enforcement again at Phase 2's worker dispatch, where AC-9's repeat-back
# still applies; until then they gate nothing.

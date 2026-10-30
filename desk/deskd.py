"""The desk agent: hotkeys, microphone, clipboard. Windows, one process.

`docs/desk-hotkeys.md` D1. Everything Win32 lives out here rather than in the
daemon, for the reason `daemon/AGENTS.md` states plainly: "Nothing blocking
runs on the event loop. This process is single-threaded; a blocking engine
call freezes every satellite, the heartbeat and the worker announcements." A
low-level keyboard hook needs its own message pump and audio capture blocks,
so neither belongs there. A hook is also not an engine, so it is not a port.

It owns no model. The recognizer stays in the daemon and this process reaches
it over HTTP at `POST /api/dictate` -- one warm parakeet on the machine, not
two.

THE SHAPE: three threads and one state machine. `hotkeys.Hook` owns the
Windows hook and its message pump and does nothing but classify and enqueue;
`audio.Microphone` owns the stream (open per press, or held open for a
lookback ring when `dictate_preroll_ms` is above 0) on PortAudio's thread;
and the COORDINATOR below owns `Stage` and is the only thing that decides
anything. That split is Handy's
(`transcription_coordinator.rs:71-73`, "serialises all transcription lifecycle
events through a single thread to eliminate race conditions") and it is the
cleanest thing in their codebase.

TWO ACTIONS AND A TABLE THE OWNER OWNS (`docs/shortcuts.md`). `dictate` is a
HOLD action -- the release is what ends it -- and `terminal` fires on the press
alone and carries a directory and a coding agent from the row it belongs to.
Which chord runs which is `desk/actions.Table`, published by
`hotkeys.Hook.set_table` from the `bindings` list the daemon serves beside its
settings. Nothing here is hardcoded to a key any more, and nothing here can
name a command: the actions are a closed set of two and the agent names are a
closed table in the daemon (`desk/AGENTS.md`).

The terminal action ASKS rather than spawns, because this process is inside the
daemon's Job Object and a terminal started from here would close on the next
restart (AC-119).

THIS PROCESS DRAWS NOTHING. No window, no dialog, no tray icon. Everything the
owner configures is on the settings page and reaches here by poll (AC-41), and
the two things that would break if that were not true are the whole feature:
the window they clicked has to stay focused, and this thread has to keep
polling.

WHY IT POLLS RATHER THAN BEING TOLD
-----------------------------------
Settings live in the daemon and nothing pushes them here. A poll is a few
bytes a second against localhost and it recovers by itself from a daemon
restart, where a subscription would have to reconnect and would be one more
thing to get wrong. `GET /api/settings` is the same surface the page reads,
which is what keeps this process out of the settings store entirely.

LOUD, NEVER FATAL (D-1)
-----------------------
A refused connection is normal: the daemon restarts under this process on
every `POST /api/restart`, and it is spawned at boot possibly before the
daemon is serving. So a failed poll is logged and retried forever, never
exited on. The agent does not need to decide when to die -- the daemon's Job
Object kills it when the daemon goes, however the daemon went (AC-122, AC-36).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from desk import actions as actions_mod               # noqa: E402
from desk import audio, hotkeys, paste as paste_mod   # noqa: E402

# How often the settings are re-read. Slow enough to be free, fast enough that
# turning hotkeys off in the page takes effect before the owner has finished
# wondering whether it did.
POLL_SECONDS = 2.0
# One request's budget. Short: this is localhost, and a poll that hangs is a
# poll that stops noticing the setting changed.
REQUEST_TIMEOUT_S = 5.0
# A dictation's own budget, and it is not the poll's. A clip at the 120 s
# ceiling costs 10.51 s to decode [MEASURED 2026-09-09,
# test/manual/bench_dictate.py], so a 5 s timeout would abandon every long
# dictation while the daemon was still answering it correctly.
DICTATE_TIMEOUT_S = 90.0

# The two cues, from `plan.md` Appendix A so the room hears the same tones the
# page uses. LISTENING is played when a capture starts and FAILED when nothing
# can be pasted -- deliberately different, because the failure the owner has to
# be able to tell apart is "it heard nothing" from "it pasted".
CUE_LISTENING = (880, 120)
CUE_DONE = (660, 150)
CUE_FAILED = (300, 220)


def log(message: str) -> None:
    print(f"[desk] {message}", flush=True)


def _ssl_context(url: str):
    """Trust the daemon's own self-signed certificate, and nothing else.

    `scripts/make-cert.py` mints a self-signed cert and the daemon serves TLS
    by default, so the ordinary verification cannot pass. This is a loopback
    connection to a process that spawned THIS one; the alternative is running
    the whole feature over plain HTTP, which is worse for no gain.
    """
    if not url.lower().startswith("https"):
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class Daemon:
    """The daemon, as seen from out here: settings in, dictation out."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._ctx = _ssl_context(self.url)
        self._values: dict = {}
        # The binding table as the daemon serves it, or None for "this daemon
        # has never told us". None and `[]` are DIFFERENT facts and the agent
        # branches on the difference (AC-140).
        self._bindings: list | None = None
        self._reachable = False
        self._said_no_bindings = False

    @property
    def values(self) -> dict:
        return self._values

    @property
    def bindings(self) -> list | None:
        return self._bindings

    def get(self, key: str, default=None):
        return self._values.get(key, default)

    def poll(self) -> bool:
        """Re-read the settings. Answers whether the daemon was reachable.

        The transition is logged in BOTH directions and only on the
        transition: an agent that logs every failed poll fills the daemon's
        log with one line every two seconds while the owner is restarting it,
        and an agent that logs nothing leaves "the hotkey does nothing" with
        no evidence anywhere (AC-7b).
        """
        try:
            request = urllib.request.Request(f"{self.url}/api/settings")
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S,
                                        context=self._ctx) as answer:
                body = json.loads(answer.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError,
                TimeoutError) as e:
            if self._reachable:
                log(f"the daemon stopped answering ({e!r}); retrying every "
                    f"{POLL_SECONDS:g}s")
            self._reachable = False
            return False
        if not self._reachable:
            log(f"settings read from {self.url}")
        self._reachable = True
        # `describe()` serves `{groups: [...], settings: [...]}` and each
        # setting carries its own `value`. There is NO `values` dict -- an
        # earlier version of this method read one and therefore saw an empty
        # settings map forever, so `hotkeys_enabled` was permanently False and
        # the agent silently did nothing. It survived the whole test suite
        # because every double overrides `get`, and the end-to-end probe
        # because that one sets `_values` directly: nothing had ever run this
        # method against the real route (D-18 -- the CALLER needs the
        # assertion, and here the caller was never exercised).
        self._values = {s["key"]: s.get("value")
                        for s in (body.get("settings") or []) if "key" in s}
        if not self._values:
            log(f"WARNING: {self.url}/api/settings answered with no settings; "
                f"top-level keys were {sorted(body)}")
        # The binding table rides beside the settings, composed by the route
        # from the shortcuts store (`docs/shortcuts.md` 4.2). A MISSING key is
        # a REFUSAL and not an instruction to clear: it means this daemon does
        # not serve bindings, which is a daemon to keep the running table for
        # rather than one to unbind every key on the owner's machine for
        # (AC-140). That distinction is the same class as the `values`-dict
        # defect this method's comment above records.
        offered = body.get("bindings")
        if isinstance(offered, list):
            self._bindings = offered
            self._said_no_bindings = False
        elif not self._said_no_bindings:
            self._said_no_bindings = True
            log(f"WARNING: {self.url}/api/settings served no bindings list "
                f"(top-level keys were {sorted(body)}); the running hotkeys "
                f"are left exactly as they are")
        return True

    def _post(self, path: str, data: bytes, content_type: str,
              timeout: float) -> tuple[int, dict]:
        """POST and answer `(status, payload)`. Never raises.

        ONE shape for every request this agent makes, because every route it
        calls answers the same way: a status the caller branches on and a body
        carrying the reason. A transport failure becomes 0 with the reason in
        the payload, so there is one thing to branch on rather than two -- and
        an exception escaping here would reach `_handle` and wedge the stage
        machine, which is a hotkey that never works again (D-1).
        """
        request = urllib.request.Request(
            f"{self.url}{path}", data=data, method="POST",
            headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=timeout,
                                        context=self._ctx) as answer:
                return answer.status, json.loads(answer.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"error": e.reason}
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            return 0, {"error": f"the daemon could not be reached: {e}"}

    def dictate(self, pcm: bytes) -> tuple[int, dict]:
        """POST one clip and answer `(status, payload)`.

        Every status this route gives is meaningful and none of them is an
        exception: 409 busy, 413 too long, 422 nothing said, 502 the
        recognizer fell over (`daemon/AGENTS.md`).
        """
        return self._post("/api/dictate", pcm, "application/octet-stream",
                          DICTATE_TIMEOUT_S)

    def open_terminal(self, cwd: str = "", agent: str = "") -> tuple[int, dict]:
        """Ask the DAEMON to open a terminal (AC-136, AC-119, AC-141).

        **THIS SIGNATURE IS A DELIBERATE REVERSAL, and the docstring is
        rewritten rather than relaxed** (D-12, `docs/shortcuts.md` 4.6). It
        used to take NO argument, and `test_desk_dictation.py` asserted that,
        because a `cwd` coming back would mean the desk agent had started
        deciding where a coding agent runs. The owner asked for a pane of
        project rows with a chord each, so it decides again -- but only in the
        narrow sense that it FORWARDS what its own binding carries. It still
        chooses nothing, prompts for nothing and draws nothing: the row, its
        directory and its agent were all typed on the page, and this process
        holds them only because the hook has to know which chord it saw.

        `agent` is a NAME the daemon resolves through its own closed
        `{pi, claude, opencode}` table. Nothing here is a command line, and a
        blank of either falls back to the daemon's own answer rather than to a
        guess made out here.

        The daemon spawns rather than this process for a mechanical reason:
        this process is inside the daemon's kill-on-close Job Object, which
        sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and nothing else -- so
        `CREATE_BREAKAWAY_FROM_JOB` from in here fails with
        ERROR_ACCESS_DENIED and a terminal spawned here would close the moment
        the daemon restarted. The daemon is in no job and its unassigned
        children simply outlive it.
        """
        body = {}
        if cwd:
            body["cwd"] = cwd
        if agent:
            body["agent"] = agent
        return self._post("/api/terminal", json.dumps(body).encode("utf-8"),
                          "application/json", REQUEST_TIMEOUT_S)


def cue(tone: tuple[int, int], enabled: bool = True) -> None:
    """One beep, blocking, or nothing at all off Windows.

    BLOCKING on purpose, and it is the cheapest honest thing this agent does.
    Handy's #1879 is a level meter that animates before capture is live, which
    tells the owner it is safe to speak when it is not
    (`.references/push-to-talk-prior-art.md` section 6). A cue that returns
    before it has sounded is the same lie.
    """
    if not enabled or os.name != "nt":
        return
    try:
        import winsound
        winsound.Beep(int(tone[0]), int(tone[1]))
    except Exception as e:                                # noqa: BLE001
        # A machine with no sound device must not lose its hotkeys over a
        # beep. Reported once per occurrence and never raised (D-1).
        log(f"the cue would not sound: {e!r}")


IDLE, RECORDING, PROCESSING = "idle", "recording", "processing"

# Shorter than this is a mistouch rather than a dictation. Mirrors the
# daemon's own `min_speech_ms` default; it is not read from settings because
# that one is about a turn's endpointing and this is about a finger.
MIN_HOLD_MS = 200

# How long an OPEN stream may deliver nothing before it is treated as dead
# (docs/desk-audio.md, AC-127). A live stream produces a block every 32 ms
# whether or not anyone is speaking, because silence is data -- so a second of
# nothing is not a quiet room, it is a stream that has stopped. Generous
# against a scheduling hiccup and far short of the two seconds between polls.
STREAM_DEAD_S = 1.0

# How far past the capture ceiling a key-up may be before the agent concludes it
# is never coming (AC-145, `Agent.check_stuck`). Generous, because the only
# thing it must never do is fire on a capture that is still running: the
# microphone stopped accumulating at the ceiling, so the seconds spent waiting
# here cost no audio at all.
STUCK_SLACK_S = 3.0


class Agent:
    """The state machine, and the only thing here that decides anything.

    One thread owns `stage`. The hook enqueues edges from its own thread and
    PortAudio fills the ring from a third; neither of them branches. That is
    the split Handy arrived at after the races
    (`transcription_coordinator.rs:71-73`, "serialises all transcription
    lifecycle events through a single thread to eliminate race conditions"),
    and it is what makes "a press during processing" a decision rather than
    an accident.
    """

    def __init__(self, daemon: "Daemon"):
        self.daemon = daemon
        self.stage = IDLE
        self.hook = None
        self.mic = None
        self._enabled = False
        # Which keys are physically DOWN, and what each one's press started.
        # A dict rather than the single boolean this replaced, because two
        # bindings can be held at once and a latch shared between them would
        # read the second press as auto-repeat.
        self._held: dict[int, str] = {}
        # Which key owns the RECORDING stage, and which modifiers its chord
        # carries. `_capturing_vk` exists because of a specific interleaving:
        # with two hold bindings, press A, press B (refused -- already
        # RECORDING), release B. A single boolean would end A's capture and
        # post a clip the owner is still speaking into. `_capturing_mods`
        # reaches `paste`, which must not inject a key-up for a modifier the
        # owner is physically holding (`docs/shortcuts.md` 4.6).
        self._capturing_vk: int | None = None
        self._capturing_mods = 0
        self._began_at = 0.0
        # The live table, and the wire rows it was built from. The rows are
        # kept so a poll that serves the same table again costs nothing, and
        # so a REFUSAL can leave both exactly as they were (AC-140).
        self._table = actions_mod.Table()
        self._rows: list | None = None
        self._said_bad_table = ""

    # -- turning the feature on and off ------------------------------------- #
    def apply_settings(self) -> None:
        """Reconcile with the daemon: first the bindings, then on/off.

        SPLIT IN TWO on purpose (`docs/shortcuts.md` 4.5). A binding change
        must not run `_start`/`_stop`: that would drop a held key's key-up,
        close and reopen the audio stream -- the fragile operation
        `docs/desk-audio.md` is entirely about -- and log `hotkeys OFF` then
        `hotkeys ON` on every keystroke of an edit on the page.

        Bindings FIRST, because `_start` logs the table it installed and would
        otherwise name the previous one on the poll that turns hotkeys on.
        """
        self._apply_bindings()
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        """Start or stop the hook and the microphone to match the setting.

        OFF means the hook is UNINSTALLED and the stream is CLOSED, not merely
        ignored: a suppressed key and an open microphone are both things the
        owner switched off, and leaving either running makes the setting a lie
        (AC-121).
        """
        want = bool(self.daemon.get("hotkeys_enabled", False))
        if want == self._enabled:
            return
        self._start() if want else self._stop()

    def _apply_bindings(self) -> None:
        """Build a table from what the daemon serves, and publish it if new.

        THREE REFUSALS, and none of them changes anything (AC-140). A daemon
        that served no `bindings` key at all (`Daemon.poll` keeps None). A
        table that will not parse -- an unknown key, a bare letter, one chord
        bound twice. And an EMPTY list. Clearing on any of those turns the
        owner's hotkeys off silently, and defaulting binds a key nobody asked
        for; both are AC-7b with a global keyboard hook attached.

        The reason is said ONCE per distinct message rather than once per poll,
        or a single bad row fills `butler.log` with a line every two seconds.
        """
        rows = self.daemon.bindings
        if rows is None or rows == self._rows:
            return
        if not rows:
            self._complain("the daemon serves an EMPTY binding table; the "
                           "running hotkeys are left as they are. Add a "
                           "shortcut on the page, or check the dictation "
                           "chord.")
            return
        try:
            table = actions_mod.Table(rows)
        except actions_mod.BindingError as e:
            self._complain(f"the binding table was refused ({e}); the running "
                           f"hotkeys are left as they are")
            return
        self._said_bad_table = ""
        self._rows = list(rows)
        if table == self._table:
            return
        self._table = table
        if self.hook is not None:
            self.hook.set_table(table)
            # Said on every real change, because this line is the owner's only
            # record of which keys this process has taken over system-wide
            # (AC-142), and a rebind changes exactly that.
            log(f"bindings: {table.describe()}")

    def _complain(self, message: str) -> None:
        if self._said_bad_table == message:
            return
        self._said_bad_table = message
        log(f"WARNING: {message}")

    def _start(self) -> None:
        # Checked BEFORE anything is opened. A hook installed on a session
        # that does not own the input desktop succeeds and then receives
        # nothing, forever, with no error -- so this is the difference between
        # a diagnosable condition and a hotkey the owner reports as broken
        # (AC-7b). Not fatal: the session can come back, and the next poll
        # tries again.
        reachable, why = hotkeys.input_desktop_reachable()
        if not reachable:
            if not getattr(self, "_said_no_desktop", False):
                log(f"WARNING: hotkeys are on but {why}")
                self._said_no_desktop = True
            return
        self._said_no_desktop = False
        if not len(self._table):
            # Nothing is bound, so there is nothing to install and nothing to
            # capture for. Said rather than done silently: "hotkeys are on and
            # no key does anything" is a state the owner has to be able to see
            # the reason for (AC-7b, AC-140).
            self._complain("hotkeys are on but no chord is bound, so nothing "
                           "is installed. Add a shortcut on the page.")
            return
        preroll = int(self.daemon.get("dictate_preroll_ms", 0) or 0)
        ceiling = int(self.daemon.get("dictate_max_seconds", 120) or 120)
        try:
            self.mic = audio.Microphone(preroll_ms=preroll,
                                        max_seconds=ceiling)
            self.mic.open()
        except audio.MicrophoneError as e:
            # Loud and never fatal. With no microphone the hotkey has nothing
            # to capture, so the hook is not installed either: a suppressed
            # key that can never produce text is worse than no hotkey (D-1).
            log(f"WARNING: {e}; hotkeys stay off")
            self.mic = None
            return
        self.hook = hotkeys.Hook(self._table)
        if not self.hook.start():
            log(f"WARNING: the keyboard hook would not install "
                f"({self.hook.error}); hotkeys stay off")
            self.hook = None
            self.mic.close()
            self.mic = None
            return
        self._enabled = True
        # The DEVICE is named (AC-128). Until this line existed, "which
        # microphone is Butler using" was unanswerable from the log on a
        # machine whose default input turned out to be an RDP redirection
        # endpoint rather than anything on the desk.
        # GENERATED FROM THE TABLE (AC-142). This line used to hardcode
        # "numpad * dictates", which became a lie the moment the table was
        # configurable -- and it is the owner's only record of which keys are
        # now suppressed system-wide on their machine.
        log(f"hotkeys ON: {self._table.describe()}; {preroll} ms of lookback, "
            f"{ceiling} s ceiling, microphone {self.mic.info}")

    def _stop(self) -> None:
        if self.hook is not None:
            self.hook.stop()
            self.hook = None
        if self.mic is not None:
            if self.mic.dropped_blocks:
                log(f"the microphone reported {self.mic.dropped_blocks} "
                    f"dropped blocks while it was open")
            self.mic.close()
            self.mic = None
        self.stage = IDLE
        # Cleared with the hook that would have delivered the release. A key
        # held across this transition never sends its key-up here, and a latch
        # left set reads every later press as auto-repeat -- a hotkey that is
        # dead until the daemon restarts.
        self._held.clear()
        self._capturing_vk = None
        self._capturing_mods = 0
        if self._enabled:
            log("hotkeys OFF: hook uninstalled, microphone closed")
        self._enabled = False

    # -- events ------------------------------------------------------------- #
    #
    # The closed action registry: a name from `desk/actions.ACTIONS` to the one
    # method that carries it out. A third action is a method plus a line here,
    # never a config file that can name a command (`desk/AGENTS.md`).
    _ACTIONS = {"dictate": "_begin", "terminal": "_open_terminal"}

    def drain(self) -> None:
        """Handle every hotkey edge waiting, then return."""
        if self.hook is None:
            return
        while True:
            try:
                binding, edge = self.hook.events.get_nowait()
            except queue.Empty:
                return
            self._handle(binding, edge)

    def _handle(self, binding, edge: str) -> None:
        """One hotkey edge, carrying the binding its key-DOWN resolved to.

        The hook now resolves once on the press and REPLAYS that binding on the
        release and on every auto-repeat (`hotkeys.classify`), so the whole
        class of mislabelled-edge defects this file used to work around is gone
        at the source: a release can no longer arrive labelled as a different
        action because the owner let go of Ctrl first. What remains here is the
        auto-repeat latch -- the hook still delivers a repeat rather than
        swallowing it, because the AGENT is the only thing that knows whether a
        second press is an instruction -- and the rule that a release ends
        whatever the same KEY started.
        """
        if edge == hotkeys.UP:
            self._held.pop(binding.vk, None)
            # Only the key that OWNS the capture ends it. With two hold
            # bindings, press A, press B (refused, already RECORDING), release
            # B -- and a single boolean would post a clip the owner is still
            # speaking into.
            if self.stage == RECORDING and self._capturing_vk == binding.vk:
                self._finish()
            return
        # A low-level hook sees a held key as a STREAM of key-downs with no
        # key-up between them, and only the leading edge is an instruction.
        # Before this latch existed the terminal branch held no state at all:
        # `butler.log` carried 138 repeats of one held chord against 4 real
        # dictations, and with the action built that is 138 coding agents.
        if binding.vk in self._held:
            return
        self._held[binding.vk] = binding.action
        method = self._ACTIONS.get(binding.action)
        if method is None:
            # Unreachable through `Table`, which refuses an action the registry
            # does not have. Said rather than passed over, because the shape of
            # this failure -- a bound key that suppresses and then does nothing
            # -- is exactly the one AC-7b is named for.
            log(f"WARNING: {binding.chord} names the action "
                f"{binding.action!r}, which this agent cannot carry out")
            return
        getattr(self, method)(binding)

    def check_stuck(self) -> None:
        """Recover a key-up the hook never delivered (AC-145). A TIMEOUT.

        Windows does not REMOVE a hook on one timeout -- it SKIPS it for that
        event -- so a single stall can lose exactly one edge. A lost key-up
        leaves the capturing key in `_held` and the stage in RECORDING, which is
        a hotkey that is dead FOREVER: the microphone stops accumulating at its
        own ceiling but nothing ever ends the capture, so every later press
        finds a stage that is not IDLE and does nothing.

        **THE FIRST VERSION OF THIS ASKED WHETHER THE KEY WAS STILL DOWN, AND
        THAT IS IMPOSSIBLE.** It shipped for one commit and broke dictation
        completely: `GetAsyncKeyState` reports a SUPPRESSED key as UP, because
        returning non-zero from a low-level hook is precisely Windows not
        updating the async key-state table for it [MEASURED 2026-09-09, 5 of 5
        each way with suppression as the only variable --
        `test/manual/probe_asyncstate.py`]. So this tick ended every capture
        inside 200 ms, every clip was the lookback ring alone, and the daemon
        answered "nothing was said in that clip" -- a statement about SPEECH for
        a failure that was nothing of the kind. Every key this agent can be
        waiting on is a key it is suppressing, so no poll can ever work here.

        A timeout needs no key state. It CANNOT fire early, because the
        microphone has already stopped accumulating at `dictate_max_seconds`
        (`Microphone._on_block` sets `_overflow` there), so finishing a moment
        later delivers exactly what a released key would have delivered and
        logs the same "the ceiling stopped this capture" line. What it costs is
        that a lost key-up is recovered after the ceiling rather than at once --
        far better than never, and the honest bound on what this can know.
        """
        if self.stage != RECORDING or self._capturing_vk is None:
            return
        # Every tick: at the ceiling the key may still be down while nothing is
        # recorded, and a per-press device must not stay pinned (AC-820).
        self.mic.release_if_full()
        ceiling = float(self.daemon.get("dictate_max_seconds", 120) or 120)
        held_s = time.monotonic() - self._began_at
        if held_s < ceiling + STUCK_SLACK_S:
            return
        log(f"no key-up arrived in {held_s:.0f} s, past the {ceiling:.0f} s "
            f"ceiling; finishing this capture so the hotkey works again")
        self._held.pop(self._capturing_vk, None)
        self._finish()

    def _open_terminal(self, binding) -> None:
        """A chord that opens a coding agent where its ROW points (AC-136).

        It ASKS NOTHING and DRAWS NOTHING -- no window, no prompt, no dialog.
        The directory and the agent name came off the page, through the
        shortcuts store and the binding table, and this process forwards them
        (`docs/shortcuts.md` 4.6). Anything drawn from here takes the
        foreground, and the whole of AC-114's second half is that the window
        the owner clicked stays the window they clicked; and a modal blocks
        this thread, so the settings poll stops and every dictation is refused
        until somebody dismisses it.

        Runs in the PROCESSING stage under the same `finally` `_finish` uses,
        so a request that throws cannot leave a stage that refuses every
        future press (D-1). Every failure logs its reason and cues, because a
        chord that opens no window and says nothing is indistinguishable from
        a hotkey that never fired (AC-7b).
        """
        self.stage = PROCESSING
        try:
            quiet = not bool(self.daemon.get("cues", True))
            status, payload = self.daemon.open_terminal(binding.cwd,
                                                       binding.agent)
            if status != 200:
                log(f"no terminal ({status}): {(payload or {}).get('error')}")
                cue(CUE_FAILED, not quiet)
                return
            # No pid: `wt.exe` hands off to Windows Terminal, which owns the
            # resulting shell, so there is no process id the daemon could
            # honestly report (see `butlerd.spawn_terminal`).
            answer = payload or {}
            log(f"terminal open: {answer.get('agent')} in "
                f"{answer.get('cwd')}")
            cue(CUE_DONE, not quiet)
        finally:
            self.stage = IDLE

    def _revive(self, why: str) -> bool:
        """Tear the stream down and open a new one. Answers whether it took.

        MEASURED 2026-09-09: a stream that has gone deaf never recovers, while
        a FRESH stream on the same device delivers immediately. `apply_settings`
        cannot help -- it only acts when the SETTING changes, so a daemon whose
        `hotkeys_enabled` stays True never re-examines a microphone that died
        under it, which is how dictation stayed broken until a restart.
        """
        log(f"the microphone {why}; reopening it")
        try:
            self.mic.reopen()
        except audio.MicrophoneError as e:
            # Loud, never fatal (D-1). A hotkey that cannot capture is worth
            # far less than a hotkey that is gone: the next press tries again.
            log(f"WARNING: the microphone would not reopen: {e}")
            return False
        log(f"the microphone is open again: {self.mic.info}")
        return True

    def _begin(self, binding) -> None:
        """Start a capture, unless one is already running.

        A second hold chord pressed while one is recording is REFUSED rather
        than allowed to take over: the first key's release is still owed, and a
        capture handed between two keys belongs to neither.
        """
        if self.stage != IDLE:
            return
        self._capturing_vk = binding.vk
        self._capturing_mods = binding.mods
        # Checked BEFORE the capture rather than after it, so the owner's press
        # WORKS instead of merely failing with a better message. A stream that
        # has produced nothing for a second is dead and that is knowable now.
        # Only a held-open stream can go deaf between presses (AC-819).
        if self.mic.holds_open and self.mic.silent_for() > STREAM_DEAD_S:
            self._revive(f"has delivered nothing for "
                         f"{self.mic.silent_for():.1f} s")
        # The device opens BEFORE the cue (AC-821): the cue is a blocking beep,
        # so the owner hears it only once capture is ready, and a failed open
        # never plays the listening tone. The beep can land in the first block
        # of the capture; a pure tone is not speech and the recognizer answers
        # nothing for it, so it costs a little audio rather than a wrong word.
        try:
            self.mic.begin()
        except audio.MicrophoneError as e:
            # The key stays in `_held` until its key-up, so the NEXT press
            # retries the open (AC-821). Nothing is posted or pasted.
            log(f"WARNING: {e}; this press records nothing, the next one "
                f"tries again")
            cue(CUE_FAILED, bool(self.daemon.get("cues", True)))
            self._capturing_vk = None
            self._capturing_mods = 0
            return
        cue(CUE_LISTENING, bool(self.daemon.get("cues", True)))
        self._began_at = time.monotonic()
        self.stage = RECORDING

    def _finish(self) -> None:
        held_ms = (time.monotonic() - self._began_at) * 1000.0
        pcm, overflowed = self.mic.end()
        mods = self._capturing_mods
        self._capturing_vk = None
        self._capturing_mods = 0
        self.stage = PROCESSING
        try:
            self._deliver(pcm, overflowed, held_ms, mods)
        finally:
            # A `finally`, and it is load-bearing. Handy uses a drop guard for
            # exactly this (`actions.rs:37-44`): a state machine left in
            # PROCESSING refuses every future press, and the owner has a dead
            # hotkey with nothing on screen to say why.
            self.stage = IDLE

    def _deliver(self, pcm: bytes, overflowed: bool, held_ms: float,
                 mods: int = 0) -> None:
        quiet = not bool(self.daemon.get("cues", True))
        seconds = len(pcm) / (audio.SAMPLE_RATE * 2)
        if overflowed:
            log(f"the ceiling stopped this capture at {seconds:.0f} s; "
                f"decoding what was heard")
        if held_ms >= MIN_HOLD_MS and self.mic.live_blocks == 0:
            # AC-126. The DEVICE failed, and this is checked before anything
            # else because both of the paths below would misreport it. Sending
            # the clip gets "nothing was said in that clip" -- a statement
            # about SPEECH -- from a recognizer handed a stale lookback ring,
            # which is exactly what happened on the owner's machine and cost
            # an hour on the wrong organ. And an EMPTY ring makes the capture
            # 0 bytes, which the mistouch check below would swallow in
            # silence, with no cue and no log at all.
            log(f"NO AUDIO: the microphone delivered nothing while the key "
                f"was held for {held_ms:.0f} ms -- {self.mic.info}. Another "
                f"application may be holding it (the Butler page in a browser "
                f"holds it for the wake word).")
            cue(CUE_FAILED, not quiet)
            # Per press the next press opens a fresh stream anyway, and a
            # reopen would log "open again" about a device nobody holds
            # (AC-823).
            if self.mic.holds_open:
                self._revive("delivered no audio during a capture")
            return
        if len(pcm) < audio.BYTES_PER_MS * MIN_HOLD_MS:
            # A brushed key, not a dictation. Silent on purpose: a failure cue
            # for a mistouch is noise the owner cannot act on.
            return
        started = time.monotonic()
        status, payload = self.daemon.dictate(pcm)
        took = time.monotonic() - started
        text = (payload or {}).get("text") or ""
        if status != 200 or not text:
            # AC-115: NOTHING is written -- not the clipboard, not a
            # keystroke. The reason goes where a person can find it and the
            # room gets a cue it can tell apart from success.
            log(f"no text ({status}): {(payload or {}).get('error')!r} "
                f"[{seconds:.1f} s of audio, {took:.1f} s round trip]")
            cue(CUE_FAILED, not quiet)
            return
        method = str(self.daemon.get("dictate_paste", "ctrl_v"))
        try:
            # The capturing chord's OWN modifiers are excluded from the wait
            # (`docs/shortcuts.md` 4.6). Until bindings were configurable the
            # only chord with a modifier pasted nothing, so every held modifier
            # could be treated as an accident; a hold binding on `ctrl+f13`
            # makes it the normal case, and injecting Ctrl-up for a key the
            # owner is pressing leaves Windows and the application disagreeing
            # about it.
            how = paste_mod.paste(text, method,
                                  ignore_mods=actions_mod.mod_names(mods))
        except paste_mod.ClipboardError as e:
            log(f"the clipboard refused the transcript: {e}")
            cue(CUE_FAILED, not quiet)
            return
        log(f"{how}: {len(text)} chars from {seconds:.1f} s of audio "
            f"({took:.1f} s round trip)")
        cue(CUE_DONE, not quiet)


def run(url: str, poll_seconds: float = POLL_SECONDS) -> int:
    """Hold settings and serve hotkeys until the daemon takes this down.

    There is deliberately no exit condition. The Job Object is what ends this
    process, and it ends it for any reason the daemon ended -- including the
    `TerminateProcess` that `atexit` never survives (AC-122, AC-36, D-24).

    The hotkey queue is drained far more often than the settings are polled,
    because a key-up waiting two seconds for its turn is a dictation the owner
    has already given up on.
    """
    daemon = Daemon(url)
    agent = Agent(daemon)
    log(f"agent up, pid {os.getpid()}, watching {daemon.url}")
    next_poll = 0.0
    try:
        while True:
            if time.monotonic() >= next_poll:
                if daemon.poll():
                    agent.apply_settings()
                next_poll = time.monotonic() + poll_seconds
            agent.drain()
            # On the same 10 ms tick as the drain, because what it recovers is
            # a key-up that never arrived and there is nothing in the queue to
            # notice it with (AC-145).
            agent.check_stuck()
            time.sleep(0.01)
    finally:
        agent._stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deskd")
    ap.add_argument("--url", default=os.environ.get("BUTLER_URL", ""),
                    help="the daemon this agent belongs to")
    ap.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    ap.add_argument("--check", action="store_true",
                    help="import, print the version line and exit 0; what the "
                         "suite uses to prove the script is runnable at all")
    args = ap.parse_args(argv)

    if args.check:
        log(f"check ok, python {sys.version.split()[0]}, os {os.name}")
        return 0
    if not args.url:
        # Fail fast rather than polling nothing forever: an agent with no
        # daemon to read is a process that looks alive and decides nothing.
        log("no daemon url: pass --url or set BUTLER_URL")
        return 2
    return run(args.url, args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())

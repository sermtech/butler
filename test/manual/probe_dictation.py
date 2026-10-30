"""Step 3 end to end: a real hook, a real key, a real decode, real clipboard.

The only thing faked is the physical microphone -- fixture audio is pushed at
`Microphone._on_block`, the seam PortAudio's thread uses -- and the only thing
faked is the finger, because `SendInput` presses the key. Everything between
them is the shipped path: the `WH_KEYBOARD_LL` hook, the coordinator's state
machine, `POST /api/dictate` against a real parakeet, the sanitiser, and the
Windows clipboard.

WHY THIS IS MANUAL AND NOT PYTEST
---------------------------------
It presses a key and writes the clipboard. An earlier prototype declined to automate the
equivalent for the reason their notes give: "a test that pressed Ctrl+V would
paste into whatever window the person running the suite had focused". Two
things make this one safe to run anyway, and both are load-bearing:

  * the hook SUPPRESSES the key it injects, so no other application ever sees
    the keystroke; and
  * delivery defaults to `clipboard_only`, so nothing is pasted anywhere. The
    owner's clipboard IS overwritten, and is saved and restored around the run.

`--paste` opts into the real chord. It launches its OWN Notepad, waits for it
to take the foreground, pastes into that, reads the text back, and closes it.
Nothing the operator had open is touched.

SCRATCH DAEMON RULES (root AGENTS.md)
-------------------------------------
Its own `BUTLER_STATE`, a free port, never 8140, and it is killed by the PID
this script recorded -- never by matching a process name, which is how a
subagent took out the owner's live daemon on 2026-08-19.

Run:  .venv/Scripts/python.exe -X utf8 test/manual/probe_dictation.py
      .venv/Scripts/python.exe -X utf8 test/manual/probe_dictation.py --paste
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from desk import actions, audio, deskd, hotkeys, paste as paste_mod  # noqa: E402

FIXTURE = "long_utterance.wav"
BOOT_TIMEOUT_S = 240
SETTLE_S = 0.25


def _norm(text: str) -> str:
    """Case and punctuation removed. The words are the claim; the engine's
    comma placement is a different axis and asserting it makes this probe
    fail on days the recognizer got BETTER."""
    import re
    # Joined back into a string rather than left as a list: the `--paste`
    # check asks whether the transcript is CONTAINED in what Notepad held,
    # and containment on a list of words is not the substring test it looks
    # like.
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fixture_blocks(name: str) -> list[bytes]:
    """The fixture as capture-sized blocks, the shape the callback delivers."""
    with wave.open(str(REPO / "test" / "assets" / name), "rb") as w:
        raw = w.readframes(w.getnframes())
    step = audio.BLOCK * 2
    return [raw[i:i + step] for i in range(0, len(raw), step)]


def boot_daemon(state: Path) -> tuple[subprocess.Popen, str]:
    """A scratch daemon with the REAL recognizer and its own state."""
    port = free_port()
    state.mkdir(parents=True, exist_ok=True)
    # Hotkeys OFF in the scratch daemon's own settings: this probe drives the
    # agent IN PROCESS, and a second agent spawned by the daemon would install
    # a second hook on the same key.
    (state / "settings.json").write_text(
        json.dumps({"hotkeys_enabled": False}), encoding="utf-8")
    env = {**os.environ, "BUTLER_ASR": "transcribecpp", "BUTLER_TTS": "fake",
           "BUTLER_BRAIN": "fake", "BUTLER_PORT": str(port),
           "BUTLER_STATE": str(state), "BUTLER_TLS": "0",
           "BUTLER_ENDPOINTER": "none", "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(REPO / "daemon" / "butlerd.py")],
        cwd=str(REPO), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + BOOT_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"the scratch daemon exited {proc.returncode}")
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc, base
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the scratch daemon never opened {port}")


class Probe(deskd.Daemon):
    """The real `Daemon`, with settings this probe chooses."""

    def __init__(self, url, values):
        super().__init__(url)
        self._values = values


# The chord this probe drives, and the table it installs. Built from the ONE key
# table (`desk/actions.py`) rather than from a VK written down here, and handed
# to the hook exactly the way `Agent._apply_bindings` hands it the daemon's --
# the scratch daemon has no shortcut rows, so there is nothing to poll.
PROBE_TABLE = actions.Table([{"chord": "numpad_multiply", "action": "dictate"}])
MULTIPLY = actions.KEYS["numpad_multiply"].vk


def press(vk=None, up=False) -> None:
    """Inject the hotkey as a FINGER, not as the agent's own injection.

    `mark=0` matters: every keystroke the agent sends carries `hotkeys.MARK`
    and its own hook passes those through untouched, so a probe that used the
    default would press a key the hook is built to ignore.
    """
    import ctypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    paste_mod._send(u32, (MULTIPLY if vk is None else vk, up), mark=0)


def run_one(base: str, method: str) -> tuple[str, float]:
    """One whole dictation, driven by an injected key. Answers (text, seconds)."""
    daemon = Probe(base, {"hotkeys_enabled": True, "cues": False,
                          "dictate_paste": method,
                          "dictate_preroll_ms": 400,
                          "dictate_max_seconds": 120})
    agent = deskd.Agent(daemon)
    agent.mic = audio.Microphone(preroll_ms=400, max_seconds=120)
    agent._table = PROBE_TABLE
    agent.hook = hotkeys.Hook(PROBE_TABLE)
    if not agent.hook.start():
        raise SystemExit(f"the hook would not install: {agent.hook.error}")
    agent._enabled = True
    blocks = fixture_blocks(FIXTURE)
    # A little room noise BEFORE the key, so the lookback ring is not empty
    # and the capture genuinely starts in the past.
    for _ in range(12):
        agent.mic._on_block(bytes(audio.BLOCK * 2), audio.BLOCK, None, None)
    try:
        started = time.monotonic()
        press()                                   # key DOWN, suppressed
        time.sleep(SETTLE_S)
        agent.drain()
        if agent.stage != deskd.RECORDING:
            raise SystemExit(f"the hook did not start a capture "
                             f"(stage {agent.stage!r}); was the key "
                             f"suppressed by something else?")
        for chunk in blocks:
            agent.mic._on_block(chunk, audio.BLOCK, None, None)
        press(up=True)                            # key UP
        time.sleep(SETTLE_S)
        agent.drain()                             # this does the whole delivery
        return paste_mod.get_clipboard() or "", time.monotonic() - started
    finally:
        agent.hook.stop()


def with_notepad(base: str) -> str:
    """Paste for real, into a Notepad window this probe OWNS.

    THE HARD-WON PART IS THE OWNERSHIP CHECK, and it is here because the
    first version of this function did not have it. It launched a bare
    `notepad.exe`, Windows 11 Notepad RESTORED THE OPERATOR'S PREVIOUS TAB,
    and the probe pasted 95 characters into the middle of a document they
    were writing -- which Notepad then autosaved into its session state
    before this function killed it. Nothing about "I launched this process"
    means "this window is mine".

    So: a named temporary file is opened explicitly, and the foreground
    window's TITLE must contain that file's name before a single key is
    injected. If it does not, nothing is pressed and the probe says why.
    """
    import ctypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.GetForegroundWindow.restype = ctypes.c_void_p
    scratch = REPO / "TMP" / f"probe-paste-{os.getpid()}.txt"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("", encoding="utf-8")
    notepad = subprocess.Popen(["notepad.exe", str(scratch)])
    try:
        time.sleep(2.0)                            # let it take the foreground
        title = ctypes.create_unicode_buffer(512)
        hwnd = u32.GetForegroundWindow()
        u32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 512)
        if scratch.stem not in (title.value or ""):
            return (f"REFUSED: the foreground window is {title.value!r}, not "
                    f"the scratch file this probe opened. Nothing was "
                    f"pressed.")
        before = hwnd
        run_one(base, "ctrl_v")
        time.sleep(0.6)
        if u32.GetForegroundWindow() != before:
            return "SKIPPED: the foreground window changed under the probe"
        # Read it back the way a person would: select all, copy, look.
        for vk, up in ((0x11, False), (0x41, False), (0x41, True),
                       (0x11, True), (0x11, False), (0x43, False),
                       (0x43, True), (0x11, True)):
            paste_mod._send(u32, (vk, up))
            time.sleep(0.03)
        time.sleep(0.3)
        return paste_mod.get_clipboard() or ""
    finally:
        notepad.kill()
        try:
            scratch.unlink()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paste", action="store_true",
                    help="also press Ctrl+V into a Notepad this probe owns")
    args = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows only")

    want = json.loads((REPO / "test" / "assets" / "manifest.json").read_text())
    row = next(f for f in want["fixtures"] if f["filename"] == FIXTURE)
    # TWO acceptable answers, normalised, and the reason is a rule this repo
    # already carries: ASR fidelity is a different axis from the thing under
    # test. `transcript` is the ground truth and `asr_observed_parakeet` is
    # what the engine returned on 2026-08-16. The first run of this probe
    # compared EXACTLY against the second and reported a failure for a decode
    # that was more accurate than the record -- today's engine drops the
    # spurious "a" the manifest preserves.
    expected = [row["transcript"], row["asr_observed_parakeet"]]

    saved = paste_mod.get_clipboard()
    print(f"saved the operator's clipboard ({len(saved or '')} chars)")
    state = REPO / "TMP" / f"probe-dictation-{os.getpid()}"
    proc, base = boot_daemon(state)
    print(f"scratch daemon on {base}, pid {proc.pid}")
    try:
        text, took = run_one(base, "clipboard_only")
        matched = next((n for n, e in enumerate(expected)
                        if _norm(text) == _norm(e)), None)
        which = ("ground truth" if matched == 0 else
                 "the 2026-08-16 record" if matched == 1 else "NEITHER")
        print(f"\nclipboard_only: {text!r}")
        print(f"ground truth:   {expected[0]!r}")
        print(f"VERDICT clipboard carries the transcript: "
              f"{matched is not None}  ({took:.1f} s, matched {which})")
        ok = matched is not None
        if args.paste:
            pasted = with_notepad(base)
            landed = any(_norm(e) in _norm(pasted) for e in expected)
            print(f"\nnotepad read back: {pasted!r}")
            print(f"VERDICT the chord reached a real window: {landed}")
            ok = ok and landed
        return 0 if ok else 1
    finally:
        proc.kill()                    # by the PID we recorded, never by name
        proc.wait(timeout=15)
        if saved:
            paste_mod.set_clipboard(saved)
            print("the operator's clipboard has been restored")


if __name__ == "__main__":
    sys.exit(main())

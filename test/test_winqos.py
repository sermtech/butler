"""Power-throttling opt-out for Butler's CPU-heavy voice processes.

Windows 11 moves a background process's ONNX threads onto the E-cores after a
few seconds of sustained work (kokoro synthesis 4.3x -> 0.8x realtime, measured
2026-10-09; docs/bench-tts.md section 5). `engines/winqos.py` opts a process out;
these tests read the state BACK from the OS (GetProcessInformation) rather than
trusting a return value, and invoke every call site: the daemon, the Smart Turn
pool child, the kokoro sidecar, and every kokoro worker on start AND respawn.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENGINES = REPO / "daemon" / "engines"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ENGINES))

import winqos  # noqa: E402

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows power throttling")


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


@win_only
def test_set_high_qos_reads_back_as_opted_out_on_a_throwaway_process():
    p = _sleeper()
    try:
        assert winqos.query_qos(p.pid) == (0, 0), "a fresh process must read as never opted in"
        assert winqos.set_high_qos(p.pid) is True
        control, state = winqos.query_qos(p.pid)
        assert control & winqos.EXECUTION_SPEED and not state & winqos.EXECUTION_SPEED
        assert winqos.is_high_qos(p.pid)
    finally:
        p.kill()


@win_only
def test_a_child_does_not_inherit_the_opt_out():
    """Why the kokoro workers need their own call: the setting is per process."""
    code = ("import sys, subprocess, time; sys.path.insert(0, %r); import winqos\n"
            "winqos.set_high_qos()\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(winqos.query_qos(c.pid), flush=True); c.kill()\n" % str(ENGINES))
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.stdout.strip() == "(0, 0)", out.stdout + out.stderr


@win_only
def test_the_cli_opts_out_another_pid_and_fails_loudly_on_a_dead_one():
    p = _sleeper()
    try:
        ok = subprocess.run([sys.executable, "-X", "utf8", str(ENGINES / "winqos.py"), str(p.pid)])
        assert ok.returncode == 0 and winqos.is_high_qos(p.pid)
    finally:
        p.kill()
        p.wait()
    nobody = 0x7FFFFFF0    # no such pid
    bad = subprocess.run([sys.executable, "-X", "utf8", str(ENGINES / "winqos.py"), str(nobody)],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "winqos:" in bad.stderr


def test_apply_is_loud_and_never_raises(monkeypatch, capsys):
    def boom(pid=None):
        raise OSError(5, "denied")
    monkeypatch.setattr(winqos, "set_high_qos", boom)
    assert winqos.apply("thing", 4242) is False
    err = capsys.readouterr().err
    assert "WARNING" in err and "thing" in err and "4242" in err and "denied" in err


def test_off_windows_is_a_no_op(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert winqos.set_high_qos() is False and winqos.query_qos() is None
    assert winqos.apply("x") is False


def test_butlerd_main_opts_the_daemon_out_before_anything_else(monkeypatch):
    from daemon import butlerd
    seen = []
    monkeypatch.setattr(winqos, "apply", lambda label, pid=None: seen.append((label, pid)) or True)
    monkeypatch.setattr(sys, "argv", ["butlerd"])

    def stop():
        raise butlerd.paths_mod.DataRootError("stop here")
    monkeypatch.setattr(butlerd.paths_mod, "resolve", stop)
    with pytest.raises(SystemExit):
        butlerd.main()
    assert seen == [("butlerd", None)]


def test_the_smart_turn_pool_child_initialiser_opts_itself_out(monkeypatch):
    import turn_engine
    seen = []
    monkeypatch.setattr(winqos, "apply", lambda label, pid=None: seen.append(label) or True)
    monkeypatch.setattr(turn_engine, "TurnDetector", lambda: object())
    turn_engine._init_worker()
    assert seen == ["smart-turn pool child"]


@win_only
@pytest.mark.slow
def test_the_real_smart_turn_pool_child_is_opted_out():
    import turn_engine
    if not turn_engine.MODEL_PATH.exists():
        pytest.skip("smart-turn model not installed")
    # A fresh interpreter, so this pytest process's module-level _POOL is untouched.
    code = (
        "import sys, os; sys.path.insert(0, %r); import turn_engine, winqos\n"
        "if __name__ == '__main__':\n"
        "    pool = turn_engine.get_pool()\n"
        "    pid = pool.submit(os.getpid).result(timeout=120)\n"
        "    print('OPTED', winqos.is_high_qos(pid), pid != os.getpid())\n"
        "    pool.shutdown()\n" % str(ENGINES))
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert "OPTED True True" in out.stdout, out.stdout + out.stderr


@win_only
def test_every_kokoro_worker_is_opted_out_on_start_and_on_respawn():
    node = shutil.which("node")
    assert node, "node is not on PATH"
    p = subprocess.Popen([node, str(REPO / "test" / "qos_probe.mjs")], cwd=REPO,
                         env=dict(os.environ, BUTLER_PYTHON=sys.executable),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    pids: list[int] = []
    timer = threading.Timer(120, p.kill)
    timer.start()
    try:
        for line in p.stdout:
            if line.startswith("PID "):
                pids.append(int(line.split()[1]))
            if len(pids) == 2:
                break
        assert len(pids) == 2, f"probe printed only {pids}"
        assert pids[0] != pids[1], "respawn must be a NEW process"
        # The probe awaited the opt-out of each pid before printing it, so a
        # printed pid means the CLI exited 0 for it (first worker is dead by now).
        assert winqos.is_high_qos(pids[1]), "the RESPAWNED worker is not opted out"
    finally:
        timer.cancel()
        p.stdin.close()
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()


@win_only
@pytest.mark.slow
def test_the_real_sidecar_and_its_worker_are_opted_out():
    from daemon.adapters import tts_kokoro
    from test_child_lifetime import _snapshot, _tree
    sc = tts_kokoro._Sidecar("af_heart", shutil.which("node") or "node", None)
    try:
        sc.start(tts_kokoro.BOOT_TIMEOUT_S)
        assert winqos.is_high_qos(sc.proc.pid), "the sidecar itself is not opted out"
        # The pool forks its worker lazily, on the first request; /voices awaits it.
        urllib.request.urlopen(f"http://127.0.0.1:{sc.port}/voices", timeout=120).read()
        deadline = time.time() + 60
        workers = []
        while time.time() < deadline and not workers:
            workers = [p for p in _tree(sc.proc.pid, _snapshot()) if p.pid != sc.proc.pid]
            time.sleep(0.5)
        assert workers, "the sidecar forked no kokoro worker"
        time.sleep(3)   # the CLI runs asynchronously after the fork
        assert all(winqos.is_high_qos(w.pid) for w in workers), \
            f"workers not opted out: {[(w.pid, w.image) for w in workers]}"
    finally:
        sc.stop()

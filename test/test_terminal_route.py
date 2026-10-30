"""The terminal route: a caller-named path, a CLOSED agent table, a child
outside the job.

`docs/shortcuts.md` D4 and 4.6, AC-136, AC-141, AC-119, AC-120.
`POST /api/terminal` opens a console running one of three coding agents where
the caller points. The daemon spawns it rather than the desk agent, and for a
mechanical reason rather than a tidy one: `daemon/childproc.py` puts every
child of the desk agent in a kill-on-close Job Object with no
`JOB_OBJECT_LIMIT_BREAKAWAY_OK`, so `CREATE_BREAKAWAY_FROM_JOB` from inside it
fails with ERROR_ACCESS_DENIED and a terminal spawned there would die with the
daemon. The daemon itself is in NO job, so a child it spawns without
`childproc.assign` naturally outlives it -- which is AC-119's whole mechanism.

**THIS FILE ASSERTED THE OPPOSITE OF ITS FIRST CLAIM UNTIL 2026-09-09.** It
said "THE CALLER NAMES NO DIRECTORY", and it was right: `terminal_cwd` was a
setting, the route ignored its body, and a test with teeth sent the RIGHT
directory to catch a route that had started reading one. The owner then asked
for a pane of project rows with a chord each, which a route that cannot be told
which row was pressed cannot serve, so the property is REVERSED
(`docs/shortcuts.md` D4). The cost is one line and is not re-litigated here: an
unauthenticated tailnet request can once again choose where a full-capability
coding agent starts. The tests are rewritten rather than deleted, so the
inversion is legible.

Four claims are what this file exists for now.

**A directory in the body is HONOURED, and the row with teeth is the one that
sends a directory the settings do NOT name** -- because a route that had gone
back to ignoring the body would pass a test whose fixture happened to agree.

**THE AGENT IS A KEY, NEVER A COMMAND.** `agent` is resolved through the closed
`shortcuts.TERMINAL_AGENTS` table, so nothing from the request is ever
interpolated into a command line. That is the whole difference between this
route and a remote shell, and an unknown name is a 400 rather than a spawn.

**AC-120 -- a path cmd would re-parse is refused by name.** The spawn goes
through `cmd` on `wt.exe`'s own command line, and `harness_pi` already paid for
this rule with a model value of `claude" & echo PWNED & rem ` that ran a second
command. The refusal is the SAME helper, not a second copy. It is load-bearing
rather than defence in depth, and it now guards a path that came off the WIRE
rather than out of a setting -- which makes it the only thing between a request
and a second command.

**Nothing is spawned on any refusal.** Every rejection is asserted on the
spawn count as well as on the status: a route that answered 400 and opened the
terminal anyway would satisfy every status check in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import QuietTts                                    # noqa: E402
from daemon import butlerd, hub as hub_mod, ports                # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                     # noqa: E402
from daemon.adapters.brain_fake import FakeBrain                 # noqa: E402
from daemon.modes import ModeStore                               # noqa: E402
from daemon import shortcuts as shortcuts_mod                    # noqa: E402
from daemon.settings import BY_KEY, Settings                     # noqa: E402

PROJECT = r"E:\PROJECTS\butler"
ELSEWHERE = r"E:\PROJECTS\somewhere-nobody-configured"


class Spawns:
    """Every terminal the route asked for: the directory AND the agent.

    Both, because "a terminal opened" is not the claim any more -- AC-136 is
    that the RIGHT agent opened in the RIGHT directory, and a double that
    recorded only the path would pass a route that ran pi for every row.
    """

    def __init__(self, boom: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.boom = boom

    def __call__(self, cwd: str, agent: str = "pi") -> int:
        self.calls.append((cwd, agent))
        if self.boom is not None:
            raise self.boom
        return 4242

    @property
    def cwds(self) -> list[str]:
        return [cwd for cwd, _agent in self.calls]


def make_hub(tmp_path, **values):
    """A Hub on a settings store of its own.

    Its own `Settings` because the suite must never write the owner's
    `state/settings.json` (test/AGENTS.md).
    """
    settings = Settings(tmp_path / "settings.json")
    for key, value in values.items():
        settings.set(key, value)
    return hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=FakeBrain(),
                       modes=ModeStore(tmp_path / "mode.json"),
                       asr_config=ports.AsrConfig(model="test"),
                       settings=settings)


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    """The real app on a hub whose FALLBACK directory is this project.

    `harness_cwd` rather than `terminal_cwd`, which is retired (AC-144): the
    route's own fallback ladder is now `harness_cwd` then the TMP scratch dir,
    and it is reached only by a caller that named no directory.
    """
    hub = make_hub(tmp_path, harness_cwd=PROJECT)
    spawns = Spawns()
    monkeypatch.setattr(butlerd, "spawn_terminal", spawns)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    return hub, spawns


async def post_terminal(body=None) -> tuple[int, dict]:
    """POST at the real route and answer `(status, payload)`.

    The body is read BEFORE the client closes, for `test_dictate.py`'s reason:
    reading it after the `finally` raises `ClientConnectionError`, which reads
    like a daemon fault and is a fault in the harness.
    """
    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await (client.post("/api/terminal") if body is None
                        else client.post("/api/terminal", json=body))
        return answer.status, await answer.json()
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# AC-136 -- the caller names the directory and the agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_body_decides_where_the_terminal_opens(terminal):
    """AC-136, and this is the REVERSAL (`docs/shortcuts.md` D4).

    The row with teeth: `ELSEWHERE` is a directory the fixture's settings do
    NOT name, so a route that had gone back to ignoring the body would fail
    here rather than pass by coincidence -- which is the mirror image of how
    the test this replaced was aimed.
    """
    _, spawns = terminal

    status, payload = await post_terminal(
        {"cwd": str(Path(__file__).resolve().parent), "agent": "claude"})

    here = str(Path(__file__).resolve().parent)
    assert status == 200, payload
    assert spawns.calls == [(here, "claude")], (
        f"the route did not open the caller's agent in the caller's "
        f"directory: {spawns.calls}")
    assert payload == {"cwd": here, "agent": "claude"}, (
        f"the answer names the directory and the agent and nothing else: "
        f"{payload}. There is deliberately no pid -- `wt.exe` hands off to "
        f"Windows Terminal, which owns the shell, so any pid here would be the "
        f"launcher's and is unreliable (measured exiting inside 0.5 s in one "
        f"run and alive at 1 s in another).")


@pytest.mark.parametrize("agent", sorted(shortcuts_mod.TERMINAL_AGENTS))
@pytest.mark.asyncio
async def test_every_agent_in_the_table_can_be_started(terminal, agent):
    """All three, because a table with an entry nothing can start is a button
    on the page that reports success and opens a window saying "not
    recognized"."""
    _, spawns = terminal

    status, payload = await post_terminal({"agent": agent})

    assert status == 200, payload
    assert spawns.calls == [(PROJECT, agent)], spawns.calls


@pytest.mark.parametrize("agent", ["bash", "pi.cmd", "", "PI",
                                  "pi && echo PWNED"])
@pytest.mark.asyncio
async def test_an_agent_outside_the_CLOSED_TABLE_is_refused(terminal, agent):
    """AC-141, and it is the whole difference between this route and a shell.

    `agent` is a KEY of `shortcuts.TERMINAL_AGENTS`, resolved to a command by
    `spawn_terminal`. If the request could name the command, an unauthenticated
    tailnet route would run anything -- so an unknown name is a 400 and nothing
    is spawned. `""` is the one exception and it means "use the default", which
    is why it is in this list: the row proves the blank does NOT fall through
    to something arbitrary.
    """
    _, spawns = terminal

    status, payload = await post_terminal({"agent": agent})

    if agent == "":
        assert status == 200, payload
        assert spawns.calls == [(PROJECT, shortcuts_mod.DEFAULT_AGENT)]
        return
    assert status == 400, f"{agent!r} was accepted: {payload}"
    assert spawns.calls == [], f"{agent!r} reached a spawn: {spawns.calls}"


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_falls_back_to_the_settings(terminal):
    """Every field is optional, so a malformed body is not an error.

    The desk agent always sends one, but `probe_terminal.py` and a curl do not,
    and refusing here would break the caller that asks for "a terminal,
    wherever you would put one".
    """
    from aiohttp.test_utils import TestClient, TestServer

    _, spawns = terminal
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await client.post("/api/terminal", data=b"not json at all")
        assert answer.status == 200
    finally:
        await client.close()
    assert spawns.calls == [(PROJECT, shortcuts_mod.DEFAULT_AGENT)]


# --------------------------------------------------------------------------- #
# where the directory comes from when the caller named none
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_caller_that_names_no_directory_gets_the_working_one(
        tmp_path, monkeypatch):
    """The tail of the ladder `harness_spec_resolver` uses, in the same order.

    `terminal_cwd` used to head it and is retired (AC-144), so what is left is
    "wherever the agent works", then the scratch dir. A second, different
    fallback here would be a place the owner never chose.
    """
    hub = make_hub(tmp_path, harness_cwd=PROJECT)
    spawns = Spawns()
    monkeypatch.setattr(butlerd, "spawn_terminal", spawns)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, payload = await post_terminal()

    assert status == 200, payload
    assert spawns.cwds == [PROJECT]


@pytest.mark.asyncio
async def test_both_blank_is_the_scratch_dir_and_never_a_refusal(tmp_path,
                                                                 monkeypatch):
    """AC-31 as amended 2026-08-18: there is always somewhere harmless.

    A fresh clone configures nothing, and a hotkey that answered an error on a
    machine where it had never been set up would read as broken. It opens in
    Butler's own TMP, which is not a guess at a named project.
    """
    hub = make_hub(tmp_path)
    spawns = Spawns()
    monkeypatch.setattr(butlerd, "spawn_terminal", spawns)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, payload = await post_terminal()

    assert status == 200, payload
    assert spawns.cwds and spawns.cwds[0].endswith("TMP"), (
        f"a daemon with nothing configured opened a terminal at "
        f"{spawns.calls}, which is neither the scratch dir nor a refusal")


def test_terminal_cwd_is_RETIRED_and_not_quietly_still_read():
    """AC-144, asserted from both ends.

    The setting is gone from the schema, replaced by a row in the shortcuts
    store. Asserted because a retirement that leaves the key readable is the
    worst version of this change: the page would stop offering the control
    while the route went on obeying whatever was last written to the file, and
    nothing would say which of the two sources had won.

    `projects` is retired the same way and in the same place, by the owner's
    backlog item 4 (2026-09-11): the pane's ROWS are the one project list, so a
    second list of directories in the schema is the drift this file already
    refuses once. Asserted here beside `terminal_cwd` because the two failures
    are identical -- a key left readable while the page stops offering it.
    """
    assert "terminal_cwd" not in BY_KEY, (
        "terminal_cwd is still in the schema, so there are two places that say "
        "where a terminal opens and no rule about which wins")
    assert "projects" not in BY_KEY, (
        "the projects setting is still in the schema, so there are two lists "
        "of directories again and no rule about which the gates read")
    assert "projects" not in butlerd.LOCAL_TARGETS, (
        "the retired setting still has a local target, so the pane's path "
        "field offers a list nothing writes")


def test_the_migration_turns_an_old_setting_into_a_ROW(tmp_path):
    """AC-144's other half, and the `migrated` flag is the part with teeth.

    Read from the settings FILE, because the setting is gone from the schema and
    `Settings._load` drops a key it no longer knows -- which is the whole reason
    this is not a `settings.get`. And it runs ONCE: without the flag, deleting
    every row would bring the old directory back on the next restart, a row the
    owner explicitly removed reappearing, which is worse than never migrating.
    """
    import json

    (tmp_path / "settings.json").write_text(
        json.dumps({"terminal_cwd": PROJECT}), encoding="utf-8")

    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    assert store.migrate_terminal_cwd(tmp_path / "settings.json") == PROJECT
    rows = store.all()
    assert len(rows) == 1, f"the setting did not become a row: {rows}"
    assert rows[0].path == PROJECT
    assert rows[0].chords == {
        shortcuts_mod.DEFAULT_AGENT: shortcuts_mod.MIGRATED_TERMINAL_CHORD}, (
        f"the migrated row does not carry the chord the hotkey used to have, "
        f"so the owner's key silently stopped working: {rows[0].chords}")

    store.remove(rows[0].id)
    again = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    assert again.migrate_terminal_cwd(tmp_path / "settings.json") == ""
    assert again.all() == [], (
        "a row the owner deleted came back on the next boot")


def test_a_daemon_with_no_rows_reaches_no_project_at_all(tmp_path):
    """The empty list is an ANSWER and never a crash.

    It used to be asserted through `LOCAL_TARGETS["projects"]`, which went with
    the setting (backlog item 4). What survives is the property that mattered:
    a Hub built with no shortcuts store -- every Hub in a test that does not
    know shortcuts exist -- has no projects, so the spoken switch refuses
    everything rather than raising on a None.
    """
    hub = make_hub(tmp_path)

    assert hub.shortcuts is None
    assert hub.project_paths() == ()


# --------------------------------------------------------------------------- #
# AC-120 -- what cmd would re-parse
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ['E:\\P & echo PWNED', 'E:\\P|whoami',
                                 'E:\\P%TEMP%', 'E:\\P^x', 'E:\\P"x',
                                 'E:\\P>out', 'E:\\P<in'])
@pytest.mark.asyncio
async def test_a_path_cmd_would_re_parse_is_refused(terminal, bad):
    """AC-120, over the whole of `harness_pi.CMD_METACHARACTERS`.

    It now guards a path that came off the WIRE rather than out of a setting,
    which makes it the ONLY thing between an unauthenticated request and a
    second command running out of a folder name. `harness_pi` paid for this
    rule with a model value of `claude" & echo PWNED & rem ` that did exactly
    that.

    Driven through the BODY, because that is where the path arrives now. A
    setting can still carry one -- `harness_cwd` is a path the owner types --
    and the same helper covers both, which is why there is one helper.
    """
    _, spawns = terminal

    status, payload = await post_terminal({"cwd": bad})

    assert status == 400, payload
    assert spawns.calls == [], f"{bad!r} reached a spawn"
    assert "cmd" in payload.get("error", ""), (
        f"the refusal does not say why: {payload}")


@pytest.mark.parametrize("bad", ['E:\\P & echo PWNED', 'E:\\P|whoami',
                                 'E:\\P%TEMP%'])
@pytest.mark.asyncio
async def test_a_SETTING_cmd_would_re_parse_is_refused_too(tmp_path,
                                                           monkeypatch, bad):
    """The fallback path is checked by the same helper, on the same route.

    `harness_cwd` is a path the owner TYPES, and a caller that names no
    directory reaches it. One gate covering both sources rather than two that
    can disagree.
    """
    hub = make_hub(tmp_path, harness_cwd=bad)
    spawns = Spawns()
    monkeypatch.setattr(butlerd, "spawn_terminal", spawns)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, payload = await post_terminal()

    assert status == 400, payload
    assert spawns.calls == [], f"{bad!r} reached a spawn"


@pytest.mark.asyncio
async def test_a_directory_that_is_not_there_is_refused(terminal, tmp_path):
    """The check that had to be ADDED when the spawn changed (AC-7b).

    `Popen(cwd=missing)` raised, so the old spawn got a 502 for free. `wt.exe
    -d missing` does NOT fail -- it opens a window and complains inside it --
    so without this check the route would answer 200, the desk agent would
    play the success cue, and the owner would hear "done" for a terminal that
    never opened. That is the shape of every defect AC-7b is named for.

    Reachable far more easily now that the path comes off the wire: a row whose
    directory the owner has since renamed is an ordinary Tuesday.
    """
    _, spawns = terminal

    status, payload = await post_terminal(
        {"cwd": str(tmp_path / "not-a-directory")})

    assert status == 400, payload
    assert spawns.calls == [], "a terminal was opened in a missing directory"
    assert "not a directory" in payload.get("error", "")


# --------------------------------------------------------------------------- #
# the spawn itself
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_spawn_that_fails_is_a_502_and_not_an_empty_200(terminal,
                                                                monkeypatch):
    """Loud and never fatal (D-1), and never a success the owner cannot see.

    The desk agent cues on the status alone, so a failure that answered 200
    would beep "done" with no window anywhere. A directory that does not exist
    arrives here too: `Popen` raises when it cannot enter one.
    """
    boom = Spawns(boom=OSError("the console host is missing"))
    monkeypatch.setattr(butlerd, "spawn_terminal", boom)

    status, payload = await post_terminal()

    assert status == 502, payload
    assert "pid" not in payload
    assert "console host" in payload.get("error", ""), (
        f"the reason the spawn failed did not reach the caller: {payload}")


def test_the_terminal_is_deliberately_NOT_put_in_the_job_object(monkeypatch,
                                                                tmp_path):
    """AC-119's mechanism, asserted at the one line that would break it.

    Every other child in this repository calls `childproc.assign` immediately
    after spawning, and `daemon/childproc.py` exists to make that the default.
    The next reader's correct instinct here is therefore the wrong one: a
    terminal the owner is typing in must survive `POST /api/restart`, which
    lands between turns and would close their session mid-command.

    Asserted on the real `spawn_terminal` with only `Popen` replaced, so the
    thing under test is the function that ships rather than a description of
    it.
    """
    from daemon import childproc

    assigned: list[object] = []
    monkeypatch.setattr(childproc, "assign", lambda p: assigned.append(p))

    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.kwargs = kwargs
            self.pid = 31337

    monkeypatch.setattr(butlerd.subprocess, "Popen", FakePopen)

    butlerd.spawn_terminal(str(tmp_path))

    assert assigned == [], (
        "the terminal was assigned to the kill-on-close Job Object, so the "
        "owner's session closes the moment the daemon restarts (AC-119)")


def test_the_spawn_hands_the_directory_to_windows_terminal(monkeypatch,
                                                           tmp_path):
    """AC-118's other half: what is actually run, and where.

    `cmd /k pi` with `CREATE_NEW_CONSOLE` shipped for one commit and produced
    the owner's bug report -- "a ghost frame terminal, plus another cmd".
    MEASURED 2026-09-09 by spawning from the LIVE daemon and enumerating
    processes and visible windows:

      * `CREATE_NEW_CONSOLE` -> `cmd.exe` plus a `conhost.exe` of its own, a
        Terminal window titled generically 'Terminal', and NO `node.exe`
        anywhere 16 s later, which means pi never started at all.
      * `wt.exe -d <cwd> cmd /k pi` -> `cmd.exe` parented to
        `WindowsTerminal.exe` with `node.exe` running under it, one window,
        titled by pi itself, and no conhost of ours.

    The directory is a `{cwd}` slot in `TERMINAL_ARGV` rather than a `cwd=`
    keyword, so it lands on wt's command line -- which is why AC-120's refusal
    went back to being load-bearing rather than defence in depth.

    The agent is a `{agent}` slot filled from the CLOSED table, and the last
    assertion here is what makes AC-141 a property of the code rather than of a
    docstring: the command that reaches the argv is the table's VALUE, not the
    name the caller sent.
    """
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = list(argv)
            seen["kwargs"] = kwargs
            self.pid = 5

    monkeypatch.setattr(butlerd.subprocess, "Popen", FakePopen)

    butlerd.spawn_terminal(str(tmp_path))

    assert seen["argv"] == ["wt.exe", "-d", str(tmp_path), "cmd", "/k", "pi"], (
        f"the terminal is not opened through Windows Terminal in the chosen "
        f"directory: {seen['argv']}")
    assert "creationflags" not in seen["kwargs"], (
        "wt.exe is a GUI launcher and needs no console flags; CREATE_NEW_"
        "CONSOLE is what produced the extra conhost the owner reported")
    assert not hasattr(butlerd, "CREATE_NEW_CONSOLE"), (
        "the constant outlived the spawn that used it, which is the stale "
        "leftover D-12 is about")

    butlerd.spawn_terminal(str(tmp_path), "opencode")
    assert (tuple(seen["argv"][-1:])
            == shortcuts_mod.TERMINAL_AGENTS["opencode"]), (
        f"the command on the line is not the closed table's value for the "
        f"agent that was asked for: {seen['argv']}")

    with pytest.raises(KeyError):
        # The last line of defence, and it is a KeyError rather than a check:
        # `TERMINAL_AGENTS[agent]` cannot resolve a name that is not in the
        # table, so even a caller that got past the route's own 400 could not
        # put a command on this line (AC-141).
        butlerd.spawn_terminal(str(tmp_path), "bash")


def test_claude_carries_the_skip_permissions_flag_as_SEPARATE_ARGV(monkeypatch,
                                                                  tmp_path):
    """AC-150, and the argv SHAPE is the half with teeth.

    The owner's ruling of 2026-09-09: every claude started from this table runs
    with `--dangerously-skip-permissions`. The flag lives in the daemon's closed
    table, so no request can add, remove or forge one -- that is the AC-141 half
    and it is asserted by the KeyError above.

    This asserts the mechanical half. `subprocess` QUOTES any list element
    containing a space, so a table value of `"claude --dangerously-skip-
    permissions"` as one element would reach `cmd` as a single quoted token and
    cmd would look for a program with that literal name. The table holds an argv
    TUPLE and `spawn_terminal` SPLICES it, which is why this checks two elements
    rather than a substring.
    """
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = list(argv)
            self.pid = 7

    monkeypatch.setattr(butlerd.subprocess, "Popen", FakePopen)

    butlerd.spawn_terminal(str(tmp_path), "claude")

    assert seen["argv"] == ["wt.exe", "-d", str(tmp_path), "cmd", "/k",
                            "claude", "--dangerously-skip-permissions"], (
        f"claude is not started with the flag as its own argv element: "
        f"{seen['argv']}")
    assert not any(" " in part for part in seen["argv"][4:]), (
        f"a command element carries a space, so subprocess will quote it and "
        f"cmd will look for a program with that literal name: {seen['argv']}")


def test_only_claude_gets_a_flag():
    """A flag nobody asked for is the thing discovered three months later.

    The owner asked for claude and said nothing about the other two, so pi and
    opencode are bare program names -- asserted, because "add the same flag
    everywhere" is the tidy-looking edit that would make this untrue.
    """
    assert shortcuts_mod.TERMINAL_AGENTS["pi"] == ("pi",)
    assert shortcuts_mod.TERMINAL_AGENTS["opencode"] == ("opencode",)
    assert "--dangerously-skip-permissions" in \
        shortcuts_mod.TERMINAL_AGENTS["claude"]


def test_a_template_with_no_agent_slot_is_left_ALONE(monkeypatch, tmp_path):
    """Why the splice matches an element rather than appending to the end.

    `test_desk.py`'s AC-119 host and `probe_terminal.py` both override
    `TERMINAL_ARGV` wholesale with their own trailing command and neither has an
    `{agent}` slot -- starting a real coding agent in this repository is exactly
    what those two exist to avoid. Appending the agent instead of splicing would
    have run it anyway, in a test whose whole point is a process lifetime.
    """
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = list(argv)
            self.pid = 8

    monkeypatch.setattr(butlerd.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(butlerd, "TERMINAL_ARGV",
                        ("wt.exe", "-d", "{cwd}", "cmd", "/k",
                         "title MARKER & ping -n 60 127.0.0.1"))

    butlerd.spawn_terminal(str(tmp_path), "claude")

    assert seen["argv"] == ["wt.exe", "-d", str(tmp_path), "cmd", "/k",
                            "title MARKER & ping -n 60 127.0.0.1"], (
        f"an override with no {{agent}} slot had a command appended to it: "
        f"{seen['argv']}")


def test_the_agents_are_served_in_the_order_the_OWNER_named_them():
    """AC-151, and it is a decision carried by dict order.

    `sorted()` rendered claude, opencode, pi. The owner named them claude, pi,
    opencode. `settings.GROUPS` sets the precedent and `daemon/AGENTS.md`
    records the rule: dict ORDER is what the page renders, so the page sorts
    nothing and names nothing.
    """
    assert list(shortcuts_mod.TERMINAL_AGENTS) == ["claude", "pi", "opencode"]

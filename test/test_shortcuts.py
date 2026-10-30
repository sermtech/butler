"""The shortcuts store and its routes. `docs/shortcuts.md`, AC-133..AC-144.

The pane the owner actually reaches for: a row per project, a chord per coding
agent, one dictation chord, and whether the fold is open. It replaced three
mechanisms at once -- the `terminal_cwd` setting, the hardcoded
`Ctrl + numpad *`, and the "starred row" idea -- so this file's job is to prove
there is exactly ONE table and that everything about it is the owner's.

Written in `test_connections.py`'s style, and for the same reason: this is
`connections.py`'s shape, down to the frozen dataclass validating a HAND-EDITED
file. Every refusal is confirmed by an OBSERVABLE CONSEQUENCE and not by a
status code -- a row missing from the list, a chord unchanged in the store, a
binding absent from the wire -- because a route that answered 400 and wrote
anyway satisfies every status check that could be written here.

FIVE CLAIMS.

**A row can be MODIFIED.** `kind="list"` cannot express that, which is the
whole argument for a store rather than a setting: an upsert with a known id must
change that row and not add a second.

**A bare key that is not bare-safe is refused BY NAME.** The one refusal here
whose failure is not recoverable from the page: with `e` suppressed
system-wide, the owner cannot type into the pane that would undo it.

**One chord does one thing.** Refused at the route so the owner reads a
sentence, and again in `Table.__init__` because this file is hand-editable.

**The pane's own state is in the STORE.** `web/` names no setting, so there is
nothing for it to name.

**A bad hand-edited row costs ONE ROW, not the daemon.** The deliberate
improvement on `connections.py`, which raises out of its constructor and
therefore refuses to boot -- and a daemon that will not start leaves no
microphone at all (D-1).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import QuietTts                                    # noqa: E402
from daemon import butlerd, hub as hub_mod, ports                # noqa: E402
from daemon import shortcuts as shortcuts_mod                    # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                     # noqa: E402
from daemon.adapters.brain_fake import FakeBrain                 # noqa: E402
from daemon.modes import ModeStore                               # noqa: E402
from daemon.settings import Settings                             # noqa: E402
from desk import actions                                         # noqa: E402

# Real folders, because the route refuses a path that is not one (AC-754).
PROJECT = str(Path(__file__).resolve().parents[1] / "daemon")
OTHER = str(Path(__file__).resolve().parents[1] / "web")


def make_store(tmp_path) -> shortcuts_mod.ShortcutStore:
    return shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")


@pytest.fixture
def served(tmp_path, monkeypatch):
    """The real app on a hub holding a shortcuts store of its own.

    Its own store because the suite must never write the owner's `state/`
    (test/AGENTS.md) -- and this store is the one that decides which keys are
    suppressed on their machine.
    """
    store = make_store(tmp_path)
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=FakeBrain(),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=Settings(tmp_path / "settings.json"),
                      shortcuts=store)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    return store


async def call(method: str, path: str, body=None) -> tuple[int, dict]:
    """Drive the real route and answer `(status, payload)`.

    The body is read BEFORE the client closes, for `test_dictate.py`'s reason:
    reading it after the `finally` raises `ClientConnectionError`, which reads
    like a daemon fault and is a fault in the harness.
    """
    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await client.request(
            method, path, **({} if body is None else {"json": body}))
        payload = {} if answer.status == 204 else await answer.json()
        return answer.status, payload
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# AC-134 -- rows are the owner's to edit
# --------------------------------------------------------------------------- #
def test_a_row_survives_the_file(tmp_path):
    """The store is a FILE, and a store that loses its rows on restart is a
    binding table the owner rebuilds every morning."""
    store = make_store(tmp_path)
    row = store.upsert(sid="", path=PROJECT, label="butler",
                       chords={"pi": "ctrl+numpad_multiply"})

    again = make_store(tmp_path)

    assert [r.public() for r in again.all()] == [row.public()], (
        f"the row did not come back: {[r.public() for r in again.all()]}")


def test_an_upsert_MODIFIES_the_row_with_that_id(tmp_path):
    """The whole argument for a store rather than a `kind="list"` setting.

    A flat list of strings written whole cannot express "change this row's
    label and leave its chords alone", and a list that can only grow is a trap
    whose only exit is hand-editing a file the owner was never told about.
    """
    store = make_store(tmp_path)
    first = store.upsert(sid="", path=PROJECT, label="butler",
                         chords={"pi": "ctrl+numpad_multiply"})

    store.upsert(sid=first.id, path=OTHER, label="crawler",
                 chords={"claude": "ctrl+f13"})

    rows = store.all()
    assert len(rows) == 1, f"the upsert added a second row: {rows}"
    assert rows[0].path == OTHER and rows[0].label == "crawler"
    assert rows[0].chords == {"claude": "ctrl+f13"}, (
        f"the old chord survived a modification that did not name it: "
        f"{rows[0].chords}. A row is replaced whole, so the pane must send "
        f"every chord it wants kept -- which it does, because it renders from "
        f"the answer this route gives back.")


@pytest.mark.asyncio
async def test_a_row_can_be_removed_again_through_the_route(served):
    """Confirmed by the row DISAPPEARING FROM THE LIST rather than by a 200."""
    status, payload = await call("POST", "/api/shortcuts",
                                {"row": {"path": PROJECT, "label": "butler"}})
    assert status == 200, payload
    sid = payload["rows"][0]["id"]

    status, payload = await call("DELETE", f"/api/shortcuts/{sid}")

    assert status == 200, payload
    assert payload["rows"] == [], (
        f"the row survived its deletion: {payload['rows']}")


@pytest.mark.asyncio
async def test_deleting_a_row_that_is_not_there_is_a_404(served):
    """`connections.py`'s error table: a policy refusal is 400 and an unknown
    id is 404, because "you may not" and "there is no such thing" are different
    facts and the pane draws them differently."""
    status, payload = await call("DELETE", "/api/shortcuts/s-nope")

    assert status == 404, payload


@pytest.mark.asyncio
async def test_the_root_row_survives_the_route_too(served, tmp_path):
    """R10 through the HTTP surface, not just the store: `DELETE
    /api/shortcuts/{sid}` and `POST /api/shortcuts` both map `ShortcutError`
    to 400 already, so the store's refusal is the whole fix -- this proves it
    reaches the wire rather than only the class under test.
    """
    served.migrate_projects(tmp_path / "settings.json", tmp_path)
    root_id = served.root_id()
    assert root_id, "the fixture's store did not mint a root row"

    status, payload = await call("DELETE", f"/api/shortcuts/{root_id}")
    assert status == 400, payload
    assert any(r.id == root_id for r in served.all()), (
        "the root row is gone after a refused delete")

    (tmp_path / "elsewhere").mkdir()
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"id": root_id, "path": str(tmp_path / "elsewhere"),
                 "label": "root"}})
    assert status == 400, payload
    assert served.get(root_id).path == str(tmp_path), (
        "the root row's directory moved through the route")


@pytest.mark.asyncio
async def test_a_row_with_no_path_is_refused(served):
    """A row that names no directory is a button that cannot open anything."""
    status, payload = await call("POST", "/api/shortcuts",
                                {"row": {"label": "nowhere"}})

    assert status == 400, payload
    _s, after = await call("GET", "/api/shortcuts")
    assert after["rows"] == [], f"the empty row was stored anyway: {after}"


# --------------------------------------------------------------------------- #
# AC-138 -- what may be bound
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("chord, why", [
    ("e", "a bare letter"),
    ("return", "a bare Return"),
    ("numpad5", "a bare numpad digit"),
    ("ctrl", "a modifier as the key"),
    ("nonsense", "a key nobody has"),
])
@pytest.mark.asyncio
async def test_a_chord_that_must_not_be_bound_is_refused_BY_NAME(served, chord,
                                                                 why):
    """AC-138 at the route, so the owner reads a sentence at the click.

    NOT recoverable if it got through: with a bare letter suppressed
    system-wide the owner cannot type into the pane that would undo it. That is
    why the same refusal lives in `Table.__init__` as well
    (`test_desk_hotkeys.py`) -- one write to `/api/settings` must not be able to
    deny input on the owner's desk.
    """
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"path": PROJECT, "chords": {"pi": chord}}})

    assert status == 400, f"{why} was accepted: {payload}"
    assert chord in payload.get("error", ""), (
        f"{why} was refused without naming it: {payload}")
    _s, after = await call("GET", "/api/shortcuts")
    assert after["rows"] == [], (
        f"{why} was refused and the row was stored anyway: {after['rows']}")


@pytest.mark.asyncio
async def test_a_chord_captured_by_PRESSING_KEYS_is_resolved_by_the_daemon(
        served):
    """AC-135 and D3: one key table, in Python.

    The page sends a browser `KeyboardEvent`'s W3C `code` plus modifier
    booleans and never a key NAME it worked out for itself, because a second
    table in JavaScript drifts -- and the drifted copy is the one that binds a
    key the hook is not watching.
    """
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"path": PROJECT,
                 "chords": {"pi": {"code": "F13", "ctrl": True}}}})

    assert status == 200, payload
    assert payload["rows"][0]["chords"] == {"pi": "ctrl+f13"}, (
        f"the capture was not canonicalised: {payload['rows'][0]}")


@pytest.mark.asyncio
async def test_one_chord_does_ONE_thing(served):
    """Refused HERE so the refusal names what already holds the chord, which
    the hook cannot do -- it only knows the chord is bound twice."""
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"path": PROJECT, "label": "butler",
                 "chords": {"pi": "ctrl+f13"}}})
    assert status == 200, payload

    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"path": OTHER, "label": "crawler",
                 "chords": {"claude": "ctrl+f13"}}})

    assert status == 400, f"one chord was bound twice: {payload}"
    assert "butler" in payload.get("error", ""), (
        f"the refusal does not say what already holds the chord: {payload}")


@pytest.mark.asyncio
async def test_a_chord_that_already_DICTATES_cannot_open_a_terminal(served):
    """The dictation chord is in the same table, which is the point of
    collapsing three mechanisms into one."""
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"path": PROJECT,
                 "chords": {"pi": shortcuts_mod.DEFAULT_DICTATE}}})

    assert status == 400, payload
    assert "dictates" in payload.get("error", "")


def test_a_row_may_KEEP_its_own_chord_when_it_is_modified(tmp_path):
    """The collision check must not refuse a row's own chord back.

    Editing a label re-sends every chord the row already has, so a check that
    compared against every row including this one would make a row uneditable
    the moment it had a binding.
    """
    store = make_store(tmp_path)
    row = store.upsert(sid="", path=PROJECT, chords={"pi": "ctrl+f13"})

    store.upsert(sid=row.id, path=PROJECT, label="renamed",
                 chords={"pi": "ctrl+f13"})

    assert store.get(row.id).label == "renamed"


@pytest.mark.asyncio
async def test_an_agent_this_daemon_cannot_START_is_refused(served):
    """The closed terminal command table (AC-141). A row naming an agent that
    is not in it is a button that would report success and open a window saying
    "not recognized"."""
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"path": PROJECT, "chords": {"bash": "ctrl+f13"}}})

    assert status == 400, payload
    assert "bash" in payload.get("error", "")


# --------------------------------------------------------------------------- #
# AC-137 -- the dictation chord
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_dictation_chord_can_be_rebound_and_REACHES_THE_WIRE(served):
    """AC-137. A rebind that saves and changes nothing is AC-7b exactly.

    Confirmed on the BINDINGS the desk agent reads, not on the answer to this
    request: the store and the wire are two different projections and only one
    of them installs a hook.
    """
    status, payload = await call("POST", "/api/shortcuts",
                                {"dictate": {"code": "F14"}})

    assert status == 200, payload
    assert payload["dictate"] == "f14"
    assert {"chord": "f14", "action": "dictate"} in served.bindings(), (
        f"the rebind never reached the binding table: {served.bindings()}")


@pytest.mark.asyncio
async def test_the_dictation_chord_cannot_be_EMPTIED(served):
    """Unbinding it is the feature switched off by a control that does not say
    so; `hotkeys_enabled` is the honest way."""
    status, payload = await call("POST", "/api/shortcuts", {"dictate": ""})

    assert status == 400, payload
    _s, after = await call("GET", "/api/shortcuts")
    assert after["dictate"] == shortcuts_mod.DEFAULT_DICTATE


def test_the_shipped_dictation_chord_is_the_one_the_hotkey_always_had(tmp_path):
    """A fresh clone must behave the way the feature shipped.

    numpad `*` for `docs/desk-hotkeys.md` D2's reason: suppression stops a key
    producing its character system-wide, so the default has to be a key almost
    nothing needs.
    """
    assert make_store(tmp_path).dictate == "numpad_multiply"
    assert actions.KEYS["numpad_multiply"].bare_safe is True


# --------------------------------------------------------------------------- #
# AC-133 -- the pane's own state, and where it lives
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_panes_open_state_round_trips_through_the_STORE(served):
    """AC-133 and D1. It is not a setting, so `web/` has nothing to name.

    Confirmed by reading it back on a fresh GET rather than from the POST's own
    answer, because the claim is that it PERSISTS -- a fold that reopens closed
    on every page load is what the word "persistent" in the ask rules out.
    """
    status, payload = await call("POST", "/api/shortcuts", {"open": True})
    assert status == 200 and payload["open"] is True

    _s, after = await call("GET", "/api/shortcuts")
    assert after["open"] is True

    await call("POST", "/api/shortcuts", {"open": False})
    _s, closed = await call("GET", "/api/shortcuts")
    assert closed["open"] is False


@pytest.mark.asyncio
async def test_the_CHORD_DISCLOSURE_round_trips_and_binds_nothing(served):
    """AC-149's other half (`docs/shortcuts.md` D8, the owner's ruling).

    The agent buttons always launch; this only reveals a second control under
    each one. So the disclosure is a VIEW state and must change no binding --
    asserted on the WIRE the desk agent reads, because a view flag that reached
    the binding table would silently rebind the owner's keys from a checkbox.
    """
    before = served.bindings()

    status, payload = await call("POST", "/api/shortcuts",
                                {"show_chords": True})

    assert status == 200, payload
    assert payload["show_chords"] is True
    _s, after = await call("GET", "/api/shortcuts")
    assert after["show_chords"] is True, "the disclosure did not persist"
    assert served.bindings() == before, (
        "showing the chord slots changed the binding table, so a view control "
        "rebound the owner's keys")


@pytest.mark.asyncio
async def test_the_disclosure_ships_OFF(served):
    """The owner's stated usage is chords on a FEW rows.

    "unbound" printed three times on every row is noise nobody can act on, and
    the row already works without a chord -- that is the whole of the second ask.
    """
    _s, doc = await call("GET", "/api/shortcuts")
    assert doc["show_chords"] is False


@pytest.mark.asyncio
async def test_the_agents_reach_the_page_in_the_daemons_own_ORDER(served):
    """AC-151. Dict order carries the decision and the page sorts nothing."""
    _s, doc = await call("GET", "/api/shortcuts")
    assert doc["agents"] == list(shortcuts_mod.TERMINAL_AGENTS)
    assert doc["agents"] == ["claude", "pi", "opencode"], (
        f"the buttons will not read in the order the owner named them: "
        f"{doc['agents']}")


def test_an_agents_COMMAND_never_crosses_the_wire(tmp_path):
    """AC-141 and AC-150 together, asserted as a negative.

    The page is told the KEYS of the terminal command table so it can draw three
    buttons, and it must never be told the VALUES -- least of all now that one
    of them carries `--dangerously-skip-permissions`. A page that held the
    command would be one edit from sending it, and the closed table would stop
    being closed.
    """
    store = make_store(tmp_path)
    store.upsert(sid="", path=PROJECT, label="butler",
                 chords={"claude": "ctrl+f13"})

    served = json.dumps(store.public()) + json.dumps(store.bindings())

    assert "--dangerously-skip-permissions" not in served, (
        "the agent's command line crossed the wire; the page must only ever "
        "hold the NAME it asks for")
    assert '"claude"' in served, "the page cannot draw a button it is not told"


def test_the_pane_is_NOT_a_setting():
    """D1's rejected alternative, asserted so it cannot drift back.

    A `look`-group checkbox for a fold the owner can see and click would be two
    ways to say one thing, and it would put a page's view state into a schema
    every other device renders as a preference.
    """
    from daemon.settings import BY_KEY

    for key in BY_KEY:
        assert "shortcut" not in key and "pane" not in key, (
            f"{key!r} looks like the shortcuts pane leaking into the settings "
            f"schema; its state lives in the store (AC-133, D1)")


@pytest.mark.asyncio
async def test_the_answer_carries_what_the_PAGE_would_otherwise_have_to_know(
        served):
    """AC-41. A page that writes down a closed daemon table is naming one.

    The three agent names and the key table both come from here, so `web/` holds
    neither -- which is also what makes the picker fallback possible without a
    second key table in JavaScript (D3).
    """
    status, payload = await call("GET", "/api/shortcuts")

    assert status == 200
    assert payload["agents"] == list(shortcuts_mod.TERMINAL_AGENTS)
    assert "numpad_multiply" in payload["keys"] and len(payload["keys"]) > 40
    assert payload["bare_keys"] == list(actions.BARE_SAFE), (
        "the page cannot tell the owner which keys need no modifier, so the "
        "refusal will be the first they hear of it")


@pytest.mark.asyncio
@pytest.mark.parametrize("body, why", [
    ({}, "nothing at all"),
    ({"open": True, "dictate": "f13"}, "two fields at once"),
    ({"open": True, "show_chords": True}, "two view fields at once"),
    ({"nonsense": 1}, "a field nobody serves"),
])
async def test_the_post_body_says_exactly_ONE_thing(served, body, why):
    """Strict for `post_connections`'s reason: a caller that sent two fields
    believes both were applied, and quietly honouring one means the other fails
    later for a reason nobody connects to this request."""
    status, payload = await call("POST", "/api/shortcuts", body)

    assert status == 400, f"{why} was accepted: {payload}"


# --------------------------------------------------------------------------- #
# AC-140 -- the wire the desk agent reads
# --------------------------------------------------------------------------- #
def test_the_wire_carries_one_binding_per_CHORD_with_its_row_attached(tmp_path):
    """The projection the desk agent installs, and every field it needs.

    A binding with no `cwd` or no `agent` is a chord that opens a terminal
    somewhere the owner did not choose, and the agent has nothing to forward.
    """
    store = make_store(tmp_path)
    store.upsert(sid="", path=PROJECT, label="butler",
                 chords={"pi": "ctrl+f13", "claude": "ctrl+f14",
                         "opencode": ""})

    wire = store.bindings()

    assert wire[0] == {"chord": "numpad_multiply", "action": "dictate"}
    terminals = [b for b in wire if b["action"] == "terminal"]
    assert len(terminals) == 2, (
        f"an empty chord became a binding, or a real one did not: {wire}")
    assert {b["agent"] for b in terminals} == {"pi", "claude"}
    for binding in terminals:
        assert binding["cwd"] == PROJECT and binding["label"] == "butler"


def test_everything_on_the_wire_PARSES_at_bind_time(tmp_path):
    """The store validates at the route and the hook validates again, and this
    is the assertion that the two agree.

    If they disagreed, the owner would bind a chord the pane accepted and the
    agent would refuse the WHOLE table on the next poll -- so one bad row would
    take every other hotkey down with it (AC-140).
    """
    store = make_store(tmp_path)
    store.upsert(sid="", path=PROJECT, label="butler",
                 chords={"pi": "ctrl+numpad_multiply"})
    store.upsert(sid="", path=OTHER, label="crawler",
                 chords={"claude": "ctrl+shift+f13", "opencode": "win+f24"})

    table = actions.Table(store.bindings())

    assert len(table) == 4, f"the table the agent would install: {table.bindings}"


@pytest.mark.asyncio
async def test_the_bindings_ride_beside_the_SETTINGS_and_not_inside_them(served):
    """`docs/shortcuts.md` 4.2. The desk agent reads one surface.

    Composed by the ROUTE rather than by `Settings.describe()`, so the settings
    schema stays the settings schema and the store stays a store -- and the
    agent gets the table on the same poll it already makes, so there is no
    second surface to keep in step.
    """
    await call("POST", "/api/shortcuts",
               {"row": {"path": PROJECT, "chords": {"pi": "ctrl+f13"}}})

    status, body = await call("GET", "/api/settings")

    assert status == 200
    assert "bindings" in body, (
        "the desk agent has nowhere to read the binding table from, so no key "
        "would ever be bound")
    assert {"chord": "ctrl+f13", "action": "terminal", "cwd": PROJECT,
            "agent": "pi", "label": PROJECT} in body["bindings"], (
        f"the new chord did not reach the settings answer: {body['bindings']}")
    assert not any(s["key"] == "bindings" for s in body["settings"]), (
        "the table leaked into the settings schema, where it would be "
        "validated by the wrong rules and rendered as a control")


# --------------------------------------------------------------------------- #
# a HAND-EDITED file (connections.py:66-78, and the improvement on it)
# --------------------------------------------------------------------------- #
def test_a_bad_hand_edited_row_costs_ONE_ROW_and_not_the_daemon(tmp_path,
                                                               capsys):
    """The deliberate improvement on `connections.py` (module docstring).

    Connections raises out of its constructor, which stops the daemon booting.
    `settings.py`'s rule is that a bad preference is never worth refusing to
    boot over, because a daemon that will not start leaves no microphone at all
    (D-1). So a bad row is logged and skipped -- and the good rows around it
    survive, which is the half `public()` running over every entry used to
    destroy.
    """
    (tmp_path / "shortcuts.json").write_text(json.dumps({
        "rows": {
            "good": {"path": PROJECT, "label": "butler",
                     "chords": {"pi": "ctrl+f13"}},
            "broken": {"path": OTHER, "chords": "not-an-object"},
            "nowhere": {"label": "no path at all"},
        }}), encoding="utf-8")

    store = make_store(tmp_path)

    said = capsys.readouterr().out
    assert [r.id for r in store.all()] == ["good"], (
        f"one malformed row hid the good ones, or was kept: "
        f"{[r.id for r in store.all()]}")
    assert "broken" in said and "nowhere" in said, (
        f"a row was dropped in silence, so the owner's shortcut is gone with "
        f"nothing to say why: {said!r}")


def test_a_shortcuts_file_that_is_not_json_is_loud_and_not_fatal(tmp_path,
                                                                 capsys):
    (tmp_path / "shortcuts.json").write_text("{{{", encoding="utf-8")

    store = make_store(tmp_path)

    assert store.all() == [] and store.dictate == "numpad_multiply"
    assert "unreadable" in capsys.readouterr().out


def test_a_hand_edited_chord_the_key_table_refuses_is_kept_until_BIND_time(
        tmp_path):
    """Deliberate, and worth stating because it looks like a gap.

    The store validates SHAPE at load and chord SEMANTICS at the route. A file
    hand-edited to `"chords": {"pi": "e"}` therefore loads -- and is then
    refused by `Table.__init__`, which leaves the RUNNING hotkeys exactly as
    they were and says why (AC-140). Refusing it here instead would silently
    delete the owner's row; refusing it nowhere would suppress their `e` key.
    """
    (tmp_path / "shortcuts.json").write_text(json.dumps({
        "rows": {"bad": {"path": PROJECT, "chords": {"pi": "e"}}}}),
        encoding="utf-8")

    store = make_store(tmp_path)
    assert len(store.all()) == 1, "the row was deleted rather than refused"

    with pytest.raises(actions.BindingError) as excinfo:
        actions.Table(store.bindings())
    assert "'e'" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# a daemon that has no store at all
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_daemon_with_no_store_serves_NO_bindings_key(tmp_path,
                                                             monkeypatch):
    """Absent and empty are DIFFERENT facts, and the agent branches on it.

    Every Hub built by a test that does not know shortcuts exist has
    `shortcuts=None`, which is the whole reason the store defaults to None
    (`hub.py`). Such a daemon must serve NO `bindings` key rather than `[]`:
    absent means "keep the table you are running", and empty would mean "unbind
    every key on the owner's machine" (AC-140).
    """
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=FakeBrain(),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=Settings(tmp_path / "settings.json"))
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, body = await call("GET", "/api/settings")
    assert status == 200
    assert "bindings" not in body, (
        "a daemon with no store told the agent to unbind every key")

    status, payload = await call("GET", "/api/shortcuts")
    assert status == 404, payload


# --------------------------------------------------------------------------- #
# ONE PROJECT TABLE (the owner's backlog item 4, 2026-09-11)
# --------------------------------------------------------------------------- #
def test_the_rows_are_the_project_list(tmp_path):
    """The rows ARE the projects, in row order.

    Until this, there were two lists: the `projects` SETTING, which the spoken
    switch and the spawn gate read, and these ROWS, which the pane launched
    terminals from. Measured against the owner's own files on 2026-09-11 they
    had diverged completely -- `projects` held one directory that was not a row,
    and all three rows were unreachable by voice. `docs/shortcuts.md` D4
    predicted exactly that drift in writing and declined to fix it; this is the
    fix, and it is the owner's ruling of 2026-09-11.
    """
    store = make_store(tmp_path)
    assert store.paths() == ()
    store.upsert("", PROJECT, "Butler")
    store.upsert("", OTHER)
    assert store.paths() == (PROJECT, OTHER), (
        "the project list is the rows, in the order the pane draws them")


def test_a_retired_projects_setting_becomes_rows_ONCE(tmp_path):
    """`terminal_cwd`'s migration, met again (AC-144).

    Read from the settings FILE and not the store, because the key is retired
    from the schema in the same commit and `Settings._load` drops one it no
    longer knows -- which is the whole reason this is not `settings.get`.

    ONCE, on the `migrated_projects` flag, for the reason the first migration
    records: without it, deleting a row would bring the directory back on the
    next restart, and a row the owner explicitly removed reappearing is worse
    than never having migrated.
    """
    (tmp_path / "settings.json").write_text(
        json.dumps({"projects": [PROJECT, OTHER]}), encoding="utf-8")
    store = make_store(tmp_path)
    made = store.migrate_projects(tmp_path / "settings.json", tmp_path / "app")
    assert PROJECT in made and OTHER in made
    assert set(store.paths()) >= {PROJECT, OTHER}

    before = store.paths()
    store.remove(store.all()[0].id)
    after = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    after.migrate_projects(tmp_path / "settings.json", tmp_path / "app")
    assert len(after.paths()) == len(before) - 1, (
        "a removed row came back: the migration ran twice")


def test_butlers_own_directory_is_a_row(tmp_path):
    """The owner asked for "a root project which is the app's own directory".

    An ordinary row with an id rather than a special case in the matcher, so
    everything that already works on a row -- the chords, the three agent
    buttons, the spoken switch, the spawn gate -- works on this one with no
    branch anywhere.
    """
    app = tmp_path / "app"
    app.mkdir()
    store = make_store(tmp_path)
    store.migrate_projects(tmp_path / "settings.json", app)
    assert str(app) in store.paths(), (
        "Butler's own directory is not reachable as a project")

    # And never twice, however often it boots.
    store.migrate_projects(tmp_path / "settings.json", app)
    again = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    assert [p for p in again.paths() if p == str(app)] == [str(app)]


# --------------------------------------------------------------------------- #
# THE ROOT IS UNDELETABLE, UNRENAMEABLE, AND SORTS FIRST
# (R10, docs/filing-and-pacing.md D3; the owner's ruling of 2026-09-16 that it
# sorts first in the rail)
# --------------------------------------------------------------------------- #
def test_the_root_row_cannot_be_removed(tmp_path):
    """R10: "The Butler root project cannot be deleted or renamed."

    `remove()` refuses BY NAME rather than silently declining, the house rule
    for every refusal here -- and the row is still there afterward, which is
    the observable consequence that matters more than the exception type.
    """
    app = tmp_path / "app"
    app.mkdir()
    store = make_store(tmp_path)
    store.upsert("", str(tmp_path / "other"), "Other")
    store.migrate_projects(tmp_path / "settings.json", app)
    root_id = store.root_id()
    assert root_id, "the migration did not mint a root row"

    with pytest.raises(shortcuts_mod.ShortcutError):
        store.remove(root_id)
    assert root_id in {r.id for r in store.all()}, (
        "the root row was removed despite the refusal")

    # An ordinary row is unaffected: root protection must not become a
    # blanket refusal of every remove.
    other = next(r for r in store.all() if r.path == str(tmp_path / "other"))
    store.remove(other.id)
    assert other.id not in {r.id for r in store.all()}


def test_the_root_rows_directory_cannot_be_edited_away(tmp_path):
    """R10's "renamed" half, read as the recommendation in
    `docs/filing-and-pacing.md` D3: the PATH is what every root-protection
    check identifies the row by, so it is the one field that must not move.

    The LABEL is untouched by this rule -- renaming what the pane CALLS the
    root is cosmetic and stays allowed.
    """
    app = tmp_path / "app"
    app.mkdir()
    store = make_store(tmp_path)
    store.migrate_projects(tmp_path / "settings.json", app)
    root_id = store.root_id()

    with pytest.raises(shortcuts_mod.ShortcutError):
        store.upsert(root_id, str(tmp_path / "elsewhere"), "Root")
    assert store.get(root_id).path == str(app), (
        "the root row's directory moved despite the refusal")

    # The label is a different field and stays editable.
    store.upsert(root_id, str(app), "Home")
    assert store.get(root_id).label == "Home"
    assert store.is_root(store.get(root_id)), (
        "renaming the label made the row stop being root")


def test_the_root_row_sorts_first_in_what_the_page_is_served(tmp_path):
    """The owner's words, 2026-09-16: "butler project should also be at top
    of the list, as the local. on left side panel."

    Display-time only: `paths()` (read by AC-31's gates) and the file on disk
    keep the migration's own "root LAST" append order, so the dedupe argument
    in `migrate_projects` is untouched.
    """
    app = tmp_path / "app"
    app.mkdir()
    store = make_store(tmp_path)
    store.upsert("", str(tmp_path / "alpha"), "Alpha")
    store.upsert("", str(tmp_path / "beta"), "Beta")
    store.migrate_projects(tmp_path / "settings.json", app)
    root_id = store.root_id()

    served = store.public()["rows"]
    assert served[0]["id"] == root_id, (
        f"the root row is not first in what the page is served: {served}")
    assert [r["id"] for r in served[1:]] == [
        r.id for r in store.all() if r.id != root_id], (
        "every other row's relative order changed, not just root's position")

    # The stored order and the dedupe argument are UNCHANGED: root is still
    # last in `_rows`/`paths()`, only `public()`'s rows are reordered.
    assert store.paths()[-1] == str(app), (
        "the stored order moved; only the SERVED order should have")


def test_a_new_row_at_the_root_directory_cannot_be_created(tmp_path):
    """`is_root(row)` attaches to a DIRECTORY, never to a row's identity, so a
    caller upserting a BRAND-NEW row whose path is `app_dir` (or a respelling
    of it) would satisfy `is_root()` just as the real root row does -- and
    `remove()`'s guard, which only asks `is_root()`, would then refuse to
    delete it forever. Refusing the CREATE is the fix: `upsert()`'s existing
    root guard (today keyed on "sid names an existing root row") must also
    fire for a brand-new sid whose path collides with the root directory.
    """
    app = tmp_path / "app"
    app.mkdir()
    store = make_store(tmp_path)
    store.migrate_projects(tmp_path / "settings.json", app)
    root_id = store.root_id()

    with pytest.raises(shortcuts_mod.ShortcutError):
        store.upsert("", str(app), "Accidental")
    assert len([r for r in store.all() if store.is_root(r)]) == 1, (
        "a second row at the root directory was created")

    # a case/trailing-separator respelling of the same directory is refused
    # the same way -- `same_dir` is the one identity rule everywhere else.
    with pytest.raises(shortcuts_mod.ShortcutError):
        store.upsert("", str(app) + "\\", "Respelt")
    with pytest.raises(shortcuts_mod.ShortcutError):
        store.upsert("", str(app).upper(), "Upper")
    assert len([r for r in store.all() if store.is_root(r)]) == 1, (
        "a respelling of the root directory was allowed to create a second "
        "row")

    # the guard is scoped to the root directory alone -- an ordinary new row
    # elsewhere is unaffected.
    ordinary = store.upsert("", str(tmp_path / "other"), "Other")
    assert ordinary.id in {r.id for r in store.all()}

    # editing an EXISTING non-root row's path to become the root directory is
    # refused for the same reason -- it would mint a second root by a
    # different route.
    with pytest.raises(shortcuts_mod.ShortcutError):
        store.upsert(ordinary.id, str(app), "Other")
    assert store.get(ordinary.id).path == str(tmp_path / "other")


def test_migrate_projects_still_dedupes_the_root_last(tmp_path):
    """The new create-time guard must not break `migrate_projects`, which
    deliberately appends the root LAST and dedupes on `same_dir` (it is the
    one legitimate caller that mints a row whose path IS the app directory).
    """
    app = tmp_path / "app"
    app.mkdir()
    store = make_store(tmp_path)
    # a pre-existing row that already IS the app directory (e.g. the owner
    # had it in `projects` before the migration ever ran) must dedupe rather
    # than be refused as a collision with a root that does not exist yet.
    store.upsert("", str(app), "Pre-existing")

    store.migrate_projects(tmp_path / "settings.json", app)

    assert len([r for r in store.all() if store.is_root(r)]) == 1, (
        "migrate_projects created a second row at the root directory instead "
        "of deduping onto the pre-existing one"
    )
    assert store.paths()[-1] == str(app), (
        "the root row is not last, which is the dedupe argument this guard "
        "must not disturb")


def test_root_id_answers_empty_before_any_migration_has_run(tmp_path):
    """A store nothing has told about `app_dir` yet identifies no row as root,
    rather than guessing from a name or a path (D-18: one predicate, used
    everywhere, and it needs `_app_dir` to answer at all).
    """
    store = make_store(tmp_path)
    store.upsert("", str(tmp_path / "solo"), "Solo")
    assert store.root_id() == ""
    assert store.public()["root_id"] == ""


# --------------------------------------------------------------------------- #
# A RESIZABLE SIDEBAR (the owner's second-batch item 2, 2026-09-11)
# --------------------------------------------------------------------------- #
def test_the_sidebar_width_is_the_daemons_and_is_bounded(tmp_path):
    """"user should be able to increase size of sidebar so it is easier to
    manage the settings/ options there."

    In the STORE for `sidebar_collapsed`'s reason and not in a setting: AC-8
    bans browser storage outright, so the only place view state can live and
    survive a reload is the daemon -- and keeping it out of the schema is what
    lets `web/` go on naming no setting (AC-41).

    BOUNDED, and that is the half with teeth. A width is dragged, so it arrives
    from a pointer that can be anywhere: 0 hides the sidebar with no way back,
    and a number wider than the window leaves no page. The store clamps rather
    than refusing, because a drag that snapped back to nothing would read as a
    broken handle.
    """
    store = make_store(tmp_path)
    assert store.sidebar_width == shortcuts_mod.SIDEBAR_DEFAULT_W

    store.set_sidebar_width(420)
    assert store.sidebar_width == 420
    assert shortcuts_mod.ShortcutStore(
        tmp_path / "shortcuts.json").sidebar_width == 420, (
        "the width did not survive a reload, which is the whole point of it "
        "living on the daemon")

    store.set_sidebar_width(0)
    assert store.sidebar_width == shortcuts_mod.SIDEBAR_MIN_W
    store.set_sidebar_width(99999)
    assert store.sidebar_width == shortcuts_mod.SIDEBAR_MAX_W
    # A non-number is a malformed request, not a preference: refused out loud
    # and the width the owner chose is LEFT ALONE. Quietly resetting it to the
    # default would throw their choice away because a caller sent a string.
    with pytest.raises(shortcuts_mod.ShortcutError):
        store.set_sidebar_width("not a number")
    assert store.sidebar_width == shortcuts_mod.SIDEBAR_MAX_W, (
        "a value that is not a number changed the width")


@pytest.mark.asyncio
async def test_the_page_is_told_the_sidebar_width(served):
    """It rides on `public()` beside the other two pieces of view state, so the
    page reads one answer and holds no width of its own."""
    served.set_sidebar_width(390)

    status, body = await call("GET", "/api/shortcuts")

    assert status == 200
    assert body["sidebar_width"] == 390

    status, body = await call("POST", "/api/shortcuts", {"sidebar_width": 500})
    assert status == 200, body
    assert body["sidebar_width"] == 500
    assert served.sidebar_width == 500

    status, body = await call("POST", "/api/shortcuts", {"sidebar_width": 4})
    assert status == 200, body
    assert served.sidebar_width == shortcuts_mod.SIDEBAR_MIN_W, (
        "a drag to nothing left a sidebar the owner cannot get back")

    status, body = await call("POST", "/api/shortcuts",
                              {"sidebar_width": "wide please"})
    assert status == 400, body
    assert served.sidebar_width == shortcuts_mod.SIDEBAR_MIN_W, (
        "a refused width was written anyway")


# --------------------------------------------------------------------------- #
# THE RAIL'S OWN COLLAPSE (u-railB1 item 3, 2026-09-16)
# --------------------------------------------------------------------------- #
def test_the_rail_collapse_is_the_daemons_and_survives_a_reload(tmp_path):
    """`sidebar_collapsed`'s twin, for the rail instead of the list.

    Stored on the daemon for AC-8's reason: a per-device copy is how one
    laptop ended up with four disagreeing copies of a preference (D-2).
    """
    store = make_store(tmp_path)
    assert store.rail_collapsed is False

    store.set_rail_collapsed(True)
    assert store.rail_collapsed is True
    assert shortcuts_mod.ShortcutStore(
        tmp_path / "shortcuts.json").rail_collapsed is True, (
        "the rail's collapse did not survive a reload, which is the whole "
        "point of it living on the daemon")

    store.set_rail_collapsed(False)
    assert store.rail_collapsed is False


@pytest.mark.asyncio
async def test_the_page_is_told_the_rail_collapse(served):
    """It rides on `public()` beside `sidebar_collapsed` and `sidebar_width`,
    and round-trips through the one route."""
    status, body = await call("GET", "/api/shortcuts")
    assert status == 200
    assert body["rail_collapsed"] is False

    status, body = await call("POST", "/api/shortcuts",
                              {"rail_collapsed": True})
    assert status == 200, body
    assert body["rail_collapsed"] is True
    assert served.rail_collapsed is True

    status, body = await call("POST", "/api/shortcuts",
                              {"rail_collapsed": False})
    assert status == 200, body
    assert served.rail_collapsed is False


# --------------------------------------------------------------------------- #
# WHICH PROJECTS THE TREE SHOWS OPEN (docs/filing-and-pacing.md 8, D-D2,
# AC-291). View state on the daemon for AC-8's reason, beside the list's own
# collapse: a phone and a desk looking at one tree must agree about it (D-2).
# --------------------------------------------------------------------------- #
def test_the_expanded_projects_survive_a_reload(tmp_path):
    store = make_store(tmp_path)
    a = store.upsert("", str(tmp_path), "A")

    store.set_expanded_projects([a.id])

    assert shortcuts_mod.ShortcutStore(
        tmp_path / "shortcuts.json").public()["expanded_projects"] == [a.id]


def test_a_fresh_store_opens_on_the_root(tmp_path):
    """Never written, the answer is the ROOT row: a fresh install opens on
    Butler's own chats rather than on a tree of shut folders."""
    store = make_store(tmp_path)
    store.upsert("", str(tmp_path / "other"), "Other")
    store.migrate_projects(tmp_path / "settings.json", tmp_path)

    assert store.public()["expanded_projects"] == [store.root_id()]


def test_an_expanded_id_no_row_has_is_not_served(tmp_path):
    """A removed row's id stays in the file and never reaches the page, so a
    later row can never inherit an open state by id."""
    store = make_store(tmp_path)
    a = store.upsert("", str(tmp_path), "A")
    store.set_expanded_projects([a.id, "s-gone"])

    assert store.public()["expanded_projects"] == [a.id]


def test_the_expanded_set_refuses_what_is_not_a_list_of_ids(tmp_path):
    store = make_store(tmp_path)
    for bad in ("s-1", [1, 2], {"s-1": True}, None):
        with pytest.raises(shortcuts_mod.ShortcutError):
            store.set_expanded_projects(bad)


@pytest.mark.asyncio
async def test_the_page_can_write_the_expanded_set(served):
    row = served.upsert("", PROJECT, "Butler")

    status, body = await call("POST", "/api/shortcuts",
                              {"expanded_projects": [row.id]})
    assert status == 200, body
    assert body["expanded_projects"] == [row.id]

    status, body = await call("POST", "/api/shortcuts",
                              {"expanded_projects": "sideways"})
    assert status == 400, body
    assert served.public()["expanded_projects"] == [row.id]


# --------------------------------------------------------------------------- #
# CHAT LIST GROUPING (D1/C, docs/triage-list.md phase 3, owner's ruling
# 2026-09-16 -- BOTH axes over the spec's own state-only recommendation)
# --------------------------------------------------------------------------- #
def test_chat_grouping_is_the_daemons_and_survives_a_reload(tmp_path):
    """`sidebar_collapsed`'s twin again: AC-8 bans browser storage, and which
    grouping is active must agree across two devices looking at the same
    list (D-2). Deliberately not a setting either (AC-41): `web/` names none
    of it, same as every other flag in this store.
    """
    store = make_store(tmp_path)
    assert store.chat_grouping == "off"

    store.set_chat_grouping("state")
    assert store.chat_grouping == "state"
    assert shortcuts_mod.ShortcutStore(
        tmp_path / "shortcuts.json").chat_grouping == "state", (
        "the grouping choice did not survive a reload, which is the whole "
        "point of it living on the daemon")

    store.set_chat_grouping("project")
    assert store.chat_grouping == "project"

    store.set_chat_grouping("off")
    assert store.chat_grouping == "off"


def test_chat_grouping_refuses_a_value_off_the_closed_list(tmp_path):
    """A THIRD value that is not one of the two axes plus off is a malformed
    request, not a preference -- refused out loud and the stored choice is
    left alone, `set_sidebar_width`'s own rule for a bad type."""
    store = make_store(tmp_path)
    store.set_chat_grouping("state")

    with pytest.raises(shortcuts_mod.ShortcutError):
        store.set_chat_grouping("by-vibe")
    assert store.chat_grouping == "state", (
        "a refused grouping value changed the stored one anyway")


@pytest.mark.asyncio
async def test_the_page_is_told_the_chat_grouping(served):
    """It rides on `public()` beside the other view state, and round-trips
    through the one route -- the SAME shape `rail_collapsed` already has."""
    status, body = await call("GET", "/api/shortcuts")
    assert status == 200
    assert body["chat_grouping"] == "off"

    status, body = await call("POST", "/api/shortcuts",
                              {"chat_grouping": "project"})
    assert status == 200, body
    assert body["chat_grouping"] == "project"
    assert served.chat_grouping == "project"

    status, body = await call("POST", "/api/shortcuts",
                              {"chat_grouping": "sideways"})
    assert status == 400, body
    assert served.chat_grouping == "project", (
        "a refused grouping value was written anyway")


# --------------------------------------------------------------------------- #
# how much is IN a workspace (AC-229, docs/parallel-agents.md phase 1)
#
# A rail draws a number per project, so the number is the daemon's -- and it is
# DERIVED from the path each conversation already carries rather than stored,
# which is the same rule `GET /api/chats?project=` scopes by. Two claims here:
# the count is right, and the route still answers when there are no
# conversations to count at all.
# --------------------------------------------------------------------------- #
class _Recorded:
    """A brain that HOLDS CONVERSATIONS, over a real `ChatStore`.

    `served` runs on `FakeBrain`, which declares no sessions -- that is
    endpoint mode and it is a case this file needs, so the count is proved
    against a second fixture rather than by changing the first. The store is
    REAL: what is under test is a walk of `state/chats/` matched against the
    rows, and a scripted list of dicts would assert the route's arithmetic
    while skipping the part that can be wrong.
    """

    HAS_SESSIONS = True

    def __init__(self, store):
        self._chats = store

    def session_list(self, limit=None, belongs=None):
        return self._chats.listing(limit=limit, belongs=belongs)


@pytest.mark.asyncio
async def test_each_row_says_how_many_conversations_are_in_it(tmp_path,
                                                              monkeypatch):
    """One walk, every row, and a row with nothing in it says 0 rather than
    nothing at all.

    The THIRD conversation is the one with teeth: it is filed under the row's
    directory spelled with a trailing separator, which is a spelling the pane's
    own edit button produces and which the owner already has on one row. A
    count that compared paths as strings reports 1 here and 2 is the truth.
    """
    from daemon import chats as chats_mod

    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    store = make_store(tmp_path)
    a = store.upsert(sid="", path=str(alpha), label="Alpha")
    b = store.upsert(sid="", path=str(beta), label="Beta")
    root = tmp_path / "chats"
    for cid, project in (("chat-3000000000000-aaaa01", str(alpha)),
                         ("chat-2000000000000-aaaa02", str(alpha) + "\\"),
                         ("chat-1000000000000-none01", "")):
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{cid}.json").write_text(json.dumps(
            {"id": cid, "createdAt": 1, "updatedAt": 1, "harness": "fake",
             "sessionRef": "", "project": project,
             "messages": [{"id": "msg-1", "role": "user", "content": "hello",
                           "timestamp": 1}]}), encoding="utf-8")
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(),
                      brain=_Recorded(chats_mod.ChatStore(root)),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=Settings(tmp_path / "settings.json"),
                      shortcuts=store)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, body = await call("GET", "/api/shortcuts")

    assert status == 200, body
    counted = {row["id"]: row["chats"] for row in body["rows"]}
    assert counted == {a.id: 2, b.id: 0}, (
        f"the per-row conversation count is wrong; the unfiled conversation "
        f"and the trailing-separator spelling are what it gets wrong: "
        f"{counted}")


@pytest.mark.asyncio
async def test_the_rows_still_answer_when_there_are_no_conversations(served):
    """Endpoint mode, MEASURED: `GET /api/chats` 404s there and the project
    rows are served anyway, because the pane is a terminal launcher and does
    not stop being one (`docs/workspaces-ui.md` 0.2).

    So the count is 0 and the route answers. A route that reached for a
    conversation store that is not there would take the whole pane down in the
    one mode where the pane is all there is.
    """
    row = served.upsert(sid="", path=PROJECT, label="butler")

    status, body = await call("GET", "/api/shortcuts")

    assert status == 200, body
    assert [r["chats"] for r in body["rows"]] == [0], (
        f"a daemon holding no conversations did not say 0: {body['rows']}")
    assert body["rows"][0]["id"] == row.id


@pytest.mark.asyncio
async def test_only_the_GET_carries_the_count_and_the_POST_deliberately_does_not(
        served):
    """The daemon half of a rule the PAGE has to keep (review 2026-09-11).

    `GET /api/shortcuts` walks `state/chats/` once and puts a `chats` on every
    row; `POST /api/shortcuts` answers the SAME payload without one, on
    purpose -- it is the route a sidebar DRAG posts to on every release, and a
    count nobody looked at is not worth reading every conversation on disk for.

    That asymmetry is invisible from the daemon and was measured as a DEFECT in
    the page: `paintShortcuts` replaces the rows wholesale, so a drag, a
    collapse, a chord bind, a rename or an add silently deleted the count from
    every tile until a reload. The page now carries `chats` forward from the
    rows it already had; this pins the fact that makes that necessary, so
    "fixing" it by making the POST pay for the walk is a deliberate decision
    rather than an accident.

    `served` is endpoint mode, where the count is 0 -- which is the point: 0 is
    a NUMBER, and "absent" is what the page must be able to tell it from.
    """
    served.upsert(sid="", path=PROJECT, label="butler")

    _status, got = await call("GET", "/api/shortcuts")
    _status, posted = await call("POST", "/api/shortcuts",
                                 {"sidebar_width": 300})

    assert [r["chats"] for r in got["rows"]] == [0], (
        f"the GET stopped carrying the count the rail draws: {got['rows']}")
    assert all("chats" not in r for r in posted["rows"]), (
        f"the POST now pays for a directory walk on every sidebar drag; if "
        f"that is wanted, the page's carry-forward should go with it: "
        f"{posted['rows']}")


# --------------------------------------------------------------------------- #
# RIGHT_PANEL (docs/automations.md AU-10, AC-466): which ONE panel the desk's
# right-hand column shows -- "", "files" or "library". It REPLACED the bool
# `files_open` (docs/file-explorer.md 3.3, AC-312), because two bools make
# "both open" a representable state nobody can draw at 300 px. A value off
# the closed set is refused by name rather than coerced.
# --------------------------------------------------------------------------- #
def test_right_panel_ships_SHUT_and_survives_a_reload(tmp_path):
    store = make_store(tmp_path)
    assert store.public()["right_panel"] == ""
    assert "files_open" not in store.public()

    store.set_right_panel("library")

    assert shortcuts_mod.ShortcutStore(
        tmp_path / "shortcuts.json").public()["right_panel"] == "library"


def test_right_panel_refuses_what_is_not_in_the_closed_set_BY_NAME(tmp_path):
    store = make_store(tmp_path)
    store.set_right_panel("files")
    for bad in ("both", True, None, 1, "Files", ["files"]):
        with pytest.raises(shortcuts_mod.ShortcutError) as e:
            store.set_right_panel(bad)
        assert "right_panel" in str(e.value)
    assert store.public()["right_panel"] == "files"


@pytest.mark.parametrize("old,expected", [(True, "files"), (False, ""),
                                          ("yes", "")])
def test_files_open_is_migrated_ONCE_into_right_panel(tmp_path, old,
                                                      expected):
    """AC-466: `true` -> "files", once; the next save drops the old key, so
    the migration cannot run twice and overrule a later choice."""
    path = tmp_path / "shortcuts.json"
    path.write_text(json.dumps({"files_open": old, "rows": {}}),
                    encoding="utf-8")
    store = shortcuts_mod.ShortcutStore(path)
    assert store.public()["right_panel"] == expected
    store.set_right_panel("library")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "files_open" not in raw and raw["right_panel"] == "library"


def test_a_written_right_panel_outranks_a_stale_files_open(tmp_path):
    path = tmp_path / "shortcuts.json"
    path.write_text(json.dumps({"files_open": True, "right_panel": "",
                                "rows": {}}), encoding="utf-8")
    assert shortcuts_mod.ShortcutStore(path).public()["right_panel"] == ""


@pytest.mark.asyncio
async def test_the_page_can_write_right_panel_and_a_bad_value_writes_nothing(
        served):
    status, body = await call("POST", "/api/shortcuts",
                              {"right_panel": "library"})
    assert status == 200, body
    assert body["right_panel"] == "library"

    status, body = await call("POST", "/api/shortcuts", {"right_panel": "x"})
    assert status == 400 and "right_panel" in body["error"], body
    assert served.public()["right_panel"] == "library"
    # The retired key is refused rather than half-honoured.
    status, body = await call("POST", "/api/shortcuts", {"files_open": True})
    assert status == 400 and "right_panel" in body["error"], body


def test_a_moved_data_root_gets_its_root_row_on_an_already_migrated_store(
        tmp_path):
    """AC-415 (`docs/data-root.md`): the root row is minted from the DATA
    root, and ENSURED on every boot rather than only the first migration.

    The owner's store has `migrated_projects: true` since 2026-09-11 and a
    row for the repo; once `BUTLER_DATA` moves the root project to their
    home folder, a boot must give that folder a row -- or `root_id` answers
    "" and every "new chat in the root" has nowhere to land (R5). The repo's
    row stays an ordinary row, so the chats filed under it stay listed.
    """
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    store = make_store(tmp_path)
    store.migrate_projects(tmp_path / "settings.json", repo)
    code_id = store.root_id()

    moved = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    made = moved.migrate_projects(tmp_path / "settings.json", home)

    assert made == [str(home)]
    root = moved.get(moved.root_id())
    assert root.path == str(home), "the root row is not the data root"
    assert not moved.is_root(moved.get(code_id))
    moved.remove(code_id)            # an ordinary row now, so removable
    assert moved.migrate_projects(tmp_path / "settings.json", home) == [], (
        "the root row was minted twice")


# --------------------------------------------------------------------------- #
# docs/project-roots.md: a project is never a whole disk (AC-751, AC-752)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", [
    "C:\\", "C:", "E:\\", "/", r"\\server\share" + "\\"]
    if sys.platform == "win32"
    else ["/"])
def test_a_project_cannot_be_a_whole_drive(tmp_path, path):
    """AC-751 (PJ-2): with a row at a drive root the file explorer reads and
    writes the whole disk. Refused at the write, naming the path."""
    store = make_store(tmp_path)
    with pytest.raises(shortcuts_mod.ShortcutError, match="whole"):
        store.upsert(sid="", path=path, label="disk")
    assert store.all() == []


def test_a_project_cannot_be_the_home_folder_but_can_live_inside_it(
        tmp_path, monkeypatch):
    """AC-751 (PJ-2): home itself is refused; a folder inside it is not."""
    home = tmp_path / "home"
    (home / "code").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    store = make_store(tmp_path)
    with pytest.raises(shortcuts_mod.ShortcutError, match="home"):
        store.upsert(sid="", path=str(home), label="home")
    row = store.upsert(sid="", path=str(home / "code"), label="code")
    with pytest.raises(shortcuts_mod.ShortcutError, match="whole"):
        store.upsert(sid=row.id, path="/" if sys.platform != "win32"
                     else "C:\\", label="moved")
    assert [r.path for r in store.all()] == [str(home / "code")]


@pytest.mark.asyncio
async def test_the_route_refuses_a_whole_drive_and_writes_nothing(served):
    """AC-751 through the route: 400 naming why, and no row."""
    root = "C:\\" if sys.platform == "win32" else "/"
    status, payload = await call("POST", "/api/shortcuts",
                                 {"row": {"path": root, "label": "disk"}})
    assert status == 400 and "whole" in json.dumps(payload), payload
    status, payload = await call("GET", "/api/shortcuts")
    assert payload["rows"] == [], payload["rows"]


def test_an_old_row_at_a_root_still_loads_and_is_named(tmp_path, capsys):
    """AC-752 (PJ-3): the rule is for writes; a row already on disk keeps
    working and boot names it once so the owner can move it."""
    root = "C:\\" if sys.platform == "win32" else "/"
    (tmp_path / "shortcuts.json").write_text(json.dumps(
        {"rows": {"s-old": {"path": root, "label": "disk"}}}),
        encoding="utf-8")
    store = make_store(tmp_path)
    assert [r.path for r in store.all()] == [root]
    out = capsys.readouterr().out
    assert out.count("whole") == 1 and root in out


# --------------------------------------------------------------------------- #
# docs/project-roots.md PJ-4: a project the owner adds is a real folder
# (AC-754). Found 2026-10-29 on the Mac mini: a row typed as
# `\Volumes\4TBowner\PROJECTS\ANA` was stored, and on macOS a backslash is
# a plain character, so the project could open nothing.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", [
    "relative/folder",
    "\Volumes\share\PROJECTS\ANA",
    "missing"])
async def test_the_route_refuses_a_path_that_is_not_a_folder_here(
        served, tmp_path, spelling):
    """AC-754: 400 naming the path, and no row written. `missing` is an
    absolute path to a folder that does not exist."""
    path = str(tmp_path / "gone") if spelling == "missing" else spelling
    status, payload = await call("POST", "/api/shortcuts",
                                 {"row": {"path": path, "label": "x"}})
    assert status == 400 and path in payload.get("error", ""), payload
    assert served.all() == [], [r.path for r in served.all()]


@pytest.mark.asyncio
async def test_the_route_can_rename_a_row_whose_folder_has_gone(
        served, tmp_path):
    """AC-754 is a rule for create and move, like PJ-2: a row whose share is
    unmounted can still be renamed, so the owner is never stuck with it."""
    row = served.upsert(sid="", path=str(tmp_path / "gone"), label="old")
    status, payload = await call(
        "POST", "/api/shortcuts",
        {"row": {"id": row.id, "path": row.path, "label": "new"}})
    assert status == 200, payload
    assert served.get(row.id).label == "new"

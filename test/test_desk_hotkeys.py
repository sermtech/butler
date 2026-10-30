"""The hook's decision, tested for the first time. `docs/shortcuts.md` 4.4.

Until `classify` existed, everything about which key does what lived inside a
`WH_KEYBOARD_LL` callback that needed a real Windows hook, a real message pump
and a real keystroke to reach -- so nothing in pytest could see it, and the two
mislabelled-edge defects this project paid for were both found by reading
`butler.log` after the fact. `classify` is that decision with the Win32 taken
out: it touches no user32, and it is pure of WINDOWS rather than pure of state
(it updates the caller's `held` dict, which the hook thread owns).

FOUR CLAIMS.

**The match is EXACT.** `ctrl+numpad *` must not fire when Ctrl AND Shift are
held, because that is a different chord and may be bound to something else --
and a subset match would make a BARE binding fire under every modifier, so
`numpad *` would dictate in the middle of every Ctrl chord.

**Suppression is SYMMETRIC.** A key-down that was passed through leaves no
entry in `held`, so its key-up passes through too. A suppressed key-up with no
matching key-down leaves the application under the caret believing a key is
still held.

**The UP REPLAYS the DOWN.** Not re-resolved. This is what deletes the whole
class of defect where a release arrives labelled as a different action because
the owner let go of Ctrl first.

**The callback actually returns 1.** A `classify` that decided correctly and a
`_callback` that called `CallNextHookEx` anyway look identical from every other
angle, which is D-18 exactly: the contract needs an assertion on the CALLER.
So one test drives the real `_callback` against a fake `_u32`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desk import actions, hotkeys                          # noqa: E402

PROJECT = r"E:\PROJECTS\butler"

DICTATE = {"chord": "numpad_multiply", "action": "dictate"}
TERMINAL = {"chord": "ctrl+numpad_multiply", "action": "terminal",
            "cwd": PROJECT, "agent": "pi", "label": "butler"}
MULTIPLY = actions.KEYS["numpad_multiply"].vk
KEY_E = actions.KEYS["e"].vk


@pytest.fixture
def table():
    return actions.Table([DICTATE, TERMINAL])


def down(table, held, vk, mods=0):
    return hotkeys.classify(table, held, vk, hotkeys.WM_KEYDOWN, mods)


def up(table, held, vk, mods=0):
    return hotkeys.classify(table, held, vk, hotkeys.WM_KEYUP, mods)


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #
def test_the_exact_chord_fires_and_is_suppressed(table):
    held: dict = {}

    (binding, edge), suppress = down(table, held, MULTIPLY, actions.CTRL)

    assert edge == hotkeys.DOWN
    assert binding.action == "terminal", (
        f"Ctrl plus the key resolved to {binding.action!r}")
    assert binding.cwd == PROJECT and binding.agent == "pi", (
        "the binding reached the agent without its row's directory or agent, "
        "so nothing downstream can know which project was asked for")
    assert suppress is True, (
        "a bound chord was passed through to the application, so the owner "
        "gets the character as well as the action")


def test_the_bare_chord_and_the_modified_one_are_DIFFERENT_bindings(table):
    held: dict = {}

    (bare, _), _ = down(table, held, MULTIPLY, 0)
    held.clear()
    (modified, _), _ = down(table, held, MULTIPLY, actions.CTRL)

    assert (bare.action, modified.action) == ("dictate", "terminal"), (
        f"one physical key with two chords resolved to "
        f"{(bare.action, modified.action)}")


@pytest.mark.parametrize("mods, why", [
    (actions.CTRL | actions.SHIFT, "Ctrl AND Shift"),
    (actions.ALT, "Alt instead of Ctrl"),
    (actions.CTRL | actions.WIN, "Ctrl AND Win"),
])
def test_a_chord_that_is_NOT_bound_passes_through_untouched(table, mods, why):
    """The exact-match rule, and the reason it is not a subset test.

    A subset match would fire `ctrl+numpad *` under Ctrl+Shift, which may be
    somebody else's shortcut -- and would fire the BARE `numpad *` under every
    modifier, so a dictation would start in the middle of every Ctrl chord the
    owner typed.
    """
    held: dict = {}

    event, suppress = down(table, held, MULTIPLY, mods)

    assert event is None, f"{why} fired a binding: {event}"
    assert suppress is False, (
        f"{why} was SUPPRESSED, so the owner's own chord never reached the "
        f"application and the key appears broken")
    assert held == {}, (
        f"{why} was recorded as held, so its key-up would be suppressed too "
        f"and the application would be left believing a key is down")


def test_a_key_nobody_bound_is_not_looked_at_at_all(table):
    """The hot path. This runs for EVERY KEYSTROKE ON THE MACHINE."""
    held: dict = {}

    assert down(table, held, KEY_E, 0) == (None, False)
    assert up(table, held, KEY_E, 0) == (None, False)
    assert held == {}


def test_a_message_that_is_neither_edge_is_ignored(table):
    """A low-level hook receives only key messages, but `classify` is given a
    raw wparam and must not treat an unknown one as a press."""
    held: dict = {}

    assert hotkeys.classify(table, held, MULTIPLY, 0x0200, 0) == (None, False)
    assert held == {}


# --------------------------------------------------------------------------- #
# the UP replays the DOWN
# --------------------------------------------------------------------------- #
def test_the_UP_replays_the_action_the_DOWN_started(table):
    held: dict = {}
    down(table, held, MULTIPLY, 0)

    (binding, edge), suppress = up(table, held, MULTIPLY, 0)

    assert (binding.action, edge) == ("dictate", hotkeys.UP)
    assert suppress is True
    assert held == {}, "the release did not clear the held entry"


def test_the_UP_replays_even_when_the_MODIFIERS_CHANGED(table):
    """The defect this design deletes, and it cost a whole dictation.

    Hold the key alone, speak, then touch Ctrl before letting go. The release
    used to be re-resolved from the modifiers held at that instant and arrived
    labelled `terminal`, so the capture was never delivered and the microphone
    kept filling to the ceiling. Replaying makes it unproducible.
    """
    held: dict = {}
    down(table, held, MULTIPLY, 0)                     # a plain dictation

    (binding, edge), suppress = up(table, held, MULTIPLY, actions.CTRL)

    assert binding.action == "dictate", (
        f"the release was re-resolved and arrived as {binding.action!r}; the "
        f"dictation is lost and the microphone keeps filling")
    assert edge == hotkeys.UP and suppress is True


def test_the_UP_of_a_chord_that_never_fired_passes_through(table):
    """Symmetry. A key-up with no matching key-down is the shape that leaves an
    application believing a key is still held."""
    held: dict = {}

    event, suppress = up(table, held, MULTIPLY, 0)

    assert event is None and suppress is False


def test_auto_repeat_REPLAYS_and_does_not_re_resolve(table):
    """A low-level hook sees a held key as a stream of key-downs.

    Each repeat must carry the SAME binding -- so the agent's latch, which is
    keyed on the key, sees one instruction -- and must still be suppressed, or
    the character appears twenty times in the document the owner is dictating
    into.
    """
    held: dict = {}
    (first, _), _ = down(table, held, MULTIPLY, 0)

    repeats = [down(table, held, MULTIPLY, actions.CTRL) for _ in range(5)]

    for (binding, edge), suppress in repeats:
        assert binding is first, (
            f"a repeat re-resolved to {binding.action!r}; Ctrl touched "
            f"mid-hold would relabel the edge")
        assert edge == hotkeys.DOWN and suppress is True


def test_a_table_swapped_MID_HOLD_still_lets_the_release_finish(table):
    """`_held` belongs to the hook thread and `set_table` never touches it.

    The owner rebinds a chord on the page while holding the old one. If the
    release were resolved against the NEW table it would find nothing, pass
    through, and leave the capture running with the microphone open -- and the
    agent would have a stage it can never leave (D-1).
    """
    held: dict = {}
    down(table, held, MULTIPLY, 0)

    fresh = actions.Table([{"chord": "f13", "action": "dictate"}])
    (binding, edge), suppress = up(fresh, held, MULTIPLY, 0)

    assert binding.action == "dictate" and edge == hotkeys.UP, (
        "the release was lost to a rebind, so the capture never ends")
    assert suppress is True
    assert held == {}


def test_two_keys_held_at_once_are_tracked_separately():
    """`held` is keyed on the VK, so one key's release cannot end another's."""
    table = actions.Table([{"chord": "f13", "action": "dictate"},
                           {"chord": "f14", "action": "dictate"}])
    a, b = actions.KEYS["f13"].vk, actions.KEYS["f14"].vk
    held: dict = {}

    down(table, held, a, 0)
    down(table, held, b, 0)
    assert sorted(held) == sorted([a, b])

    up(table, held, b, 0)
    assert list(held) == [a], (
        f"releasing one key cleared another's entry: {held}")


# --------------------------------------------------------------------------- #
# the CALLER (D-18): suppression has to reach Windows
# --------------------------------------------------------------------------- #
class FakeU32:
    """Just enough user32 for `_callback`, and it RECORDS the chain call.

    The whole point: `classify` answering `suppress=True` and `_callback`
    returning `CallNextHookEx` anyway are indistinguishable from every other
    angle, and only the second one lets the character through.
    """

    def __init__(self, held_mods=0):
        self.held_mods = held_mods
        self.chained = 0

    def GetAsyncKeyState(self, vk):
        for mod_vk, bit in actions.MOD_VKS:
            if mod_vk == vk and self.held_mods & bit:
                return -32768                              # the HIGH bit
        return 0

    def CallNextHookEx(self, *_a):
        self.chained += 1
        return 0


class FakeKb:
    def __init__(self, vk, mark=0):
        self.vkCode = vk
        self.dwExtraInfo = mark


def drive(hook, vk, wparam, mark=0):
    """Call the real `_callback` with the ctypes cast stubbed out.

    `ctypes.cast(lparam, POINTER(KBDLLHOOKSTRUCT))` needs a real pointer to a
    real struct, so the lparam here is the struct itself and the cast is
    replaced -- the smallest possible substitution, so what runs is the
    function that ships rather than a description of it.
    """
    import ctypes

    real_cast = ctypes.cast

    class Holder:
        def __init__(self, contents):
            self.contents = contents

    ctypes.cast = lambda p, _t: Holder(p)
    try:
        return hook._callback(0, wparam, FakeKb(vk, mark))
    finally:
        ctypes.cast = real_cast


def test_the_callback_returns_1_so_the_key_never_reaches_the_application(table):
    """D-18. The rule the callee's own tests cannot prove."""
    hook = hotkeys.Hook(table)
    hook._u32 = FakeU32(held_mods=actions.CTRL)

    answer = drive(hook, MULTIPLY, hotkeys.WM_KEYDOWN)

    assert answer == 1, (
        f"the callback returned {answer!r} instead of 1, so Windows passed the "
        f"keystroke on and the owner gets the character as well as the action")
    assert hook._u32.chained == 0, "the hook chain was called for a bound chord"
    binding, edge = hook.events.get_nowait()
    assert (binding.action, edge) == ("terminal", hotkeys.DOWN)


def test_the_callback_CHAINS_a_key_it_does_not_own(table):
    hook = hotkeys.Hook(table)
    hook._u32 = FakeU32()

    answer = drive(hook, KEY_E, hotkeys.WM_KEYDOWN)

    assert answer == 0 and hook._u32.chained == 1, (
        "an unbound key was swallowed, which suppresses somebody else's "
        "keystroke system-wide")
    assert hook.events.empty()


def test_the_callback_passes_OUR_OWN_injected_keys_through(table):
    """`MARK`. The paste chord is Ctrl plus V, and a hook that read its own
    injection would be reading the keystrokes it just sent."""
    hook = hotkeys.Hook(table)
    hook._u32 = FakeU32()

    answer = drive(hook, MULTIPLY, hotkeys.WM_KEYDOWN, mark=hotkeys.MARK)

    assert answer == 0 and hook._u32.chained == 1
    assert hook.events.empty(), "the agent read back its own injection"


def test_set_table_is_ONE_assignment_and_takes_no_lock():
    """`docs/shortcuts.md` 4.4, and it is a rule about a FAILURE MODE.

    The coordinator thread would hold a lock while it parses and formats a new
    table, so a lock here converts a settings-poll hiccup into a REMOVED HOOK --
    Windows unhooks a callback whose thread stops pumping past
    `LowLevelHooksTimeout`, which is the failure the whole module exists to
    prevent. Asserted by grep, because there is nothing to observe: the correct
    behaviour is the absence of a wait.
    """
    source = Path(hotkeys.__file__).read_text(encoding="utf-8")
    body = source.split("def set_table")[1].split("def ")[0]
    assert "acquire" not in body and "with self._lock" not in body, (
        f"set_table takes a lock: {body!r}")
    hook = hotkeys.Hook()
    fresh = actions.Table([DICTATE])
    hook.set_table(fresh)
    assert hook.table is fresh


# --------------------------------------------------------------------------- #
# the table itself, at BIND time -- the only door (AC-138, AC-140)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("chord, why", [
    ("e", "a bare letter"),
    ("5", "a bare digit"),
    ("return", "a bare Return"),
    ("escape", "a bare Escape"),
    ("numpad3", "a bare numpad DIGIT, which needs NumLock on to arrive at all"),
    ("numpad_add", "a bare numpad +, which people type"),
    ("space", "a bare space"),
])
def test_a_bare_key_that_is_not_bare_safe_is_refused_BY_NAME(chord, why):
    """AC-138, and it is the one refusal whose failure is NOT recoverable from
    the page: with `e` suppressed everywhere the owner cannot type into the pane
    that would undo it.

    Suppression stops a key producing its character SYSTEM-WIDE while hotkeys
    are on, so the only keys safe to bind bare are the ones almost nothing
    needs. That is also exactly why `docs/desk-hotkeys.md` D2 chose numpad `*`.
    """
    with pytest.raises(actions.BindingError) as excinfo:
        actions.Table([{"chord": chord, "action": "dictate"}])

    message = str(excinfo.value)
    assert chord in message, f"{why} was refused without naming it: {message}"
    assert "numpad_multiply" in message, (
        f"the refusal does not say what CAN be bound bare: {message}")


@pytest.mark.parametrize("chord", sorted(actions.BARE_SAFE))
def test_every_bare_safe_key_really_can_be_bound_bare(chord):
    """The other half. A list nothing can use is worse than no list."""
    assert len(actions.Table([{"chord": chord, "action": "dictate"}])) == 1


def test_the_same_key_with_a_modifier_is_always_allowed():
    for chord in ("ctrl+e", "alt+return", "ctrl+shift+numpad3", "win+space"):
        assert len(actions.Table([{"chord": chord, "action": "dictate"}])) == 1


def test_a_modifier_cannot_BE_the_key_because_it_has_no_entry():
    """By construction rather than by a check. `KEYS` holds no modifier VK, so
    there is nothing to forget to test for."""
    assert not set(actions.MODS) & set(actions.KEYS)
    for name in actions.MODS:
        with pytest.raises(actions.BindingError):
            actions.Table([{"chord": name, "action": "dictate"}])


def test_numpad_enter_is_DELIBERATELY_absent():
    """Its code is `NumpadEnter` and its VK is VK_RETURN, the same as the main
    Enter -- and `classify` matches on VK alone. Binding it would suppress the
    owner's Enter key while the table claimed to have named a numpad key."""
    assert "numpad_enter" not in actions.KEYS
    assert actions.KEYS["return"].vk == 0x0D


def test_one_chord_bound_TWICE_is_refused():
    """Last-one-wins would mean the owner sees one binding work and cannot tell
    which, with the answer depending on dict order in a JSON file."""
    with pytest.raises(actions.BindingError) as excinfo:
        actions.Table([DICTATE, {"chord": "numpad_multiply",
                                 "action": "terminal", "cwd": PROJECT,
                                 "agent": "pi"}])
    assert "numpad_multiply" in str(excinfo.value)


def test_an_action_nobody_wrote_is_refused_at_BIND_time():
    """The closed set, checked here as well as at the route, because
    `state/shortcuts.json` is hand-editable and this is the last gate before a
    key is suppressed system-wide (`desk/AGENTS.md`)."""
    with pytest.raises(actions.BindingError) as excinfo:
        actions.Table([{"chord": "f13", "action": "run_this_command"}])
    assert "run_this_command" in str(excinfo.value)


def test_there_is_no_second_door_into_a_table():
    """AC-138's last clause. Validation at bind time is only a complete rule
    while `Table.__init__` is the only way a binding is made."""
    assert not hasattr(actions.Table, "add_binding")
    assert not hasattr(actions.Table, "add")
    table = actions.Table([DICTATE])
    assert isinstance(table.bindings, tuple), (
        "the binding list is mutable, so a caller can append past the parser")


# --------------------------------------------------------------------------- #
# the wire, both directions
# --------------------------------------------------------------------------- #
def test_a_browser_KEY_CODE_resolves_here_and_not_in_javascript():
    """`docs/shortcuts.md` D3. One key table, in Python.

    The page sends `code` and never `key`: `key` is layout-dependent, so binding
    on it means one physical key produces a different binding on a Russian or
    AZERTY layout -- the mistake `desk/paste.py` records for the paste chord.
    """
    assert actions.canonical_chord(
        {"code": "NumpadMultiply", "ctrl": True}) == "ctrl+numpad_multiply"
    assert actions.canonical_chord(
        {"code": "F13", "shift": True, "alt": True}) == "alt+shift+f13"
    # A browser reports the Windows key as `metaKey`.
    assert actions.canonical_chord({"code": "KeyK", "meta": True}) == "win+k"


def test_the_canonical_order_is_fixed_so_two_spellings_cannot_both_be_stored():
    assert (actions.canonical_chord("shift+ctrl+e")
            == actions.canonical_chord("ctrl+shift+e") == "ctrl+shift+e")


def test_a_code_this_daemon_cannot_bind_says_so_rather_than_guessing():
    with pytest.raises(actions.BindingError) as excinfo:
        actions.canonical_chord({"code": "MediaPlayPause", "ctrl": True})
    assert "MediaPlayPause" in str(excinfo.value)


def test_an_empty_chord_means_UNBOUND_and_is_not_an_error():
    """A row whose pi chord is cleared is a normal edit, not a refusal."""
    assert actions.canonical_chord(None) == ""
    assert actions.canonical_chord("") == ""


def test_the_install_line_names_every_chord_and_what_it_does(table):
    """AC-142. It is the owner's only record of what is suppressed system-wide."""
    said = table.describe()

    assert "numpad_multiply dictates" in said
    assert "ctrl+numpad_multiply opens pi in " + PROJECT in said
    assert actions.Table().describe() == "no bindings"

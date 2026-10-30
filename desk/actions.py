"""The key table, the chord parser, and the closed set of actions.

`docs/shortcuts.md` 4.3. Pure data plus one parser: this module imports
nothing Win32 and touches no device, which is what lets the DAEMON import it
for the `code` half of the key table while the desk agent imports it for the
VK half. One table, so a name cannot mean one key in the browser and another
on the hook.

THREE RULES THIS FILE EXISTS TO KEEP
------------------------------------
**`KEYS` IS the allow-list.** An unnamed key cannot be bound, and no modifier
VK has an entry at all -- so "bind Ctrl itself" is impossible by construction
rather than by a check somebody has to remember to write. The same goes for
`ACTIONS`: a chord can only ever name one of two written behaviours, never a
command line (`desk/AGENTS.md`).

**"numpad" is NOT bare-safe, and that correction cost nothing only because it
was caught before the table shipped.** `VK_NUMPAD0..9` arrive ONLY with
NumLock on; with it off the same physical keys send Insert, End, Home, PageUp
and the arrows, so a bare numpad-digit binding silently stops working the
first time anybody touches NumLock. And numpad `+` and `-` are typed
constantly by anyone who uses the numpad at all. The honest bare-safe set is
`numpad_multiply`, `numpad_divide`, `f13`-`f24`, `pause` and `scroll_lock` --
which is also exactly why `docs/desk-hotkeys.md` D2 chose numpad `*` in the
first place. Suppression stops a key producing its character SYSTEM-WIDE, so
the only keys safe to bind with no modifier are the ones almost nothing needs.

**Validation happens at BIND TIME and there is no other door.** `Table` is the
only way a binding reaches the hook, and `Table.__init__` parses every row. A
UI-only rule would mean one write to an unauthenticated tailnet route could
bind bare `Return` and deny input on the owner's desk -- where they then cannot
type into the page that would undo it (AC-138).
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# modifiers
# --------------------------------------------------------------------------- #
# A bitmask rather than a set, because the hook compares one integer per
# candidate keystroke. CANONICAL ORDER is the order below: a chord is written
# and stored in it, so two spellings of one binding cannot both be stored and
# then collide at bind time.
CTRL, ALT, SHIFT, WIN = 1, 2, 4, 8
MODS = {"ctrl": CTRL, "alt": ALT, "shift": SHIFT, "win": WIN}
MOD_ORDER = ("ctrl", "alt", "shift", "win")

# The VKs the hook polls to build that mask. Both Windows keys map to the same
# bit: nothing in this feature can usefully tell them apart, and a binding that
# worked on the left key and not the right would read as broken.
VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
MOD_VKS = ((VK_CONTROL, CTRL), (VK_MENU, ALT), (VK_SHIFT, SHIFT),
           (VK_LWIN, WIN), (VK_RWIN, WIN))


def mod_names(mods: int) -> list[str]:
    """The mask as names, in canonical order.

    Used by the paste path: a HOLD binding with a modifier means the owner is
    still physically holding that modifier when the transcript lands, and
    `paste.wait_for_modifiers` must EXCLUDE it rather than inject a key-up for
    a key they are pressing (`docs/shortcuts.md` 4.6).
    """
    return [name for name in MOD_ORDER if mods & MODS[name]]


# --------------------------------------------------------------------------- #
# the key table
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Key:
    """One bindable key, by all three of its names.

    `code` is the browser's W3C `KeyboardEvent.code`, so a chord captured by
    pressing keys in the page resolves here rather than in JavaScript.
    `bare_safe` says whether this key may be bound with NO modifier.
    """
    vk: int
    code: str
    bare_safe: bool = False


def _row(name: str, vk: int, code: str, bare_safe: bool = False):
    return name, Key(vk=vk, code=code, bare_safe=bare_safe)


def _build() -> dict[str, Key]:
    out: dict[str, Key] = {}

    def add(name, vk, code, bare_safe=False):
        out[name] = Key(vk=vk, code=code, bare_safe=bare_safe)

    for i in range(26):                       # a..z
        add(chr(ord("a") + i), 0x41 + i, f"Key{chr(ord('A') + i)}")
    for d in range(10):                       # 0..9 on the main row
        add(str(d), 0x30 + d, f"Digit{d}")
    for n in range(1, 25):                    # F1..F24
        # F13-F24 are the bare-safe half: no keyboard produces them without a
        # remap, so nothing on the machine is waiting for one.
        add(f"f{n}", 0x6F + n, f"F{n}", bare_safe=n >= 13)
    for d in range(10):                       # numpad digits -- NOT bare-safe
        add(f"numpad{d}", 0x60 + d, f"Numpad{d}")
    add("numpad_multiply", 0x6A, "NumpadMultiply", bare_safe=True)
    add("numpad_divide", 0x6F, "NumpadDivide", bare_safe=True)
    add("numpad_add", 0x6B, "NumpadAdd")
    add("numpad_subtract", 0x6D, "NumpadSubtract")
    add("numpad_decimal", 0x6E, "NumpadDecimal")
    # `numpad_enter` is deliberately ABSENT. Its code is `NumpadEnter` and its
    # VK is VK_RETURN -- the SAME vk as the main Enter -- and `classify`
    # matches on vk alone, so binding it would suppress the owner's Enter key
    # while the table claimed to have named a numpad key.
    add("pause", 0x13, "Pause", bare_safe=True)
    add("scroll_lock", 0x91, "ScrollLock", bare_safe=True)
    add("insert", 0x2D, "Insert")
    add("delete", 0x2E, "Delete")
    add("home", 0x24, "Home")
    add("end", 0x23, "End")
    add("page_up", 0x21, "PageUp")
    add("page_down", 0x22, "PageDown")
    add("up", 0x26, "ArrowUp")
    add("down", 0x28, "ArrowDown")
    add("left", 0x25, "ArrowLeft")
    add("right", 0x27, "ArrowRight")
    add("space", 0x20, "Space")
    add("return", 0x0D, "Enter")
    add("tab", 0x09, "Tab")
    add("escape", 0x1B, "Escape")
    add("backspace", 0x08, "Backspace")
    add("minus", 0xBD, "Minus")
    add("equal", 0xBB, "Equal")
    add("bracket_left", 0xDB, "BracketLeft")
    add("bracket_right", 0xDD, "BracketRight")
    add("backslash", 0xDC, "Backslash")
    add("semicolon", 0xBA, "Semicolon")
    add("quote", 0xDE, "Quote")
    add("backquote", 0xC0, "Backquote")
    add("comma", 0xBC, "Comma")
    add("period", 0xBE, "Period")
    add("slash", 0xBF, "Slash")
    return out


KEYS = _build()
BY_CODE = {key.code: name for name, key in KEYS.items()}
BARE_SAFE = tuple(sorted(n for n, k in KEYS.items() if k.bare_safe))


# --------------------------------------------------------------------------- #
# the closed set of actions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Action:
    """One named behaviour.

    `hold` says whether the key-up matters: a dictation is defined by the
    release, and a terminal is defined by the press alone. That is the whole
    reason this project uses a low-level hook rather than `RegisterHotKey`,
    which gives no key-up (`docs/desk-hotkeys.md` D2).
    """
    hold: bool
    what: str


ACTIONS = {
    "dictate": Action(hold=True, what="dictates"),
    "terminal": Action(hold=False, what="opens a terminal"),
}


class BindingError(ValueError):
    """A chord or a binding could not be used, and the message says why.

    Deliberately loud and deliberately NAMING the key: the owner reading it
    has just tried to bind something and needs to know which half was refused
    (AC-138). It is a `ValueError` so the route can map it to a 400 with the
    same `except ValueError` shape `reject_shell_metacharacters` already uses.
    """


# --------------------------------------------------------------------------- #
# chords
# --------------------------------------------------------------------------- #
def canonical_chord(value) -> str:
    """A chord from the page or from the file, normalised, or "" for unbound.

    Two input shapes, one output. A STRING is a chord as stored --
    `"ctrl+numpad_multiply"`. A DICT is a capture or a picker choice:
    `{"code": "NumpadMultiply", "ctrl": true}` from a browser
    `KeyboardEvent`, or `{"key": "numpad_multiply", "ctrl": true}` from the
    picker fallback. Accepting both here is what keeps the key table out of
    JavaScript (`docs/shortcuts.md` D3).

    `None` and `""` mean UNBOUND and answer `""`. That is how a chord slot is
    cleared, and it must not be an error: a row whose pi chord is removed is a
    normal edit.

    Raises `BindingError` on an unknown key, an unknown modifier, a modifier
    used as the key, or a bare key that is not bare-safe.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        return _from_event(value)
    if not isinstance(value, str):
        raise BindingError(
            f"a chord is text such as 'ctrl+numpad_multiply', or an object "
            f"carrying a key code; got {type(value).__name__}")
    parts = [p.strip().lower() for p in value.split("+") if p.strip()]
    if not parts:
        return ""
    *mod_parts, name = parts
    mods = 0
    for part in mod_parts:
        bit = MODS.get(part)
        if bit is None:
            raise BindingError(
                f"{part!r} is not a modifier; known: {sorted(MODS)}")
        mods |= bit
    return _canonical(name, mods)


def _from_event(spec: dict) -> str:
    """A browser `KeyboardEvent`, or a picker choice, as a chord.

    The page sends `code` and never `key`: `key` is layout-dependent, so
    binding on it means the same physical key produces a different binding on
    a Russian or AZERTY layout -- which is the mistake `desk/paste.py` records
    for the paste chord ("raw VK codes, never characters").
    """
    code = str(spec.get("code") or "").strip()
    name = str(spec.get("key") or "").strip().lower()
    if code:
        resolved = BY_CODE.get(code)
        if resolved is None:
            raise BindingError(
                f"this daemon cannot bind the key your browser calls "
                f"{code!r}. Pick one from the list instead.")
        name = resolved
    if not name:
        raise BindingError("name a key: send either a code or a key name")
    mods = 0
    for word, bit in (("ctrl", CTRL), ("alt", ALT), ("shift", SHIFT),
                      ("win", WIN)):
        if spec.get(word):
            mods |= bit
    # A browser reports the Windows key as `metaKey`; accepted under its own
    # name so a caller need not know which spelling this daemon prefers.
    if spec.get("meta"):
        mods |= WIN
    return _canonical(name, mods)


def _canonical(name: str, mods: int) -> str:
    key = KEYS.get(name)
    if key is None:
        if name in MODS:
            # Reachable only from a hand-written chord: `KEYS` has no modifier
            # entry, so a capture can never produce this. Named anyway,
            # because "ctrl is not a key" is the one refusal whose reason is
            # not obvious from the sentence above it.
            raise BindingError(
                f"{name!r} is a modifier and cannot be the key of a chord; "
                f"write it as {name}+<key>")
        raise BindingError(
            f"there is no bindable key called {name!r} in this daemon's key "
            f"table")
    if not mods and not key.bare_safe:
        raise BindingError(
            f"{name!r} needs at least one modifier. Binding a key suppresses "
            f"it everywhere on this machine while hotkeys are on, so only "
            f"keys almost nothing needs may be bound bare: "
            f"{', '.join(BARE_SAFE)}.")
    return "+".join([*mod_names(mods), name])


# --------------------------------------------------------------------------- #
# bindings and the table
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Binding:
    """One chord and what it does. Frozen, because the hook reads it.

    `cwd` and `agent` are the terminal action's payload and come from the row
    the chord belongs to. The desk agent never chooses either of them -- it
    forwards what its binding carries, which is what replaced "the agent names
    no directory" when the owner asked for per-row terminals
    (`docs/shortcuts.md` 4.6).
    """
    chord: str
    vk: int
    mods: int
    action: str
    hold: bool
    cwd: str = ""
    agent: str = ""
    label: str = ""

    def describe(self) -> str:
        """One clause for the install log line (AC-142)."""
        what = ACTIONS[self.action].what
        where = ""
        if self.action == "terminal":
            what = f"opens {self.agent or 'a terminal'}"
            where = f" in {self.cwd}" if self.cwd else ""
        return f"{self.chord} {what}{where}"


def parse_binding(action: str, chord, cwd: str = "", agent: str = "",
                  label: str = "") -> Binding:
    """One wire row into a `Binding`. Raises `BindingError`.

    The action is checked against the closed registry here rather than at the
    route, so a hand-edited store naming an action this agent does not have is
    refused at bind time instead of enqueueing an event nothing handles.
    """
    spec = ACTIONS.get(action)
    if spec is None:
        raise BindingError(
            f"there is no action called {action!r}; this daemon has "
            f"{sorted(ACTIONS)}")
    canonical = canonical_chord(chord)
    if not canonical:
        raise BindingError(
            f"the {action!r} binding has no chord, so nothing would ever "
            f"trigger it")
    name = canonical.split("+")[-1]
    mods = 0
    for part in canonical.split("+")[:-1]:
        mods |= MODS[part]
    return Binding(chord=canonical, vk=KEYS[name].vk, mods=mods,
                   action=action, hold=spec.hold, cwd=str(cwd or ""),
                   agent=str(agent or ""), label=str(label or ""))


class Table:
    """Every binding, parsed and checked. IMMUTABLE once built.

    This is the ONE door. There is no `add_binding` and no mutation: a new
    table is built and published whole by `hotkeys.Hook.set_table`, which is a
    single attribute assignment. That is what makes "validation at bind time"
    a complete rule rather than a rule with a back way in (AC-138).

    `keys` is a frozenset because the hook's first test runs for EVERY
    KEYSTROKE ON THE MACHINE and must be one membership check
    (`docs/shortcuts.md` 4.4).
    """

    def __init__(self, rows=()):
        bindings: list[Binding] = []
        seen: dict[tuple[int, int], Binding] = {}
        for row in rows:
            binding = parse_binding(
                action=str((row or {}).get("action") or ""),
                chord=(row or {}).get("chord"),
                cwd=(row or {}).get("cwd") or "",
                agent=(row or {}).get("agent") or "",
                label=(row or {}).get("label") or "")
            already = seen.get((binding.vk, binding.mods))
            if already is not None:
                # Refused rather than last-one-wins. Two bindings on one chord
                # means the owner sees one of them work and cannot tell which,
                # and the answer would depend on dict order in a JSON file.
                raise BindingError(
                    f"{binding.chord} is bound twice, to {already.action!r} "
                    f"and to {binding.action!r}; one chord does one thing")
            seen[(binding.vk, binding.mods)] = binding
            bindings.append(binding)
        self.bindings: tuple[Binding, ...] = tuple(bindings)
        self.keys: frozenset = frozenset(b.vk for b in bindings)
        self._by_chord = seen

    def match(self, vk: int, mods: int) -> Binding | None:
        """The binding for this EXACT chord, or None.

        Exact, never a subset: `ctrl+numpad *` must not fire when the owner
        holds Ctrl and Shift and presses it, because that is a different chord
        and may be bound to something else. A subset match also makes a bare
        binding fire under every modifier, so `numpad *` would dictate while
        the owner was mid Ctrl-chord.
        """
        return self._by_chord.get((vk, mods))

    def describe(self) -> str:
        """The install log line's body, generated from the live table.

        `deskd.py` used to hardcode "numpad * dictates", which becomes a lie
        the moment the table is configurable -- and it is the owner's only
        record of which keys this process has taken over system-wide (AC-142).
        """
        if not self.bindings:
            return "no bindings"
        return "; ".join(b.describe() for b in self.bindings)

    def __len__(self) -> int:
        return len(self.bindings)

    def __eq__(self, other) -> bool:
        return (isinstance(other, Table)
                and self.bindings == other.bindings)

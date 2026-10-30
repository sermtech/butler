"""plan.md 4.3 is a contract, so something has to READ it. D-12.

The protocol's frame list lives in a fenced block in `plan.md` section 4.3
and nothing checked it, so it drifted: `stop` shipped with AC-111 and the
block never gained the line. That is the recurring hazard here in its
purest form -- documentation nobody executes -- and the answer is not more
documentation, it is a test that goes red when the page and the code
disagree.

Both directions, inbound. Every key of `hub._handlers` is a line in the
`satellite -> daemon` block, and every line there is a key. The first half
catches a frame the daemon learned to handle and nobody wrote down; the
second catches a frame the document promises and the daemon would answer
with "unsupported message type".

Outbound, one direction fully and the other as far as the block allows.
Every `type="..."` the daemon actually sends appears in the
`daemon -> satellite` block. Going back the other way needs the block's own
"NOT implemented today" marker, because 4.3 deliberately lists the Phase 2
frames so their shape is fixed -- so the frames ABOVE that marker must be
sent, and the ones below it must NOT be. Loosening the check to "documented
frames may or may not exist" would have let the marker rot too.

Nothing in this file writes down a frame name. A list copied in here would
be a third place for the same names to drift, which is the defect being
fixed rather than a test of it.

GUARDING THE GUARD (D-13). Every parser here FAILS rather than returning an
empty set: a moved heading, a renamed fence or a reworded marker would
otherwise make all five assertions pass vacuously, and a gate that cannot
fail is worse than no gate. This does not skip the way `test_seams.py`'s
voice gate does -- `plan.md` and `daemon/` are both tracked, so their
absence is a broken checkout, not an uninstalled dependency.
"""
import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "plan.md"

# hub.py is where the protocol lives, and `settings` is broadcast from the
# settings route in butlerd.py -- so a hub-only scan would report a real,
# documented, shipped frame as missing from the code.
SENDER_FILES = [REPO / "daemon" / "hub.py", REPO / "daemon" / "butlerd.py"]

# The three ways a text frame reaches a satellite. `send_audio` is binary and
# carries no type.
SEND_METHODS = {"send", "send_for_turn", "broadcast"}

HEADING = "### 4.3"
# Exact, not a prefix: `### 4.3b` and `### 4.30` both start with `### 4.3`,
# so a renumbered section would go on matching and this gate would quietly
# read some other block (measured -- the prefix version survived that
# mutation).
HEADING_LINE = re.compile(r"^" + re.escape(HEADING) + r"(\s|$)")
FENCE = "```"
INBOUND_HEADER = "satellite -> daemon"
OUTBOUND_HEADER = "daemon -> satellite"
NOT_BUILT = "NOT implemented today"

# A documented frame line, e.g. `  {type:"final", text, turn, reason, ...}`.
# Anchored at the start so the prose in the same block ("There is no
# check_turn") is not read as a frame.
FRAME_LINE = re.compile(r'^\s*\{type:"([a-z_]+)"')


def _block() -> list[str]:
    """The lines of 4.3's fenced frame list, read from the document."""
    lines = PLAN.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if HEADING_LINE.match(ln)), None)
    if start is None:
        pytest.fail(
            f"plan.md has no '{HEADING}' heading. The protocol contract moved: "
            f"point HEADING in this file at wherever the frame list lives now, "
            f"or this gate silently stops checking anything (D-13).")
    opened = next((i for i in range(start, len(lines))
                   if lines[i].strip() == FENCE), None)
    closed = None if opened is None else next(
        (i for i in range(opened + 1, len(lines))
         if lines[i].strip() == FENCE), None)
    if closed is None:
        pytest.fail(
            f"plan.md {HEADING} has no fenced block. The frame list is inside "
            f"a ``` fence and this parser needs it; restore the fence or "
            f"update FENCE here (D-13).")
    return lines[opened + 1:closed]


def _documented() -> tuple[frozenset, frozenset, frozenset]:
    """(inbound, outbound built, outbound Phase 2) as plan.md 4.3 lists them."""
    inbound, built, phase_2 = set(), set(), set()
    where, later, marked = None, False, False
    for line in _block():
        stripped = line.strip()
        if stripped == INBOUND_HEADER:
            where, later = inbound, False
            continue
        if stripped == OUTBOUND_HEADER:
            where, later = built, False
            continue
        if NOT_BUILT in stripped:
            later = marked = True
            continue
        found = FRAME_LINE.match(line)
        if found and where is not None:
            (phase_2 if later else where).add(found.group(1))
    # Guard the guard, three ways: each assertion below reads one of these
    # sets, and an empty one makes its assertion pass no matter what the
    # daemon does. The unbuilt list is guarded only while its MARKER is
    # there: `announce` was its last frame and shipped 2026-09-26
    # (docs/automations.md AC-470), so the marker went with it -- and a
    # marker left above nothing would be a reworded-parser failure again.
    for names, what in ((inbound, INBOUND_HEADER), (built, OUTBOUND_HEADER),
                        (phase_2 if marked else {"-"},
                         f"{OUTBOUND_HEADER} after '{NOT_BUILT}'")):
        if not names:
            pytest.fail(
                f"plan.md {HEADING} lists no frames under '{what}'. Either the "
                f"block's shape changed -- a renamed direction header, a "
                f"reworded '{NOT_BUILT}' marker, a frame line that no longer "
                f"starts with {{type:\"...\" -- or the frames really are gone. "
                f"Fix the block or this parser; do not leave it returning "
                f"nothing, because every check in this file would then pass "
                f"against any daemon at all (D-13).")
    return frozenset(inbound), frozenset(built), frozenset(phase_2)


def _handled() -> frozenset:
    """Every inbound frame `hub._handlers` dispatches, read off the dict."""
    tree = ast.parse((REPO / "daemon" / "hub.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        if not any(isinstance(t, ast.Attribute) and t.attr == "_handlers"
                   for t in node.targets):
            continue
        names |= {k.value for k in node.value.keys
                  if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if not names:
        pytest.fail(
            "no `self._handlers = {...}` literal found in daemon/hub.py. The "
            "dispatch table moved or is now built at runtime; point this "
            "parser at whatever names the inbound frames, or the inbound half "
            "of this gate is checking an empty set (D-13).")
    return frozenset(names)


def _sent() -> frozenset:
    """Every `type="..."` the daemon puts on the wire, read off the calls.

    AST rather than grep, because `content_type=` and `dtype=` are both in
    these files and a text search for `type="` finds them.
    """
    names = set()
    for path in SENDER_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in SEND_METHODS):
                continue
            names |= {kw.value.value for kw in node.keywords
                      if kw.arg == "type" and isinstance(kw.value, ast.Constant)
                      and isinstance(kw.value.value, str)}
    if not names:
        pytest.fail(
            f"no frame sends found in {[p.name for p in SENDER_FILES]}. The "
            f"send helpers were renamed away from {sorted(SEND_METHODS)}, or "
            f"the type is no longer a literal keyword argument. Update "
            f"SEND_METHODS/SENDER_FILES; an empty set here makes the outbound "
            f"half of this gate vacuous (D-13).")
    return frozenset(names)


def test_every_frame_the_hub_handles_is_in_the_document():
    """A frame the daemon answers and nobody wrote down.

    This is the half that would have caught `stop`: AC-111 added the handler
    weeks before 4.3 gained the line.
    """
    inbound, _, _ = _documented()
    missing = sorted(_handled() - inbound)
    assert missing == [], (
        f"hub._handlers dispatches {missing}, which plan.md {HEADING} does not "
        f"list under '{INBOUND_HEADER}'. Add a line for each to the fenced "
        f"block naming its fields -- the block is the protocol's contract and "
        f"a satellite author reads it, not hub.py.")


def test_every_inbound_frame_in_the_document_is_handled():
    """A frame the document promises and the daemon refuses.

    `hub._dispatch` answers an unknown type with `{type:"error"}`, so this
    drift is invisible until a satellite sends the frame.
    """
    inbound, _, _ = _documented()
    unhandled = sorted(inbound - _handled())
    assert unhandled == [], (
        f"plan.md {HEADING} promises {unhandled} under '{INBOUND_HEADER}' and "
        f"hub._handlers has no entry, so the daemon answers them with "
        f"'unsupported message type'. Either implement the handler or delete "
        f"the line -- a documented frame that errors is worse than one that "
        f"was never promised (D-12).")


def test_every_frame_the_daemon_sends_is_in_the_document():
    """An outbound frame the page has to handle and the contract omits."""
    _, built, phase_2 = _documented()
    missing = sorted(_sent() - built - phase_2)
    assert missing == [], (
        f"the daemon sends {missing} and plan.md {HEADING} does not list them "
        f"under '{OUTBOUND_HEADER}'. Add a line per frame with its fields: a "
        f"satellite that does not know a frame exists cannot act on it.")


def test_every_built_outbound_frame_in_the_document_is_sent():
    """A frame the contract describes as shipped that nothing emits.

    Scoped to the lines ABOVE the block's own "NOT implemented today"
    marker, so the Phase 2 entries stay legal without loosening the rule for
    everything else.
    """
    _, built, _ = _documented()
    never = sorted(built - _sent())
    assert never == [], (
        f"plan.md {HEADING} lists {never} under '{OUTBOUND_HEADER}' as shipped "
        f"and nothing in {[p.name for p in SENDER_FILES]} sends them. Either "
        f"send them, move the line below the '{NOT_BUILT}' marker where the "
        f"Phase 2 frames live, or delete it. If the send moved to another "
        f"module, add that module to SENDER_FILES.")


def test_the_phase_2_frames_are_not_sent_yet():
    """The marker means what it says, and stops meaning it silently.

    A Phase 2 frame that starts being sent is good news, and the danger is
    that 4.3 goes on calling it unbuilt: the next reader trusts the marker
    and writes a satellite that ignores a frame the daemon really emits.
    """
    _, _, phase_2 = _documented()
    shipped = sorted(phase_2 & _sent())
    assert shipped == [], (
        f"plan.md {HEADING} marks {shipped} '{NOT_BUILT}' and the daemon sends "
        f"them. Move those lines above the marker, into the shipped part of "
        f"the '{OUTBOUND_HEADER}' block.")

"""Butler knows Butler (docs/butler-help.md, AC-641..AC-656).

The agent was told nothing about the app it runs in, so it could neither
recommend a feature nor say truthfully whether one exists. This module turns
ONE feature table (`daemon/guide_features.py`) into:

- `block()`: the always-on part (BH-1): the suggestion policy (BH-4) and one
  line per feature. STATIC, so it changes no prompt between turns (AC-643);
  sent where the memory rules go (`persona.Persona.blocks`).
- `help_text(topic, state)`: what `butler_help` answers (BH-2), with the live
  state the daemon read for this call (BH-5, AC-655).
- `TOOLS`: the one tool definition both brains are offered.

PURE: no hub, no settings store. The daemon hands in the state.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    """One thing Butler does, as the agent is told it.

    `key` is the topic `butler_help` takes. `index` is the always-on line:
    WHEN it helps, in a few words. `detail` is how to use it, in plain
    sentences naming the real button or words. `groups` are the settings
    groups (`settings.GROUPS` keys) it covers, whose owner-facing blurbs are
    appended to the detail. `tools` are the agent tools it covers. `say` is
    (matcher, phrase) pairs: a phrase the detail tells the owner to SAY, and
    the `settings` function that must recognise it (AC-653). `state` names the
    keys of the live-state dict the daemon builds that this feature reports
    (BH-5), in order."""
    key: str
    name: str
    index: str
    detail: str
    groups: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    say: tuple[tuple[str, str], ...] = ()
    state: tuple[str, ...] = ()


#: The hard cap on the always-on block (AC-642): paid on every turn.
BLOCK_CAP = 1500

#: BH-4, the owner's choice of 2026-10-08 (proactive, one at a time). ONE
#: wording for both brains; names no language (language.md D2, AC-654).
POLICY = (
    "Butler features (butler_help gives the steps and what is on now):\n"
    "- Suggest one only when it clearly saves the user effort now: at most "
    "one per reply, never mid-task, never one already on or in use.\n"
    "- Before telling the user how, call butler_help and give its steps; "
    "never guess steps.\n"
    "- If told to stop suggesting something, save that with memory_write.\n"
    "- Never claim a feature that is not listed here.")

HELP = "butler_help"

TOOLS = (
    {"type": "function", "function": {
        "name": HELP,
        "description": (
            "Ask what Butler (this app) can do: with no topic, every feature "
            "and what is on now; with a topic, how the user uses that "
            "feature, its limits and its settings. Use it before suggesting "
            "a feature, and when the user asks what Butler can do."),
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string",
                      "description": "A feature key or name from the "
                                     "Butler features list; leave out for "
                                     "all of them."},
        }, "required": []}}},
)
TOOL_NAMES = (HELP,)


def features():
    """The table. Imported here, not at module top: the table imports
    `Feature` from this module."""
    from daemon.guide_features import FEATURES
    return FEATURES


def block() -> str:
    """The always-on block (BH-1): the policy, then one line per feature.
    Holds no state, so it is byte-identical between turns (AC-643)."""
    lines = [f"- {f.name}: {f.index}" for f in features()]
    return POLICY + "\n" + "\n".join(lines)


def _find(topic: str):
    """By the feature's key or name, or by the key or label of a settings
    group it covers: "quiet hours" finds the feature that explains them."""
    from daemon import settings
    want = " ".join(str(topic).split()).casefold()
    for f in features():
        names = {f.key, f.name, *f.groups,
                 *(settings.GROUPS[g].label for g in f.groups)}
        if want in {n.casefold() for n in names}:
            return f
    return None


def _now(f: Feature, state: dict) -> str:
    """This feature's live state, its keys' answers joined; "" for none."""
    return "; ".join(state[k] for k in f.state if state.get(k))


def help_text(topic, state: dict) -> str:
    """What `butler_help` answers (BH-2): every feature with its state, or
    one feature's detail, state and settings blurbs. `state` is the dict the
    daemon read for THIS call (BH-5, AC-655)."""
    from daemon import settings
    table = features()
    if not topic or not str(topic).strip():
        out = ["Butler features and what is on now:"]
        for f in table:
            now = _now(f, state)
            out.append(f"- {f.key} ({f.name}): {f.index}"
                       + (f" Now: {now}" if now else ""))
        return "\n".join(out)
    f = _find(topic)
    if f is None:
        keys = ", ".join(x.key for x in table)
        return f"There is no Butler feature called {topic!r}. Topics: {keys}."
    out = [f"{f.name}: {f.detail}"]
    now = _now(f, state)
    if now:
        out.append(f"Now: {now}")
    for g in f.groups:
        group = settings.GROUPS[g]
        if group.blurb:
            out.append(f"Settings, {group.label}: {group.blurb}")
    return "\n".join(out)

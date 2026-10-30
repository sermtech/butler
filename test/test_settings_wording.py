"""The settings sheet's WORDS, on the daemon side (AC-810..AC-814).

The page prints whatever the schema says (AC-41), so every word a person reads
on the sheet -- a label, a choice's label, a help line, a blurb, an inactive
reason -- is the daemon's, and so is every turn-mode confirmation Butler
speaks. These tests hold those words to the owner's rulings of 2026-10-29:
the turn group headlines the mode that decides turns, every choice reads as
words, one vocabulary for turn modes everywhere, and "coding agent" rather
than "harness" in anything a person reads. Lists are read off the code
(test/AGENTS.md), never copied here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import settings  # noqa: E402
from daemon.guide_features import FEATURES  # noqa: E402
from daemon.hub import Hub  # noqa: E402

TURN = settings.BY_KEY["turn_mode"]
PATIENCE = settings.BY_KEY["patience"]


def _user_texts() -> list[tuple[str, str]]:
    """Every string a person reads on the sheet or hears as a turn-mode
    confirmation, with where it came from."""
    out = []
    for s in settings.SCHEMA:
        out.append((f"{s.key}.label", s.label))
        out.append((f"{s.key}.help", s.help))
        out += [(f"{s.key}.labels[{v}]", t) for v, t in s.labels.items()]
    for k, g in settings.GROUPS.items():
        out += [(f"group {k}.label", g.label), (f"group {k}.blurb", g.blurb)]
    for mode, (_, _, why) in settings.Settings.INERT.items():
        out.append((f"INERT[{mode}]", why))
    out += [(f"auto-language {k}", why)
            for k, why in settings.Settings.INERT_UNDER_AUTO_LANGUAGE]
    out.append(("WAKE_NEW_UNDER_ENDPOINT",
                settings.Settings.WAKE_NEW_UNDER_ENDPOINT))
    out += [(f"spoken {m}", t) for m, t in Hub.TURN_MODE_SAID.items()]
    return out


def test_the_turn_group_headlines_the_turn_mode():
    """AC-810. The signal-path strip and the phone list showed "smartturn"
    (the ADVANCED End-of-turn model) while turns really ended on a pause."""
    assert settings.GROUPS["turn"].headline == "turn_mode"


def test_every_choice_reads_as_words():
    """AC-811. A choice with declared values labels EVERY value, so no row,
    headline or strip stop shows a stored identifier. Read off the schema:
    a new unlabelled choice fails here. No exceptions today."""
    bare = sorted(s.key for s in settings.SCHEMA
                  if s.kind == "choice" and s.choices
                  and set(s.labels) != set(s.choices))
    assert not bare, f"choices shown as raw values: {bare}"


def test_the_blank_language_reads_as_words():
    """AC-811. The default "" used to print as nothing (or "none")."""
    assert settings.BY_KEY["language"].labels[""] == "Model default"


def test_one_vocabulary_for_turn_modes():
    """AC-812. Pause / Smart / Long form, and Short / Medium / Long for how
    long a pause is; "patient" is no longer both a mode and a level."""
    assert TURN.labels == {"smart": "Smart", "patient": "Pause",
                           "endword": "Long form"}
    assert PATIENCE.labels == {"quick": "Short", "balanced": "Medium",
                               "patient": "Long"}
    assert "Pause mode" in PATIENCE.help
    for mode, label in TURN.labels.items():
        assert label in TURN.help, (mode, label)
        said = Hub.TURN_MODE_SAID[mode]
        assert said.startswith(label), (mode, said)
        # A phrase that selects the mode by voice starts with its name.
        assert any(p.startswith(label.casefold())
                   for p in settings.SPOKEN_MODES[mode]), (mode, label)
    assert set(Hub.TURN_MODE_SAID) == set(TURN.choices)
    voice = next(f for f in FEATURES if f.key == "voice")
    for label in TURN.labels.values():
        assert label.casefold() in voice.detail.casefold(), label


def test_no_user_facing_word_is_an_identifier():
    """AC-813. "harness" is "coding agent" to a person; a raw turn-mode or
    endpointer value, or a snake_case key such as `voice_effect`, is never
    printed or spoken. The guide's rows are held to the same words, except
    snake_case: the guide is read by the agent and names its tools."""
    words = r"harness\w*|patient|endword|smartturn"
    raw = re.compile(rf"\b({words}|[a-z]+_[a-z_]+)\b", re.IGNORECASE)
    found = [(where, m.group(0)) for where, text in _user_texts()
             for m in raw.finditer(text)]
    guide_raw = re.compile(rf"\b({words})\b", re.IGNORECASE)
    found += [(f"guide {f.key}", m.group(0)) for f in FEATURES
              for m in guide_raw.finditer(f.name + " " + f.index + " "
                                          + f.detail)]
    assert not found, found


def test_old_brain_mode_words_are_gone():
    """AC-813. The three modes read Chat only / Chat + coding agent / Coding
    agent only, on the sheet and in the guide."""
    assert settings.BY_KEY["brain_mode"].labels == {
        "endpoint": "Chat only", "harness": "Chat + coding agent",
        "harness_only": "Coding agent only"}
    old = ("brain only", "brain + harness")
    for where, text in _user_texts() + [(f.key, f.detail) for f in FEATURES]:
        assert not any(o in text.casefold() for o in old), where


def test_everyday_help_starts_with_what_it_does_for_you():
    """AC-814. The first sentence of an everyday setting's help is what a
    family member reads before "More": no measurement, date or criterion
    number in it."""
    tech = re.compile(r"\bAC-\d|\bD-\d|\b20\d\d\b|\bmeasured\b|\d\s?(ms|MB)\b"
                      r"|\bissue\b", re.IGNORECASE)
    for s in settings.SCHEMA:
        if s.advanced or not s.help:
            continue
        first = re.split(r"(?<=[.!?])\s", s.help, maxsplit=1)[0]
        assert not tech.search(first), (s.key, first)

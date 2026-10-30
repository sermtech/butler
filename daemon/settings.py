"""Server-side preferences, described by a schema. plan.md 4.6.

Separate from modes.py on purpose. Permission modes gate what Butler is
allowed to DO, so they are asymmetric, refuse escalation by voice, and fail
closed. Everything here is a preference: how long to wait before submitting
is nobody's security problem.

What they share is where they live. Settings are on the DAEMON, never in
the browser, because a preference kept per-origin diverges the moment you
open the page on a second device -- the defect that produced four
disagreeing copies of a voice setting on one laptop (D-2, AC-8).

The schema is the contract. The API serves it, the page renders from it,
and validation reads from it, so there is exactly one place that knows a
setting exists. A form that hardcodes its own list of fields drifts from
the daemon the first time someone adds one, and the drift is invisible
until a setting silently does nothing.
"""
from __future__ import annotations

import re
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePath

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Setting:
    key: str
    default: object
    group: str
    label: str
    help: str = ""
    choices: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    kind: str = "choice"    # choice | number | bool | text | multiline |
                            # list | pair
    labels: dict = field(default_factory=dict)
    """Display text per choice: what the sheet, its headlines and the
    signal-path strip print instead of the stored value.

    It does two jobs at once (AC-66). It lets a stored value stay frozen while
    its label changes -- `brain_mode` is `endpoint` on disk and "Chat only" on
    screen, and renaming the value to match would boot an existing install
    into a mode nobody chose. And carrying labels is what lets the page render
    a SEGMENTED control instead of a dropdown without naming a setting
    (AC-41): a spec with labels is one the daemon is saying should be laid out
    as a row of named buttons.

    EVERY choice with declared values carries labels for all of them (AC-811,
    `test_settings_wording.py`), so no row shows an identifier. The page keeps
    a LONG labelled choice on a select (more values than a row of buttons
    holds; the count is the page's generic rule, naming no setting):
    `reading_face` labels its seven values so the blank "Style default" reads
    as words, and a seven-button row would be a worse dropdown.
    """
    choices_from: str = ""
    """A target whose model list fills this control (AC-30).

    The values are DISCOVERED, not declared: `GET /api/models?target=` asks
    the endpoint what it serves. Named here rather than in the page so the
    schema still says where every control gets its options, and the page
    still names nothing of its own.
    """
    lowercase: bool = True
    """Whether a `list` setting's entries are folded to lower case.

    End phrases are matched against a transcript and must be, or "Send It"
    never fires. Model ids are IDENTIFIERS -- bartowski/Ornith-1.0-9B-Q5_K_L
    lowercased is a 404 -- so the rule that makes the first work destroys
    the second. It is per setting rather than per kind for that reason.
    """
    allow_empty: bool = False
    """Whether an empty list is a legal value for a `list` setting.

    End phrases cannot be emptied -- nothing would ever submit. A favourites
    list starts empty and that is its normal state, so the rule that
    protects the first would break the second.
    """
    advanced: bool = False
    """Behind this seam's disclosure rather than in front of it.

    A per-SETTING tier, not a per-group one. Groups used to encode it --
    "Advanced: ending a turn" sat in a different part of the page from the
    turn mode it modifies -- so configuring one seam meant visiting two
    places. The tier is orthogonal to which seam a setting belongs to, and
    only one of those two is an organising idea.
    """
    favourites: bool = False
    """Whether this control offers the favourites star.

    Opt-in, because the star is generic across every `choices_from` control
    and writes the single shared `model_favourites` list. A picker that
    joined without this would paint model ids under it as clickable buttons,
    and clicking one would commit an id as the value -- a model name as a
    voice, which the engine then refuses at synthesis time. Favourites exist
    because a list of 130 is unreadable; a list of eleven voices is not.
    """
    form: str = ""
    """A `text` setting's required shape, a key into `TEXT_FORMS`, or "" for
    any text. The settings store has no time kind (docs/proactive.md PR-14),
    so `quiet_start` is text that must be `hhmm`, refused at write. On a
    `list` setting it is checked against every entry."""
    restart: bool = False
    """True when the value is read once at construction and a change is
    applied by restarting the daemon, which it does BY ITSELF as soon as
    nothing is running (`apply_settings.Applier`, AC-715).

    It MUST be shown in the UI, and `Settings.pending` says which of these
    the running daemon has not applied yet (AC-716). A setting that saves,
    reports success, and changes nothing with nobody saying so is worse than
    one that is not editable at all -- the user concludes the whole panel is
    fake. A construction-time engine cheap enough to rebuild and warm between
    turns is swapped live instead and carries no flag (AC-713).
    """
    quick: str = ""
    """The word this setting goes by under the chat input, or "" to stay in
    the settings surface only.

    The owner's backlog item 5: the model actually answering, the coding agent,
    the model that looks at images, and whether that agent may write, are the
    four choices made while USING Butler rather than while configuring it, so
    they belong against the text box.

    A STRING and not a bool, because it does two jobs at once -- the same
    argument `labels` makes one field up. It marks the setting, and it carries
    the short word the row prints: "Conversation model" does not fit on a pill
    beside three others on a 390 px screen, and a page that shortened the label
    itself would be naming a setting, which AC-41 forbids. The full label is
    still what the control announces to a screen reader.

    A quick setting must not be restart-flagged, checked in
    `_validated_settings` for the reason `Group.quick` is checked in
    `_validated_groups`: that row sits against the composer with no restart
    badge and no commit bar anywhere near it.

    It is PER SETTING where the appearance pill's flag is per GROUP, and that
    is forced rather than chosen. These four live in `brain` and `harness`
    beside `brain_mode`, which IS restart-flagged, so marking either group
    would be refused at import -- correctly, because the whole group is not
    what the owner asked to reach.
    """
    file: str = ""
    """The FILE a `multiline` setting's value lives in, or "" for a value kept
    in `settings.json` like every other.

    docs/identity-memory.md 11.3 (AC-426). `SOUL.md` and `USER.md` are the
    owner's files in the persona folder (`paths.Paths.persona`), editable by
    hand AND on the settings page. The value IS the file's body -- the file
    minus its leading instruction comment -- so the page renders a textarea
    from `kind` as it would for any prose setting and names no file (AC-41).
    The store reads and writes through the objects handed to `Settings` as
    `files`, and never puts the value in `settings.json`, because two copies
    of a persona drift and the drifted one is the one a model reads.
    """
    preview: bool = False
    """Whether the page draws this choice's values as preview cards rather
    than as buttons or a dropdown (docs/lantern-ui.md AC-704).

    The page may not name the setting it draws as cards (AC-41), so the daemon
    marks it. A card shows a choice by name, so the flag is legal only on a
    `choice` whose `labels` name every value, checked in `_validated_settings`.
    """


def _s(*a, **kw) -> Setting:
    return Setting(*a, **kw)


def _adv(*a, **kw) -> Setting:
    """An advanced setting: fully real and editable, one disclosure away.

    A separate constructor rather than `advanced=True` on every call, because
    this schema is read far more often than it is written and the tier should
    be visible at a glance down the left margin.
    """
    return Setting(*a, advanced=True, **kw)


# The streaming-latency presets belong to the ENGINE, so the picker is filled
# from the engine's catalogue rather than from a tuple written here -- the
# same rule the voice list was rewritten under (AC-42). The tuple that used to
# sit in the schema offered `low`, `balanced` and `high`, and no preset has
# ever been called `high`: `stt_engine._family_ext` looked it up with a
# default of `balanced`, so choosing it saved, reported success and changed
# nothing, while `accurate` -- a real preset, cheaper per second of audio AND
# more accurate -- could not be reached from the page at all. That is AC-7b
# by the front door, and a list living in the daemon is how it got there.
#
# Read from the JSON rather than through `stt_engine.catalog_public()`,
# because importing that module imports `transcribe_cpp`, which enumerates
# Vulkan devices and loads native backends: a quarter of a second and four
# lines of driver noise that every daemon and every test would pay to draw one
# dropdown, which is why every other caller imports it lazily
# (`daemon/adapters/asr_transcribecpp.py::_stt_engine`). The file IS the
# catalogue and that accessor is a view of it; `test/test_asr_latency.py`
# holds the two against each other so the shortcut cannot become a second
# copy.
_CATALOG_PATH = Path(__file__).resolve().parent / "engines" / "catalog.json"


@dataclass(frozen=True)
class WakeWord:
    """One wake word the page can load (`docs/wake-words.md` section 2).

    `url` is the model file RELATIVE TO THE PAGE, so the page names no model
    (AC-41). Its setting is `wake_<id>` and holds a role from `WAKE_ROLES`;
    `default` is that setting's default."""
    id: str
    phrase: str
    url: str
    default: str


# THE CATALOGUE OF WAKE WORDS (AC-803), in the order the page lists them. The
# settings rows below, the `ready.wake` field and the `wake` broadcast
# (`Hub.wake_frame`), and the check on `wake_candidate.word` all read this.
WAKE_WORDS: tuple[WakeWord, ...] = (
    WakeWord("hey_butler", "hey butler", "models/hey_butler.onnx", "screen"),
    WakeWord("hey_jarvis", "hey jarvis", "models/hey_jarvis_v0.1.onnx", "off"),
)
WAKE_ROLES = {"off": "Off", "screen": "Conversation on screen",
              "new": "New conversation"}


def wake_key(word_id: str) -> str:
    return f"wake_{word_id}"


def _wake_settings() -> tuple[Setting, ...]:
    return tuple(
        _s(wake_key(w.id), w.default, "listen", w.phrase.title(),
           choices=tuple(WAKE_ROLES), labels=dict(WAKE_ROLES),
           help=(f"What saying \"{w.phrase}\" does. Conversation on screen "
                 f"talks to the conversation the device shows. New "
                 f"conversation starts a new one in that conversation's "
                 f"project first. Off stops listening for it; at least one "
                 f"wake word must stay on."))
        for w in WAKE_WORDS)


def _latency_presets() -> tuple[str, ...]:
    """The engine's streaming presets, in the order the catalogue lists them.

    `_note` is documentation for whoever opens the file rather than a preset,
    and the engine's own `catalog_public()` drops it the same way.

    Fails at IMPORT if the catalogue is missing or malformed, like
    `_validated_groups` below and unlike everything that reads
    `settings.json`: the usual rule -- a preference is never worth refusing to
    boot over -- is about a VALUE on disk. This is the shipped schema being
    broken, which no restart fixes and which otherwise ships as a picker with
    nothing in it.
    """
    presets = json.loads(
        _CATALOG_PATH.read_text(encoding="utf-8"))["latency_presets"]
    return tuple(k for k in presets if k != "_note")


# The one value of `language` that is NOT a locale. Butler's own word, not an
# engine's: it means "decide each turn from what I say" (AC-207). Named here
# because `hub.py` compares against it and a second spelling of it would be a
# setting that silently stopped working.
AUTO_LANGUAGE = "auto"


def _latency_labels() -> dict[str, str]:
    """Each streaming preset's words, from the catalogue's own `label` minus
    its (left, chunk, right) tuple: "best accuracy (70,13,13)" reads "Best
    accuracy" (AC-811). Derived, so a preset the engine adds is labelled
    without an edit here."""
    presets = json.loads(
        _CATALOG_PATH.read_text(encoding="utf-8"))["latency_presets"]
    return {k: presets[k]["label"].split(" (")[0].capitalize()
            for k in _latency_presets()}


def _language_labels() -> dict[str, str]:
    """`language`'s labels (AC-811): the blank and `auto` as words, every
    locale code as itself -- derived from the choices, never a second list."""
    return {"": "Model default", AUTO_LANGUAGE: "Automatic",
            **{c: c for c in _catalog_languages()[2:]}}


def _catalog_languages() -> tuple[str, ...]:
    """Every locale the shipped recognizers claim, blank first (AC-153).

    The same rule as `_latency_presets` above and for the same reason: a
    LANGUAGE list is the ENGINE's, so it is read from the catalogue rather
    than written here. A tuple in this file would be wrong the day a model is
    added, and wrong invisibly -- which is how `asr_latency` came to offer a
    preset called `high` that no preset has ever been called (AC-42, AC-7b).

    A UNION across models rather than the current model's own list, because
    the schema is static and `asr_model` is a separate choice: an owner who
    has not switched models yet still has to be able to see what switching
    would buy. Picking a locale the LOADED model does not speak is refused by
    the engine at the start of the turn -- measured, `unsupported language
    (status 10)` -- rather than silently ignored, which is the honest place
    for that refusal to live.

    Blank leads because it is the default and it means "whatever this model
    speaks by default", the same way a blank `voice` does.

    `auto` follows it and is the ONE entry that is not read from the
    catalogue, which is a deliberate exception to the paragraph above rather
    than a hole in it (AC-207). AC-153's rule is that the list of LOCALES is
    the engine's, and `auto` is not a locale -- no engine offers it, the
    recognizer rejects the literal string outright, and it is Butler's own
    instruction to decide the language from what the owner said. Every other
    entry is still the engine's, so the rule holds where it applies.

    Fails at IMPORT if the catalogue is missing or malformed, like
    `_latency_presets`: this is the shipped schema being broken, not a value
    on disk, and it would otherwise ship as a picker with nothing in it.
    """
    models = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))["models"]
    every = {lang for m in models for lang in m.get("languages", ())}
    return ("", AUTO_LANGUAGE) + tuple(sorted(every))


SCHEMA: tuple[Setting, ...] = (
    # -- everyday ---------------------------------------------------------- #
    # AC-812: ONE vocabulary for the three modes -- Pause, Smart, Long form --
    # on the sheet, in the spoken phrases (`SPOKEN_MODES`), in Butler's spoken
    # confirmation (`Hub.TURN_MODE_SAID`) and in the guide. The stored values
    # are frozen; `patient` the MODE reads "Pause" so it is no longer also a
    # patience level.
    _s("turn_mode", "patient", "turn", "Turn taking",
       help="How Butler knows you have finished speaking. Pause answers "
            "once you stop talking for a moment (how long is Pause length, "
            "below). Smart listens for a finished sentence, so a thinking "
            "pause does not cut you off. Long form waits until you say an "
            "end phrase, for talking at length. You can also say \"pause "
            "mode\", \"smart turn\" or \"long form\".",
       choices=("smart", "patient", "endword"),
       labels={"smart": "Smart", "patient": "Pause",
               "endword": "Long form"}),
    _s("patience", "balanced", "turn", "Pause length",
       help="In Pause mode, how long you have to stay quiet before Butler "
            "answers. Short answers soonest; Long gives you time to think "
            "mid-sentence.",
       choices=("quick", "balanced", "patient"),
       labels={"quick": "Short", "balanced": "Medium", "patient": "Long"}),
    _s("end_phrases", ["over to you", "send it", "go ahead", "over"],
       "turn", "End phrases", kind="list",
       help="In Long form mode, what you say to hand over: nothing is sent "
            "until you say one of these and then pause. Deliberately "
            "awkward: a natural phrase would send your half-finished "
            "thought the first time you said it in passing."),
    # AC-110. First in the group because it decides whether anything else in
    # it is audible. Separate from `cues` below and staying that way: the
    # tones say the microphone is alive, and losing them along with the answer
    # would make a muted Butler and a dead one sound the same (AC-7b). A
    # DAEMON setting, so every device goes quiet at once -- per-device mute is
    # the acknowledged right end state and is deliberately deferred (owner's
    # ruling D3 = A, docs/voice-control.md 3).
    _s("speak", True, "tts", "Speak replies", kind="bool",
       help="Whether Butler reads its replies aloud. Off answers in text "
            "only: the reply still appears in the "
            "transcript and is still remembered, and nothing is read aloud. "
            "The tones below are a separate setting and keep working, so a "
            "silent Butler still sounds different from a broken one. Takes "
            "effect on the next reply."),
    _s("voice", "", "tts", "Voice", kind="text",
       choices_from="tts_voices",
       help="The voice Butler speaks in. Blank means the speech engine's "
            "own default voice. The "
            "list comes from the engine itself, so it is right for whichever "
            "one is loaded. Takes effect on the next reply; the first use of "
            "a new voice costs a sidecar start."),
    # AC-107. The range is NARROW on purpose, and it is the third of the three
    # options docs/voice-control.md 6 lists -- bound the speed so the drift
    # cannot matter. `speech.py` caps a synthesis segment in WORDS
    # (FIRST_SEGMENT_MAX_WORDS, LATER_SEGMENT_MAX_WORDS) and those caps exist
    # to bound MILLISECONDS: time to first audio (AC-4) and how long an
    # interrupted segment keeps playing (AC-5). Speed is the constant in the
    # words-to-seconds conversion, so a generous range would stretch two time
    # budgets with nothing saying so. At 0.8 the worst case is 25% over the
    # budget those caps were sized for, which is inside the slack they already
    # carry; 0.5 would be double it. Widening this means restating AC-4 and
    # AC-5 or scaling the caps, and neither is free.
    _s("tts_speed", 1.0, "tts", "Speaking speed", kind="number",
       minimum=0.8, maximum=1.3,
       help="How fast Butler talks. 1.0 is the engine's own rate. The range "
            "is deliberately narrow: how much of a reply you lose when you "
            "interrupt is budgeted in words, and speed is what turns words "
            "into seconds. Takes effect on the next reply."),
    _s("cues", True, "tts", "Audible cues", kind="bool",
       help="The tones for heard-you, about-to-submit, and taken."),
    _s("cue_volume", 0.13, "tts", "Cue volume", kind="number",
       minimum=0.0, maximum=1.0),

    # -- look ---------------------------------------------------------------- #
    # On the daemon rather than in the browser, for the same reason as every
    # other preference here (D-2, AC-8). These are the ones it is most
    # tempting to keep locally -- they are per-device taste, they change
    # nothing about behaviour, and localStorage is one line -- which is
    # exactly why they are written down. The satellite page is served on
    # several origins and carried between rooms; a look that disagrees per
    # origin is the four-copies defect in a smaller hat, and the grep gate
    # over the shipped client files would fail anyway.
    #
    # AC-678, AC-704 (docs/lantern-ui.md CU-2). The style is FIRST in this
    # group because Appearance's first row is the style; `preview` is what
    # tells the page to draw its choices as cards without naming this setting
    # (AC-41). Lantern is the default so an install that never opens the
    # control gets the approved look. NOT restart-flagged: the page writes
    # `data-style` on `<html>` on every settings read. Each style owns its
    # surfaces AND its state colours (CU-4), which is why `colour_scheme`
    # retired: see the note where it used to be, below `text_scale`.
    _s("ui_style", "lantern", "look", "Style",
       choices=("lantern", "caption"),
       labels={"lantern": "Lantern", "caption": "Caption"}, preview=True,
       help="How the whole page is drawn, the same on every device. Lantern "
            "is warm, and Butler's replies are set in a book-like serif. "
            "Caption is cool, with one plain typeface throughout. Takes "
            "effect at once."),
    # `labels` here is what makes this BUTTONS rather than a dropdown, and
    # that is not cosmetic: since the header pill became the appearance surface
    # (the `quick` flag on this group), a select would have made switching
    # theme two gestures where it used to be one. The labels match the values
    # because these two values already read as English -- what the labels buy
    # is the layout, which is the daemon's call to make (AC-66, AC-41).
    #
    # `auto` (docs/lantern-ui.md CU-6) follows the device's own light or dark
    # preference and changes with it while the page is open. Dark stays the
    # default so no page changes on upgrade.
    _s("theme", "dark", "look", "Theme", choices=("dark", "light", "auto"),
       labels={"dark": "dark", "light": "light", "auto": "auto"},
       help="Dark is the default. Light is for a bright room. Auto follows "
            "this device's own light or dark setting, and changes with it. "
            "Every theme honours the system contrast and motion "
            "preferences."),
    # AC-167 (docs/familiar-ui.md R3, step 4). WHERE the wave is drawn. The
    # DAEMON names the four placements because nothing in `web/` may (AC-41),
    # and the owner decides by looking rather than by reading.
    #
    # `off` stops the DRAWING and never the CALLS: all seven `viz.*` call
    # sites stay in `app.js` or `test_visualiser_contract.py` goes red. That
    # is the one thing about this control that is easy to get wrong from the
    # page's side, so it is written where the choice is declared.
    #
    # `band` RETIRED with the wave band (docs/lantern-ui.md CU-3): the
    # composer's own border is the voice now, so the placements are the mic
    # button, the full page and off. A stored "band" fails validation in
    # `_load` and falls back to the default with its one "ignoring bad
    # setting" line. `mic` is the default: the lattice's ring stays on the
    # control it came from. NOT restart-flagged: the page re-reads it on
    # every change, exactly like `theme` below it.
    _s("viz_placement", "mic", "look", "Wave placement",
       choices=("mic", "ambient", "off"),
       labels={"mic": "Mic button", "ambient": "Full page", "off": "Off"},
       help="Where the Lattice wave is drawn: only used when Voice effect, "
            "below, is Lattice. Mic button keeps it inside the microphone "
            "button, Full page also fills the space behind what you are "
            "reading, and Off draws nothing. Off stops only the drawing -- "
            "your voice is still followed, so turning it back on costs "
            "nothing. With any other effect the message box decides its own "
            "placement. Takes effect at once."),
    # AC-252, AC-262 (docs/voice-effects.md, section 1, option A). ONE
    # control answers "what is drawn" rather than two that could disagree --
    # `voice_effect` and `viz_placement` both want the same canvas, and
    # section 1 rejected letting them collide silently. Every value past
    # `lattice` is a treatment born at the message box's perimeter and drawn
    # OUTWARD, with the inside of the field left clean, so what changes
    # while Butler hears you or answers is always visible around the box you
    # are looking at rather than somewhere else on the page. `off` draws
    # nothing, exactly like `viz_placement`'s own `off`.
    #
    # The default is `lantern` (docs/lantern-ui.md CU-3, AC-690, reversing
    # the 2026-09-16 "lattice stays the default"): the message box's own
    # border lights from the level, blue while Butler hears and amber while
    # it thinks or speaks. CSS only, from the same `--fx-level` the other
    # treatments read; `lattice` stays a choice. NOT restart-flagged,
    # for `viz_placement`'s own reason: the page re-reads it on every
    # change.
    # `aurora` was CUT, not tuned (RULING 2, CLAUDE.md 2026-09-16 backlog):
    # rendered evidence showed it as two or three faint vertical smudges
    # above the composer at every level from 0.17 to 0.99, which is a
    # rendering artefact rather than light -- a shape problem, not a
    # brightness one. Ten choices, not eleven.
    _s("voice_effect", "lantern", "look", "Voice effect",
       choices=("off", "lantern", "lattice", "mist", "ember", "bloom",
                "ribbon", "sonar", "filaments", "comet", "dust"),
       labels={"off": "Off", "lantern": "Lantern", "lattice": "Lattice",
               "mist": "Mist", "ember": "Ember", "bloom": "Bloom",
               "ribbon": "Ribbon", "sonar": "Sonar",
               "filaments": "Filaments", "comet": "Comet", "dust": "Dust"},
       help="What the message box does while Butler hears you and while it "
            "answers. Lantern (the default) lights the box's own border, "
            "blue while Butler hears you and amber while it thinks or "
            "speaks; Lattice is a wave, placed by Wave placement; Off draws "
            "nothing; the rest are other looks to try. Every effect starts "
            "at the edge of the box and is drawn outward, so the inside stays "
            "clean for typing. Takes effect at once."),
    # AC-173 (docs/familiar-ui.md R5, step 6). The reading face is the
    # owner's. Six families are vendored under `web/fonts/` and declared in
    # `web/fonts.css`, because a satellite has to render with no internet at
    # all -- nothing is fetched from a CDN, and a browser downloads only the
    # family something is actually set in.
    #
    # The VALUES are the CSS family names, so the page sets what it is given
    # and holds no list of its own (AC-41). A table in `web/` mapping a
    # daemon key to a family would be the second copy that drifts, and the
    # drifted copy is the one that names a face nothing can draw (AC-42).
    # `test_seams.py` holds these against `fonts.css` for that reason.
    #
    # "" is "Style default" (docs/lantern-ui.md CU-5): first and the
    # default, it is the one value that is NOT a family. The page writes no
    # face for it, so the style's own reading face (`--read`) stands; any
    # other value replaces it for Butler's replies. The interface text is the
    # style's (`--ui`) either way.
    #
    # EVERY value is labelled, because a "" with no label is a blank entry
    # (AC-66: every label or none). Seven values is more than the page lays
    # out as buttons, so it stays a dropdown, now showing the labels.
    _s("reading_face", "", "look", "Reading face",
       choices=("", "IBM Plex Sans", "Figtree", "Plus Jakarta Sans", "Inter",
                "Nunito Sans", "Atkinson Hyperlegible"),
       labels={"": "Style default", "IBM Plex Sans": "IBM Plex Sans",
               "Figtree": "Figtree", "Plus Jakarta Sans": "Plus Jakarta Sans",
               "Inter": "Inter", "Nunito Sans": "Nunito Sans",
               "Atkinson Hyperlegible": "Atkinson Hyperlegible"},
       help="The typeface Butler's replies are set in. Style default is the "
            "style's own: a book-like serif in Lantern, Inter in Caption. "
            "Figtree, Plus Jakarta Sans and Nunito Sans are friendlier and "
            "rounder, Inter is the neutral one, and Atkinson Hyperlegible "
            "was drawn by the Braille Institute for legibility -- it ships "
            "in two weights only, so other weights are approximated in it. "
            "All of them are on this machine already and nothing is "
            "downloaded. Takes effect at once."),
    # R7 (CLAUDE.md rulings of 2026-09-16), reversing `web/index.html`'s own
    # former comment that the type scale was "baked in, not offered". The
    # owner's ask was "a little larger... for older users"; the cap is
    # DELIBERATE and not derived -- 1.15 is what the page's roughly fifty
    # fixed-px control heights (44px buttons, `.chatrow`, `.act`, settings
    # rows) hold before an edge clips, and moving those to `em` first (the
    # root-cause fix, and the WCAG 200%-zoom failure it would also close) was
    # declined for THIS pass, not declined outright.
    #
    # The VALUE multiplies ONE custom property, `--scale`, on `:root`, and
    # every font-size in `web/index.html` is written as `calc(var(--scale) *
    # Npx)` rather than a second px table (AC-41): the page names no size,
    # exactly like `reading_face` names no family of its own. `test_seams.py`
    # holds every font-size against `var(--scale)` for that reason. NOT
    # restart-flagged, for `reading_face`'s own reason: the page writes the
    # property on every settings read rather than on boot.
    #
    # Default is 1.08, not the cap: "a little larger" than what shipped, so
    # nobody who never opens this control is handed the untested largest size.
    _s("text_scale", "1.08", "look", "Text size",
       choices=("1.0", "1.08", "1.15"),
       labels={"1.0": "Standard", "1.08": "Larger", "1.15": "Largest"},
       help="How large this page's own text is. Larger is a little bigger "
            "than what shipped before this setting existed. Largest is the "
            "most the page's fixed-size buttons and rows can hold before an "
            "edge clips -- there is no larger choice because the boxes "
            "around the text do not grow with it yet. Takes effect at once."),
    # `colour_scheme` used to live HERE and is RETIRED (docs/lantern-ui.md
    # CU-4). Its five schemes repainted SURFACES only and were barred from the
    # state colours (AC-174); each style now owns its surfaces AND its state
    # colours (AC-680), so the schemes folded into the two styles. Like
    # `mic_style` below it has no successor to carry a value to (a scheme is
    # not a style), so it gets no `RENAMES` entry: `_load`'s unknown-key drop
    # is the whole migration, and it says so once on the next boot.
    # `mic_style` used to live HERE and is RETIRED (docs/familiar-ui.md 6.4,
    # step 7). Five microphone treatments -- orb, ring, glyph, capsule, arc
    # -- existed because the mic was the centrepiece of the page; in the
    # composer it is a 40 px button beside a text field and four of the five
    # have nowhere to be. The cost, stated rather than buried: the owner
    # picked `arc`, and that choice is being taken away.
    #
    # It gets no `RENAMES` entry, and that is the one difference from
    # `asr_language`: a rename carries a VALUE to a SUCCESSOR key, and this
    # setting has none -- `viz_placement` above answers a different question
    # and no mic treatment is a legal value of it. `_load`'s unknown-key drop
    # is the whole migration, and it is loud: a stored `arc` is announced as
    # ignored on the next boot rather than sitting in the file looking like
    # it still does something.

    # -- the brain ---------------------------------------------------------- #
    # `router_role` (4.4) is still deliberately ABSENT: a control that saves,
    # reports success and changes nothing is the AC-7b failure, and shipping a
    # switch before the thing it switches is the most inviting way to commit
    # it. `brain_mode` landed with a spoken grant that gated its unsafe
    # option; the owner retired that grant on 2026-08-18 (AC-29), so the help
    # text below has to say plainly what choosing `harness` now means.
    # Restart-flagged because the brain composite is wired through half of
    # `build_hub` (persona rules, the conversation store, the stop hook): a
    # live swap would have to redo all of it, and a wrong one is silent. The
    # daemon restarts itself to apply it once nothing is running (AC-715).
    # Three modes, and the VALUES are frozen while the labels are not. A
    # settings.json on disk already says `endpoint` or `harness`, and renaming
    # a stored value to make a control read better is how an existing install
    # boots into a mode nobody chose. `labels` is the page's problem and
    # `choices` is the daemon's (AC-66).
    _s("brain_mode", "endpoint", "brain", "Brain mode",
       choices=("endpoint", "harness", "harness_only"), restart=True,
       labels={"endpoint": "Chat only",
               "harness": "Chat + coding agent",
               "harness_only": "Coding agent only"},
       help="What answers you. Chat only talks and does nothing else. Chat + "
            "coding agent talks, and hands real work (files, code, commands) "
            "to a coding agent. Coding agent only sends everything to the "
            "agent, so even \"what time is it\" costs a full agent turn. "
            "Whenever a coding agent answers it works at full capability in "
            "its folder: it reads, writes and runs commands without asking, "
            "in every permission mode. In both Chat modes a small model "
            "(the router, below) reads each request first; Coding agent only "
            "has none. A change "
            "restarts Butler by itself as soon as nothing is running."),
    _s("router_model", {"conn": "", "model": ""}, "brain", "Router model",
       kind="pair", choices_from="connections", favourites=True,
       help="The small always-warm model that classifies each utterance. "
            "Blank means the endpoint's own default. Keep it small: this one "
            "runs on every single thing you say."),
    _s("endpoint_model", {"conn": "", "model": ""}, "brain",
       "Conversation model", kind="pair", choices_from="connections",
       favourites=True, quick="Brain",
       help="The model that talks with you in Chat modes: it answers "
            "anything the router passes on. Blank means the connection's own "
            "default."),
    # Two caps and not one, because a verdict and a reply are different
    # animals (docs/architecture-seams.md 4.1). The verdict is schema-bound
    # JSON well under 100 tokens and its cap exists to bound a runaway or a
    # reasoning model; the reply is open prose and its cap bounds spend. One
    # knob sized for the reply does nothing for the router, and one sized for
    # the router silences every substantive answer.
    #
    # Both default 0 -- send no cap at all -- for the same reason Butler ships
    # no default model: a shipped number is wrong on the first model that
    # reasons, and it silently outranks a choice the owner already made.
    _adv("router_max_tokens", 0, "brain", "Router token cap", kind="number",
         minimum=0, maximum=32768,
         help="Most tokens the router may generate for one verdict. 0 sends "
              "no cap. A reasoning model can spend an entire small cap "
              "thinking and return an empty verdict; that turn then goes to "
              "the conversation model and the log says why."),
    _adv("reply_max_tokens", 0, "brain", "Reply token cap", kind="number",
         minimum=0, maximum=32768,
         help="Most tokens one spoken reply may generate. 0 sends no cap. "
              "This bounds what the model may produce, not what is read "
              "aloud -- a reply cut off mid-sentence is shown on screen "
              "rather than spoken."),
    # Per role, and OFF means "tell the model to skip its reasoning block".
    # A separate control for each role, like the caps, because a reasoning
    # model is right for one role and wrong for the other. Measured on
    # Ornith-1.0-9B [2026-08-18]: with reasoning ON a spoken reply's first word
    # arrives 30-68 s after the question and the router returns an EMPTY
    # verdict, both on a free GPU; OFF, the reply starts in 0.2 s and the
    # router answers valid JSON in 0.5 s. Default True -- send NOTHING -- so a
    # cloud connection that would reject the kwarg is untouched until the owner
    # who runs a local reasoning model turns it off. The signal is the
    # Qwen-family chat_template_kwargs enable_thinking=false; other families
    # ignore it, which is why this is opt-in and not a global default.
    #
    # EVERYDAY, not advanced, unlike the caps they sit beside: the wrong value
    # here is the empty-verdict failure the help text warns about, and a UI
    # review found the pair buried three disclosures deep for a control that
    # decides whether a local reasoning model works at all. High consequence
    # earns the top of the section.
    # AC-200. The attachment setting lives in `brain` because it answers
    # "what does the model get" (docs/attachments.md 3). `vision_model`
    # used to live HERE and is RETIRED (AC-815, owner 2026-10-29): the model
    # that answers sees the image itself. No successor, so no `RENAMES`
    # entry: `_load`'s unknown-key drop is the whole migration, said once.
    _s("document_reading", "text_then_vision", "brain", "Reading documents",
       choices=("text", "text_then_vision", "vision"),
       labels={"text": "read the text",
               "text_then_vision": "read it, then look",
               "vision": "look at it"},
       help="What Butler does with a PDF or a Word document you attach. "
            "Reading the text happens on this machine, needs no model and no "
            "network, and is right for almost every document that was typed "
            "rather than scanned. 'read it, then look' shows the file to the "
            "model when there is no text in it -- a scan, or a page of "
            "diagrams. 'look at it' never reads, which costs far more. "
            "Looking needs a model that can see."),
    _s("router_reasoning", True, "brain", "Router reasoning", kind="bool",
       help="Whether the router may think before deciding. On leaves the "
            "model's own default. Off tells a reasoning model "
            "to skip thinking, so the verdict is fast -- a reasoning router "
            "can spend its whole budget thinking and return an empty "
            "verdict. Off is for local Qwen-family models; leave it on for "
            "a connection that would reject the request."),
    _s("reply_reasoning", True, "brain", "Reply reasoning", kind="bool",
       help="Whether the conversation model may think before it answers. "
            "On leaves the model's own default. Off tells a reasoning model "
            "to skip thinking, so the spoken reply begins at once instead of "
            "after a long silent think. Off is for local Qwen-family models; "
            "leave it on for a connection that would reject the request."),
    _adv("model_favourites", [], "brain", "Favourite models", kind="list",
       allow_empty=True, lowercase=False,
       help="Shown first in every model picker. A list of 130 is a list "
            "nobody reads."),

    # -- the harness (docs/harness-mode.md 7.4) ----------------------------- #
    # Its own group, between the brain and speech, because it is the fifth
    # seam and a seam gets one home (AC-45, plan.md 4.6a). `brain_mode` lives
    # in the BRAIN group rather than here: it is the choice of what answers,
    # and these three are how the thing it may choose is configured.
    # NOT restart-flagged, and that is a consequence of AC-60 rather than a
    # preference: these three are fields of `HarnessSpec`, which is resolved
    # per turn and compared against the running child, so changing one
    # replaces that child on the next thing said. `brain_mode` keeps its flag,
    # because the Brain composite is still built once at boot.
    _s("harness", "pi", "harness", "Default coding agent", kind="text",
       choices_from="harnesses",
       help="Which coding agent a NEW chat runs on: pi, Claude Code "
            "(claude) or opencode. Each chat keeps the agent it started with, "
            "so changing this does not move an existing chat."),
    # docs/agent-and-terminal-settings.md SW-1: which coding agents a chat
    # may run on. The default agent (`harness`) is always on as well, so the
    # default can never point at a switched-off agent (SW-4 by construction).
    _s("chat_agents", ["pi"], "harness", "Chat agents", kind="list",
       lowercase=True, choices_from="harnesses",
       help="The coding agents a new chat may start on. With one, no choice "
            "is shown. The default coding agent is always available."),
    # docs/chat-model.md CM-1, CM-D2: the DEFAULT for new chats on the
    # default agent; a chat's own model is picked in the composer's chip,
    # which replaced this setting's quick chip.
    _s("harness_model", "", "harness", "Default model", kind="text",
       choices_from="harness_models", favourites=True,
       help="The model a new chat on the default coding agent starts with; "
            "a chat can pick its own in the message box. Blank means the "
            "agent's own default. Pick a fast one: this choice decides "
            "whether the coding agent feels usable. The list comes from the "
            "agent's own registry, not from a connection. Measured on the "
            "same question, a bridged cloud model answered in 1.55 s and a "
            "local 4B took about 27 s."),
    # `projects` IS RETIRED (the owner's backlog item 4, 2026-09-11). It was a
    # second list of directories beside the shortcuts pane's rows, and the two
    # had diverged completely on the owner's own machine: the setting held one
    # directory that was not a row, and all three rows were unreachable by
    # voice. `docs/shortcuts.md` D4 predicted that drift in writing. The rows
    # are the one table now -- `ShortcutStore.paths()` -- and both of AC-31's
    # gates read it. Existing values become rows once, in
    # `ShortcutStore.migrate_projects`, which reads this FILE rather than the
    # store because `_load` drops a key the schema no longer knows.
    _s("harness_cwd", "", "harness", "Working directory", kind="text",
       help="Where the agent is started, and therefore what it can see. "
            "Blank defaults to a scratch dir (TMP) inside Butler's own "
            "folder, so there is always somewhere harmless to run; it never "
            "guesses a named project. Takes effect on the next thing you "
            "say: a working directory is fixed when the agent starts, so "
            "changing this replaces the one that is running."),
    # A plain persistent knob, NOT the spoken grant AC-29 retired: no ceremony,
    # no permission-mode coupling, no repeat-back. Default read_only so a
    # wake-word false accept cannot make a built-in write or run bash; the
    # owner flips it to full for real work (owner's decision 2026-08-18). Read
    # per message like the rest of this group, so it is not restart-flagged.
    # `labels` for the same reason `brain_mode` carries them: the VALUES are
    # identifiers and the page lays a labelled choice out as named buttons
    # (AC-66). It is what makes this a two-button segment under the composer
    # rather than a dropdown of identifiers nobody reads.
    _s("harness_tools", "read_only", "harness", "Agent tools", quick="Tools",
       choices=("read_only", "full"),
       labels={"read_only": "Read only", "full": "Full"},
       help="Whether the coding agent may change things. Read only lets it "
            "look at files but not edit them or run commands, so a wake word "
            "heard by mistake cannot make it write anything. Full lets it "
            "edit, write and run commands, and use its skills (web search "
            "and the like). Applies from the next message. In detail: Read "
            "only keeps pi's read tools (read, grep, find, ls) and drops its "
            "built-in bash, edit and write; an installed pi extension can "
            "still run commands under Read only (measured 2026-08-18), "
            "because it bounds pi's built-in tools, not its extensions."),
    # THE CAP ON PARALLEL AGENTS (AC-237, docs/parallel-agents.md D3 and phase
    # 5). Ten is the owner's own number and it is a SETTING rather than a
    # constant because they asked for it to be theirs. NOT restart-flagged, for
    # the reason every other member of this group is not: it is read at the
    # moment a child is about to be spawned (`_Host.ensure`), so a change
    # reaches the next thing said.
    #
    # BOTH BOUNDS ARE ARGUED rather than picked. 1 is legitimate and is the
    # floor: it serialises everything, which is Butler as it was before this
    # phase, and it is the only way to say "one agent at a time" without
    # turning harness mode off. 0 is NOT allowed -- it would refuse every
    # question with a cap sentence and no way to answer one, which is a
    # microphone that cannot be used (D-1). The ceiling is 50 and it is about
    # the MACHINE rather than about Butler: ten idle pi children measured
    # 1326 MB here, about 2% of RAM [MEASURED, docs/parallel-agents.md], so 50
    # is roughly 6.6 GB and a tenth of this desk -- an order of magnitude past
    # what the owner asked for, still short of the point where the refusal
    # would be kinder than the swap file.
    _s("harness_agents_max", 10, "harness", "Agents at once",
       kind="number", minimum=1, maximum=50,
       # THE HELP SAYS WHAT THIS DAEMON DOES, and it used to say two things it
       # does not (review, 2026-09-12). It offered "or let one finish", and a
       # finished conversation KEEPS its child (AC-59) -- so finishing is
       # exactly what fills the cap, and three finished conversations at a cap
       # of three have Butler saying "3 of 3 agents are running" when none is.
       # And "press stop" only reaches a turn IN FLIGHT (`Hub._stop` ignores a
       # frame that does not name the turn the session owns), so an idle
       # conversation cannot be stopped at all until phase 8 makes stop act on
       # the conversation on screen. Whether a done conversation should keep
       # its child is the OWNER's ruling and is open in `handoff.md`; saying
       # something false in the meantime is not.
       help="How many coding agents may run at once, one per chat. At the "
            "limit a new request is refused out loud, naming the projects "
            "that hold the places; nothing is queued. A chat keeps its agent "
            "once it has one, so finishing a reply does not free a place: "
            "press stop while that chat is still answering, or delete it. 1 "
            "runs one agent at a time. Takes effect on the next thing you "
            "say. Ten idle pi agents measured 1326 MB here, about 2% of RAM, "
            "so the ceiling is your machine rather than Butler."),

    # ONE ACTIVE CHAT PER PROJECT (docs/filing-and-pacing.md item 1, section
    # 1.6). "Only one agent/active chat per project at once, but up to the
    # max allowed agents in settings which would be on different projects...
    # for now, and for other users, it is safer to be ready for it, but only
    # allow 1 per project for now" -- the owner's own words, and the reason
    # this is a NUMBER read every spawn rather than a hardcoded exclusivity
    # rule: raising it later is a settings change, never a redesign. A
    # SEPARATE number from `harness_agents_max` above -- that one is the
    # total across every project, this one bounds ONE project at a time, and
    # D5 settled both as ONE global number rather than a per-project field on
    # `ShortcutStore.Shortcut`, matching the owner's own singular phrasing.
    # NOT restart-flagged, for `harness_agents_max`'s own reason: read at the
    # moment `_Host.plan()` checks a collision, so the next thing said sees
    # a raised limit with no restart.
    # THE MAXIMUM IS `harness_agents_max`'s DEFAULT (10), NOT its own ceiling
    # (50) -- review, 2026-09-16. A first draft copied 50 straight from the
    # global cap, whose own 50 is earned by a measured argument (10 idle pi
    # children = 1326 MB) that says nothing about what ONE project may hold.
    # A per-project limit set above however many agents the daemon runs AT
    # ALL cannot ever bind: with `harness_agents_max` at its own default of
    # 10, a `project_agent_limit` of 20-50 would be a control the page shows
    # doing nothing, silently, until the owner also raises the other
    # setting. 10 is the honest ceiling for that default install; raising
    # `harness_agents_max` and wanting more concurrency in one project at
    # once is the case this bound should be revisited for, not guessed at
    # now.
    _s("project_agent_limit", 1, "harness", "Agents per project",
       kind="number", minimum=1, maximum=10,
       help="How many of those agents may be in the SAME project at once. "
            "1 means a second chat on a project with a live agent waits for "
            "the first to finish or be stopped. Designed to be raised past "
            "1 later; today it is enforced at 1 either way. Bounded at 10 "
            "here because that is the total agent count's own default "
            "(Agents at once) -- a project limit above however many agents "
            "run at all cannot bind."),
    # THE IDLE GRACE (section 1.6, stage 3). "project held but idle child
    # occupy its single slot for x time (settings adjustable) default to 60
    # minutes" -- the owner's own words. 0 is a real value and not a
    # special case: it means the slot releases the instant a turn ends,
    # which is stage 2's behaviour folded in as this setting's own floor
    # rather than a second knob. Checked LAZILY, at the same moment the
    # project collision above is checked -- never a background timer, for
    # D-1's reason: a timer is a task this process has to remember to cancel
    # on every teardown path, and a lazy check has no state that can leak.
    # THE MAXIMUM IS 1440 (24 HOURS) FOR A STATED REASON, NOT A ROUND NUMBER
    # BORROWED FOR ITS SHAPE (review, 2026-09-16). This is a real unit the
    # owner already reasons in -- "I will come back to this project
    # tomorrow" -- rather than an arbitrary power-of-two or a ceiling copied
    # from an unrelated setting. Past a full day an idle child is not being
    # kept warm for later today, it is a slot silently reserved for a
    # project nobody has touched since yesterday, which is what this cap
    # exists to bound.
    _s("project_idle_grace_minutes", 60, "harness", "Idle grace (minutes)",
       kind="number", minimum=0, maximum=1440,
       help="How long an idle agent keeps holding its project's slot before "
            "a new chat there frees it automatically. 0 releases the slot "
            "the moment a turn ends. The agent's own memory is not lost "
            "when this frees the slot -- it resumes from its session file "
            "the next time that conversation is asked something, at the "
            "cost of a 1.5-2.3s respawn. Capped at 1440 (24 hours): past a "
            "full day, a held slot is reserved for a project nobody has "
            "touched since yesterday, not kept warm for later today."),

    # -- advanced: end of turn --------------------------------------------- #
    _adv("smart_threshold", 0.6, "turn", "Smart threshold",
       kind="number", minimum=0.1, maximum=0.95,
       help="In Smart mode, how sure the model must be that you have "
            "finished. Upstream "
            "hardcodes 0.5 and its own issue #41 measures Incomplete "
            "precision at 52-58%, so the model already over-waits: lower "
            "this if it feels slow, raise it if it cuts you off."),
    _adv("eou_first_check_ms", 280, "turn", "First check after",
       kind="number", minimum=100, maximum=2000,
       help="In Smart mode, the silence before the model is asked at "
            "all."),
    _adv("eou_recheck_ms", 450, "turn", "Re-ask every",
       kind="number", minimum=100, maximum=2000),
    _adv("eou_max_wait_ms", 6000, "turn", "Give up waiting after",
       kind="number", minimum=1000, maximum=30000,
       help="Silence after which the turn ends regardless of the model. "
            "Measured against SILENCE, never against how long you talk."),
    _adv("no_endpointer_silence_ms", 700, "turn",
       "Silence-only pause", kind="number", minimum=300, maximum=5000,
       help="The pause that ends a Smart turn when the End-of-turn model "
            "is None or not loaded. The field's floor is "
            "500-900 ms; below that a breath reads as the end of a sentence."),
    _adv("no_speech_ms", 6000, "turn", "Give up if nothing is said",
       kind="number", minimum=1000, maximum=30000),
    _adv("absolute_max_turn_ms", 120000, "turn", "Absolute ceiling",
       kind="number", minimum=10000, maximum=600000,
       help="Safety net so a turn always ends. Nothing should ever reach it."),

    # -- listening ---------------------------------------------------------- #
    # AC-68 (docs/ambient.md 3 and 4). How many seconds of the room the
    # SATELLITE holds, so a request made mid-thought does not have to be
    # restated. 0 is off and is the shipped default: a room kept by default is
    # not a default, and 0 means the page keeps no ring at all rather than
    # keeping one it declines to send (AC-72).
    #
    # The ceiling is what a decode can be PAID for, not what the engine can
    # take -- no ceiling was found there at all (ports.Asr.transcribe). The
    # whole-clip path costs about 2 s for 30 s of audio and about 4 s for 60 s
    # [MEASURED 2026-08-19, docs/ambient.md 1], and since the owner's ruling
    # that the back-fill is QUEUED behind the live final rather than decoded
    # beside it, that cost lands between the owner finishing and Butler
    # answering, on every ambient turn (AC-69). 240 s would be about 16 s,
    # which is not a wait anyone pays twice, so the number is in the help text
    # rather than left to be discovered. Everyday rather than advanced: this
    # one decides whether a room is being held, which is not a knob to bury.
    # Read per turn, so it is not restart-flagged.
    # AC-179. WHETHER the satellite listens at all, and the first thing in this
    # group because nothing else in it decides anything while it is off. A
    # DAEMON setting for the reason every preference here is one (D-2, AC-8):
    # the owner turns listening off on the tablet and means it everywhere, and
    # a per-origin copy is how one laptop ended up with four disagreeing copies
    # of a voice setting. It is also what makes the owner's ask literal --
    # "all settings with persistence in between sessions" -- since the page may
    # store nothing.
    #
    # THE PAGE ACTS ON IT AT BOOT (owner's ruling 2026-09-10,
    # `docs/mic-optional.md` R1): true means arm the microphone without waiting
    # for a tap. The cost is stated in the help text rather than buried,
    # because a reloaded tab opening its own microphone is a thing a person
    # should be able to find the switch for. A browser that refuses is a
    # refusal the page SAYS; it is never retried silently.
    _s("stt_enabled", True, "listen", "Listen for the wake phrase",
       kind="bool",
       help="Whether your devices listen for you at all. Off: no device "
            "opens its microphone, so no wake word and no dictation from the "
            "microphone button in the message box; Butler still answers "
            "anything you type. On: a device turns its microphone on as soon "
            "as its page loads, without waiting for a tap, so a tab you "
            "reload is listening again straight away. One setting for every "
            "device."),
    _s("ambient_seconds", 0, "listen", "Ambient context", kind="number",
       minimum=0, maximum=60,
       help="Lets Butler hear the last few seconds before you said the wake "
            "word, so you need not repeat what you were saying. This is how "
            "many seconds your device keeps; 0, the default, keeps nothing "
            "anywhere. It makes each answer slower: about 2 seconds at 30, "
            "about 4 seconds at 60, measured on this machine. The audio "
            "stays on the device until the wake word fires, never reaches "
            "disk, and what Butler overhears is kept apart from what you say "
            "to it."),

    # -- advanced: listening ------------------------------------------------ #
    _adv("hotkeys_enabled", False, "listen", "Desk hotkeys", kind="bool",
         help="Let a key on THIS machine start dictation, and Ctrl plus that "
              "key open a terminal running the coding agent. Off is the "
              "default and off means the helper process is not started at "
              "all: turning this on installs a system-wide keyboard hook that "
              "stops the key producing its character anywhere, which is not "
              "something to switch on by accident. Turning it OFF takes "
              "effect within a few seconds, because a running helper "
              "uninstalls its hook when it sees the change; turning it ON "
              "takes effect at the next daemon start, because the helper is "
              "only started at boot."),
    _adv("dictate_preroll_ms", 0, "listen", "Dictation lookback",
         kind="number", minimum=0, maximum=2000,
         help="How much of the room the desk hotkey keeps ready before you "
              "press the key, so your first word is not clipped. 0 (the "
              "default) opens the microphone only while you hold the key and "
              "closes it when you let go, so Remote Desktop audio keeps "
              "working for every other app on this PC; the first word may be "
              "clipped by about 260 milliseconds. Above 0 never clips the "
              "first word, but the microphone stays open the whole time "
              "hotkeys are on, and that breaks Remote Desktop audio for every "
              "app on this PC (a browser's microphone dies and does not come "
              "back). Nothing reaches disk and nothing leaves the machine "
              "unless you hold the key."),
    _adv("dictate_paste", "ctrl_v", "listen", "Dictation delivery",
         choices=("ctrl_v", "shift_insert", "clipboard_only"),
         labels={"ctrl_v": "Ctrl+V", "shift_insert": "Shift+Insert",
                 "clipboard_only": "clipboard only"},
         help="How dictated text reaches the window you clicked in. Ctrl+V "
              "suits most applications and Shift+Insert suits most terminals. "
              "'clipboard only' pastes nothing and leaves the text one "
              "Ctrl+V away, which is the honest answer for an application "
              "that refuses both. The text is left on the clipboard either "
              "way, and your previous clipboard contents are not restored."),
    # AC-197. The ceiling the upload route is DERIVED from -- `butlerd`
    # multiplies this by a megabyte rather than carrying a second number
    # (D-12), which is `AMBIENT_MAX_BYTES`' idiom and its reason. The maximum
    # bounds what a hand-edited settings file can ask for; the VALUE bounds
    # what arrives. A body is read into memory before anything looks at it and
    # the caller is unauthenticated on a tailnet.
    _adv("attachment_max_mb", 20, "listen", "Attachment ceiling",
         kind="number", minimum=1, maximum=100,
         help="The largest file you may attach to a message, in megabytes. A "
              "photograph is one or two; a scanned document can be tens. The "
              "file is read into memory before anything looks at it, which is "
              "why there is a limit at all."),
    _adv("dictate_max_seconds", 120, "listen", "Dictation ceiling",
         kind="number", minimum=5, maximum=120,
         help="The longest single dictation the desk hotkey will decode. A "
              "key held down and never released stops here instead of filling "
              "the daemon's memory. A full 120-second dictation costs 10.5 "
              "seconds before the text appears, measured on this machine at "
              "that exact length; 30 seconds costs 1.9 and 60 costs 4.4. It "
              "gets slower per second as the clip grows, so do not scale the "
              "short numbers up."),
    # `terminal_cwd` used to live HERE and is RETIRED (AC-144,
    # `docs/shortcuts.md` D5). One directory and one coding agent, edited on
    # the settings page, was replaced by the shortcuts pane on the main page:
    # a row per project, a chord per agent, and one dictation chord, all in
    # `daemon/shortcuts.py`. It is not re-added as a fallback -- two ways to
    # say where a terminal opens is the drift the store exists to remove -- and
    # an existing value is migrated into a row on first boot rather than lost.
    # A row is not a setting because `kind="list"` is a flat list of strings
    # written whole and cannot carry an id, a label or a chord.
    _adv("rms_on", 0.020, "listen", "Speech starts above",
       kind="number", minimum=0.001, maximum=0.5,
       help="A floor, not an absolute: the gate rises with the room, since "
            "browsers leave automatic gain control on."),
    _adv("rms_off", 0.012, "listen", "Silence below",
       kind="number", minimum=0.001, maximum=0.5),
    _adv("min_speech_ms", 200, "listen", "Ignore bursts shorter than",
       kind="number", minimum=0, maximum=2000,
       help="Shorter than this is a cough, not a turn."),
    _adv("arbitration_window_ms", 300, "listen", "Arbitration window",
       kind="number", minimum=0, maximum=1000,
       help="How long to wait for a second device to report the same wake "
            "word before picking the one that heard it best."),
    *_wake_settings(),
    _s("wake_threshold", 0.5, "listen", "Wake sensitivity",
       kind="number", minimum=0.05, maximum=0.95,
       help="Lower hears you more often and the television too."),
    _adv("wake_cooldown_ms", 1300, "listen", "Wake cooldown",
       kind="number", minimum=0, maximum=5000,
       help="Firing is suppressed this long after a reply. Do not go below "
            "1280: the detector scores 16 embeddings of 80 ms, and a "
            "shorter cooldown re-arms it while its context still holds the "
            "tail of Butler's own voice."),

    # -- advanced: engines --------------------------------------------------- #
    # Model and backend load a GGUF (about 0.7 GB of VRAM on Vulkan, 1-2.5 s
    # [MEASURED 2026-10-27, docs/lessons/daemon.md]) and the
    # engine's cache keeps the old one, so a live swap would hold two models
    # on the 1080 Ti: restart-flagged, applied by an automatic restart once
    # nothing is running (AC-715).
    _s("asr_model", "parakeet-unified-en-0.6b", "stt", "Recognition model",
       choices=("parakeet-unified-en-0.6b", "nemotron-3.5-asr-streaming-0.6b"),
       labels={"parakeet-unified-en-0.6b": "Parakeet (English, 0.6B)",
               "nemotron-3.5-asr-streaming-0.6b":
                   "Nemotron (multilingual, 0.6B)"},
       restart=True,
       help="The model that turns your speech into text. Parakeet "
            "understands English only. Nemotron understands other languages "
            "too, but needs a specific language chosen below: it refuses "
            "Automatic. A change restarts Butler by itself as soon as "
            "nothing is running."),
    _adv("asr_backend", "auto", "stt", "Recognition backend",
       choices=("auto", "vulkan", "cpu"), restart=True,
       labels={"auto": "Automatic", "vulkan": "GPU (Vulkan)", "cpu": "CPU"},
       help="Which hardware runs the recognition model. Automatic uses the "
            "graphics card when there is one. A change restarts Butler by "
            "itself as soon as nothing is running."),
    # Default `accurate` by the owner's ruling 2026-08-19: it is 1.6x cheaper
    # per second of audio than `balanced` (7.4 passes over each second against
    # 12.0, window/chunk from the catalogue) and is the preset the engine
    # itself names best-accuracy. What it costs is lookahead -- 2080 ms
    # against 1120 ms -- so partials appear about a second later, which is the
    # one thing the owner will notice and so is said in plain words below.
    # NOT restart-flagged (AC-713): the preset is a per-stream option over the
    # GGUF the engine already holds, so a new adapter warms in about 1 ms
    # [MEASURED 2026-10-27] and is swapped in for the next thing said.
    _adv("asr_latency", "accurate", "stt", "Streaming chunk",
       choices=_latency_presets(), labels=_latency_labels(),
       help="How soon your words appear while you speak, against how "
            "accurate and cheap the recognition is. Best accuracy, the "
            "default, shows words about 2.1 s behind you and is the most "
            "accurate and the cheapest; Balanced about 1.1 s; Low latency "
            "about 0.3 s, but it re-reads each second of audio roughly 37 "
            "times and fell behind under GPU load on this machine (Balanced "
            "re-reads it about 12 times, Best accuracy about 7). The list is "
            "the engine's own. Takes effect on the next thing you say."),
    # AC-152. ONE setting names the language, and every port derives from it.
    # It was `asr_language` until 2026-09-10 and it was INERT: nothing outside
    # this file read it, so it saved, reported success and changed nothing --
    # AC-7b in its purest form, and the reason D-35 exists. It is renamed
    # rather than re-flagged so there is exactly one name for the decision
    # (docs/language.md D1, D4).
    #
    # NOT restart-flagged, and that is a deliberate departure from the four
    # settings it sits beside. A language is per-`open()` at the engine
    # (`stream(language=...)`), the pipeline is rebuilt every turn, and the
    # GGUF is cached per (path, backend) -- so nothing is reloaded and the
    # change lands on the next thing the owner says. Carrying `restart=True`
    # across the rename would have told them to restart for a change the
    # daemon had already taken, which is AC-7b pointing the other way.
    # `labels` come from `_language_labels`, DERIVED from the choices (AC-811):
    # the blank and `auto` read as words, and every locale code labels itself,
    # because a human name per locale would put a list of languages back in
    # this file -- the AC-42 rule this setting's choices exist to obey.
    _s("language", "", "stt", "Language", choices=_catalog_languages(),
       labels=_language_labels(),
       help="The language you speak to Butler, and the one it answers in "
            "when the speech engine has a voice for it. Automatic follows "
            "the language you just used, message by message. Model default "
            "is whatever the recognition model expects. Parakeet understands "
            "English only; another language needs Nemotron, and a language "
            "the loaded model does not speak is refused out loud at the "
            "start of the turn. Under Automatic the Voice setting is dimmed, "
            "because Butler chooses the voice; a message too short to tell "
            "(one word) keeps the last language. The other entries are the "
            "engine catalogue's own language codes. Takes effect on the next "
            "thing you say."),
    # NOT restart-flagged (AC-713, AC-714): read at boot by `build_hub`
    # (until 2026-10-27 it was not read at all -- `build_endpointer` took only
    # the config file, D-36's shape) and swapped live; the scoring child is a
    # CPU process warmed in 788 ms cold, 40 ms once running [MEASURED
    # 2026-10-27].
    # `none` does not refuse: `endpointing.build` falls back to a silence
    # policy (Silence-only pause) and logs it, so the help says that.
    _adv("endpointer", "smartturn", "turn", "End-of-turn model",
       choices=("smartturn", "none"),
       labels={"smartturn": "Smart Turn", "none": "None"},
       help="The model Smart mode uses to hear that you have finished. None "
            "turns it off: Smart mode then ends a turn on silence alone "
            "(Silence-only pause, above), and the log says so. Takes effect "
            "on the next thing you say."),
    # WHO BUTLER IS, AND WHO IT IS TALKING TO: two FILES, the owner's ruling
    # of 2026-09-26 (docs/identity-memory.md 11, AC-420..AC-429), Hermes's and
    # OpenClaw's SOUL.md / USER.md. The one `identity` setting that lived here
    # is RETIRED and migrated into them once (`persona.migrate_identity`,
    # AC-427); `_load` drops the key with its loud unknown-key line. The caps
    # are measured on the body the textbox shows and sum to the old single
    # cap of 6,000, so the per-turn ceiling did not move: SOUL gets the larger
    # share because it carries personality, the speak-versus-work split,
    # permissions and boundaries; USER is a profile, between Hermes's 1,375
    # and OpenClaw's 4,000, and facts learned later go to memory notes. Not
    # restart-flagged: the conversation model reads them per turn and a
    # harness child is replaced by the spec change they cause (AC-374). The
    # help names NO language on purpose (AC-428, language.md D2).
    _s("soul", "", "identity", "Who Butler is", kind="multiline",
       maximum=4000, file="SOUL.md",
       help="SOUL.md in Butler's home folder: Butler's name and personality, "
            "how it speaks (replies are heard, so short and plain) versus how "
            "it works (files and code follow the project's own rules), what "
            "it may do without asking, and its boundaries. Read on every turn "
            "and by any coding agent Butler starts, in any folder; a "
            "project's own AGENTS.md adds detail for that project. The "
            "guidance comment at the top of the file is kept and never sent. "
            "At most 4000 characters."),
    _s("user", "", "identity", "Who you are", kind="multiline",
       maximum=2000, file="USER.md",
       help="USER.md in Butler's home folder: your name and roles, how you "
            "like answers, your standing priorities and preferences, people "
            "and places Butler should know. Things you tell Butler with "
            "\"remember that ...\" go to its memory notes, not here. The "
            "guidance comment at the top of the file is kept and never sent. "
            "At most 2000 characters."),
    # WHERE A FINISHED AUTOMATION IS SENT (docs/automations.md 7, AU-19, the
    # owner's R-A: Telegram ON by default). The channels themselves are FILES
    # (`daemon/notify.py`), not settings: a bot token has no place in
    # settings.json and a second bot must not be a code change. This names
    # which one a NEW automation starts ticked; adding the first channel while
    # it is blank sets it (AC-474). Not validated against the folder here: a
    # name that no longer exists fails at use, by name, and is never rerouted
    # (AC-472).
    _s("notify_default", "", "notify", "Default channel", kind="text",
       help="The channel a new automation notifies unless you untick it. "
            "Adding your first channel below fills this in. Blank means new "
            "automations notify nobody."),
    # docs/proactive.md PR-7 (AC-480): read at each failure, so no restart.
    _s("fail_disable_after", 5, "notify", "Turn off after failures",
       kind="number", minimum=0, maximum=20,
       help="An automation that fails this many times in a row is turned "
            "off, and you get one message saying so. Before that you hear "
            "about the first failure and at most one reminder a day. 0 "
            "never turns one off."),
    # QUIET HOURS (docs/proactive.md PR-14..PR-16, AC-485..AC-487). Read at
    # each spoken line and each tick, so none is restart-flagged. Times are
    # `text` validated as HH:MM at write, and a start equal to its end is
    # refused there (`DIFFERENT`), the old values kept.
    _s("quiet_hours", True, "quiet", "Quiet hours", kind="bool",
       help="Hold unrequested spoken lines overnight and say one summary "
            "when you are back."),
    _s("quiet_start", "22:00", "quiet", "Starts", kind="text", form="hhmm",
       help="24-hour time, HH:MM. A heartbeat does not run during quiet "
            "hours."),
    _s("quiet_end", "08:00", "quiet", "Ends", kind="text", form="hhmm",
       help="When you are back, Butler says the held lines once."),
    # TRIGGERS (docs/proactive.md PR-26..PR-30). Read at every pass and every
    # hook call, so not restart-flagged. The sources themselves are files,
    # listed by the group's collection.
    _s("triggers_enabled", True, "triggers", "Triggers", kind="bool",
       help="Check mail and calendar sources and accept webhook calls. Off "
            "pauses every source and refuses every webhook call."),
    # WHATSAPP (docs/messaging.md MS-5, MS-8, MS-12, MS-14a, MS-16, MS-18a,
    # MS-21; section 6.1 lists the keys). None is restart-flagged: the switch
    # starts and stops the sidecar on change (`daemon/whatsapp.py`), the
    # answer list, its switch and the trigger are pushed to the sidecar on
    # change, and the rest are read at use. OFF by default: nothing starts,
    # and nothing touches WhatsApp, until the user turns it on and links.
    _s("whatsapp_enabled", False, "whatsapp", "WhatsApp", kind="bool",
       help="Turns the link on or off. Off disconnects without unlinking."),
    _s("whatsapp_send", True, "whatsapp", "Butler may send", kind="bool",
       help="Lets Butler send WhatsApp messages to people and groups you "
            "have saved. The first send to each person asks you first. At "
            "most 10 a minute."),
    _s("whatsapp_read", False, "whatsapp", "Butler may read", kind="bool",
       help="Lets the assistant list, read and search your saved WhatsApp "
            "chats when you ask. Off keeps every chat closed to it."),
    _s("phone_questions", True, "whatsapp", "Questions to your phone",
       kind="bool",
       help="When an automation or a phone chat needs an answer, Butler asks "
            "on your phone as well: WhatsApp, or Telegram if its channel has "
            "inbound on. Questions raised at the desk or on the page stay "
            "there."),
    _s("phone_chat", "root", "whatsapp", "Phone chat",
       choices=("root", "current"),
       labels={"root": "Main Butler chat",
               "current": "The chat I last used"},
       help="Which conversation your WhatsApp and Telegram messages join. "
            "They are your own turns in that chat."),
    _s("phone_reply", "voice_text", "whatsapp", "Reply form",
       choices=("voice_text", "mirror", "text"),
       labels={"voice_text": "Voice note and text",
               "mirror": "Mirror what I send",
               "text": "Text only"},
       help="How Butler answers you on WhatsApp and Telegram, in the "
            "language you wrote or spoke."),
    _s("whatsapp_answer_chats", [], "whatsapp", "Butler answers",
       kind="list", lowercase=False, allow_empty=True, form="jid",
       choices_from="whatsapp_chats",
       help="People and groups Butler may reply to for you: pick from your "
            "WhatsApp chats, or type a chat id. It says it is Butler, uses "
            "no tools, knows nothing private about you, answers at most 20 "
            "times an hour per chat, and goes quiet for 30 minutes when you "
            "reply yourself."),
    _s("whatsapp_answer_all", False, "whatsapp",
       "Answer every saved contact who writes", kind="bool",
       help="Off: only the people and groups listed above. On: anyone in "
            "your saved contacts who messages you in a one-to-one chat. "
            "Groups always need the list and the trigger word."),
    _s("whatsapp_group_trigger", "Butler", "whatsapp", "Group trigger word",
       kind="text", form="nonblank",
       help="In a group you listed, Butler answers only a message that "
            "starts with this word, or a reply to one of its messages. "
            "Mentioning you does not count."),
    # DEVICES (docs/desktop-app.md DA-15, DA-16): read at each phone-link
    # read and set-up (`daemon/phonelink.py`), so not restart-flagged. A
    # setting because 443 was already forwarded to another program on the
    # desk [MEASURED 2026-10-07], and Butler never replaces a forward.
    _s("phone_link_port", 443, "devices", "Phone link HTTPS port",
       kind="number", minimum=1, maximum=65535,
       help="The port of this PC's Tailscale address that phones open. "
            "443 gives the plain address; pick another only when 443 "
            "already forwards to something else. Changing it leaves the "
            "old forward in place."),
    # ACCESS (docs/remote-access.md): who may open Butler from another
    # device. Both owner-only (RA-11): a paired device cannot change them.
    # RA-7: read by `butlerd.main` BEFORE it binds, hence the restart.
    _s("lan_access", False, "access", "Open on the local network",
       kind="bool", restart=True,
       help="Off: other devices reach Butler only through this PC's "
            "Tailscale address. On: also at this PC's network address "
            "over HTTPS, and each such device is paired once. Needs the "
            "certificate made by scripts/make-cert.py."),
    # docs/agent-and-terminal-settings.md SW-6: a terminal is a shell, so
    # off by default and owner-only (`access.OWNER_KEYS`); read per request
    # by `daemon/terminals.py`, and switching it off ends every terminal.
    _s("terminals_enabled", False, "access", "In-page terminals",
       kind="bool",
       help="Open a shell in a project's folder from the page (Open terminal "
            "under the project); type claude, pi or opencode there. Off: "
            "no terminals, and turning it off closes the open ones."),
    # RA-6: read by `daemon/access.py` at each request through the proxy
    # port, so not restart-flagged.
    _s("tailscale_owners", "", "access", "Tailscale owners",
       kind="text",
       help="Tailscale logins that open Butler through this PC's Tailscale "
            "address with no pairing, separated by commas. Blank means the "
            "login this PC is signed in with."),
)

#: A `text` setting's `form`: the check, and the words a refusal uses.
TEXT_FORMS = {"hhmm": (re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]"),
                       "a 24-hour time, HH:MM"),
              # The sidecar refuses a blank trigger (`parseConfig`), so the
              # store refuses it first, where the page can show why.
              "nonblank": (re.compile(r"\S.*"), "a word, not blank"),
              # A `list` setting with a form checks EACH entry. The sidecar
              # refuses an entry without an `@` (MS-14a), and a refused
              # config push would stop the whole service.
              "jid": (re.compile(r"[^\s@]+@[^\s@]+"),
                      "a WhatsApp chat id such as 15551234567@s.whatsapp.net")}

#: Pairs of settings that may never hold the same value, and the refusal.
#: Checked in `update()` on the values AFTER the write, so moving both at
#: once is one write judged as one (docs/proactive.md PR-14, AC-485).
DIFFERENT = ((("quiet_start", "quiet_end"),
              "Quiet hours cannot start and end at the same time ({})."),)

def _validated_settings(schema: tuple) -> tuple:
    """Refuse a schema whose composer row would lie.

    `Group.quick` has this guard in `_validated_groups` and the per-setting flag
    needs its own, at import, where the schema is DEFINED -- a restart-flagged
    picker against the text box saves, reports success and changes nothing,
    with no restart badge and no commit bar within reach of that row (AC-7b,
    AC-86). One rule, two flags, because a setting can carry the second without
    its group carrying the first.
    """
    owed = sorted(s.key for s in schema if s.quick and s.restart)
    if owed:
        raise ValueError(
            f"{owed} are drawn under the chat input (quick), so they may not "
            f"be restart-flagged; each would save, report success and do "
            f"nothing until a restart, with nowhere on that row to say so")
    # A file holds PROSE (AC-426): the page's textarea is the only editor it
    # has, and a file-backed choice or number would be a second store for a
    # value that has a first one.
    odd = sorted(s.key for s in schema if s.file and s.kind != "multiline")
    if odd:
        raise ValueError(f"{odd} are backed by a file, which only a "
                         f"multiline setting may be")
    # A preview card shows a choice by its NAME (AC-704): anything but a
    # fully labelled choice would draw cards with nothing readable on them.
    unnamed = sorted(s.key for s in schema if s.preview and not (
        s.kind == "choice" and s.choices and set(s.labels) == set(s.choices)))
    if unnamed:
        raise ValueError(f"{unnamed} ask for preview cards, which only a "
                         f"choice with a label for every value may")
    return schema


SCHEMA = _validated_settings(SCHEMA)
BY_KEY = {s.key: s for s in SCHEMA}
DEFAULTS = {s.key: s.default for s in SCHEMA}
#: THE CEILING ON ONE TYPED MESSAGE, in characters: what the owner could have
#: SAID in the longest dictation this daemon listens to, at the 16 characters
#: a second it has measured (the argument is `hub.TYPED_MAX_CHARS`'s comment,
#: which re-exports these). Here so `library.expand` caps a filled recipe at
#: the same number without importing the hub.
TYPED_CHARS_PER_SECOND = 16
TYPED_MAX_CHARS = (int(BY_KEY["dictate_max_seconds"].maximum)
                   * TYPED_CHARS_PER_SECOND)
#: The settings whose value is a FILE's body, never a `settings.json` entry
#: (`Setting.file`, docs/identity-memory.md 11.3).
FILE_KEYS = frozenset(s.key for s in SCHEMA if s.file)

# Keys that were RENAMED, old -> new, applied once as the file is read
# (`_load`). A rename is not a `_migrate` case: that function takes a key and
# returns a value, so it cannot move one key's value to another, and the
# unknown-key drop would have thrown the value away before it ever ran.
#
# The value still goes through `validate` afterwards, which is what makes this
# safe rather than merely convenient: the owner's stored `asr_language` was
# "PT", which is not a locale ANY engine here accepts (measured -- nemotron
# takes `pt-BR` and rejects `pt`), so it is dropped with a loud line by the
# existing bad-value branch instead of being carried into a recognizer that
# would refuse every turn. A value that never did anything is not a choice
# worth preserving into a setting that does.
RENAMES = {"asr_language": "language"}


@dataclass(frozen=True)
class Group:
    """A section of the settings page: what it is called, where it sits, and
    the one current fact it shows.

    `stage` is its position in the audio path -- 1 for where sound arrives,
    6 for where Butler answers -- or None for a group that is not a stage of
    anything. It used to be a digit glued to the front of `label`
    ("1. Listening", "5. Harness"), which meant the only thing that knew the
    pipeline was a string being printed. The page draws a chain of the stages
    and cannot work the sequence out for itself: nothing in `web/` may name a
    setting or a seam (AC-41, web/AGENTS.md), so the daemon says it.

    `headline` is the KEY of the setting whose value that section's box
    displays, or None where the group has no single fact worth showing.

    `blurb` is the one sentence printed under the section's heading, saying
    what that stage of the audio path DOES to someone deciding whether they
    are in the right place. It is here for the same reason `stage` and
    `headline` are: a page that writes its own description of a seam is
    naming one, which AC-41's grep gate forbids, and the mock that asked for
    these sentences invented the field to hold them. Empty is legal and
    means the section prints no sentence, exactly as a None `headline` shows
    no value -- a heading with nothing under it is honest, and a heading
    with a restatement of its own label under it is noise.

    `quick` means this group is ALSO offered outside the settings surface, on
    the page itself. The owner asked for every appearance option on the
    header pill rather than only dark and light (2026-09-11), and the page may
    not decide which settings those are: naming the four `look` keys in
    `web/app.js` would be exactly the hardcoded form AC-41 exists to stop, and
    the drifted copy is the one that silently omits the setting added last.
    So the DAEMON marks the group and the page renders whatever is in it --
    from the same schema answer, through the same single-key write.

    A quick group's settings must not be restart-flagged. A control on the
    main page that saves, reports success and changes nothing until the daemon
    is restarted is AC-7b at the front door, and the pill has nowhere to put a
    restart badge. Checked in `_validated_groups` rather than trusted.
    """
    label: str
    stage: int | None = None
    headline: str | None = None
    blurb: str = ""
    quick: bool = False
    collection: str = ""
    """A route whose ITEMS this section also lists and edits, or "".

    docs/automations.md 7: notification channels are files with secrets in
    them, not settings, yet they belong on this page beside the setting that
    names the default one. The page may name neither the route nor a field
    (AC-41), so the daemon serves the URL here and the route answers
    `{noun, items, kinds}` -- the rows, and the fields an Add form needs,
    with a `secret` flag the page honours by never pre-filling that input.
    """
    pairing: str = ""
    """A route that links this section to a device by QR code, or "".

    docs/messaging.md MS-5, AC-503: the WhatsApp group's pairing block. Same
    argument as `collection`: the page may name neither the route nor what
    it links (AC-41), so the daemon serves the URL here and the route answers
    one generic document -- status words, the QR as an image, whether to
    poll, and the buttons that apply now (`daemon/whatsapp.py` `page_doc`).
    """
    section: str = ""
    """The key of the `SECTIONS` entry this group is listed under in the
    settings sheet's navigation (docs/lantern-ui.md CU-11, AC-700). The page
    may not decide that Brain is under Voice (AC-41), so the daemon says.
    Required in practice: `_validated_groups` refuses one not in `SECTIONS`.
    """


#: The settings sheet's navigation, in the order it is listed (AC-700), key
#: to label. Served whole by `describe()`. "safety" has no daemon group yet:
#: the permission mode it holds is a block the page builds by hand, not a
#: settings group, and it is kept here so the page draws that category from
#: this list rather than naming one of its own.
SECTIONS = {"voice": "Voice", "you": "You", "connected": "Connected",
            "safety": "Safety"}


def _validated_groups(groups: dict[str, Group]) -> dict[str, Group]:
    """Refuse a group table the page could only render as a blank.

    At import, where the table is DEFINED, rather than at the moment a box is
    drawn. A headline naming a setting that no longer exists is what a rename
    leaves behind, and it looks complete from every angle: the schema is
    consistent, the page renders, and one box quietly stops saying anything.
    The owner reads that as "not set" (AC-7b). No fallback, no blank: the
    daemon does not start.

    The stage numbers are checked here too rather than only in the test suite,
    for the same reason and at the same cost -- a gap or a swap draws a broken
    chain, and the page cannot notice.
    """
    for key, group in groups.items():
        if group.headline is not None and group.headline not in BY_KEY:
            raise ValueError(
                f"settings group {key!r} headlines {group.headline!r}, which "
                f"is not a setting in the schema")
        # A group under no served section is listed nowhere on a sheet of
        # categories (AC-700), which is the blank this function refuses.
        if group.section not in SECTIONS:
            raise ValueError(
                f"settings group {key!r} is in section {group.section!r}, "
                f"which is not one of {list(SECTIONS)}")
    stages = [g.stage for g in groups.values() if g.stage is not None]
    if stages != list(range(1, len(stages) + 1)):
        raise ValueError(
            f"settings group stages must run contiguously from 1 in key "
            f"order, got {stages}")
    for key, group in groups.items():
        if not group.quick:
            continue
        owed = sorted(s.key for s in SCHEMA if s.group == key and s.restart)
        if owed:
            raise ValueError(
                f"settings group {key!r} is drawn on the main page (quick), "
                f"so it may hold no restart-flagged setting; {owed} would "
                f"save, report success and do nothing until a restart, with "
                f"nowhere on that surface to say so")
    return groups


# One group per stage of the audio path, in the order sound travels it
# (plan.md 4.2), so the page reads as the pipeline: hear it, recognise it,
# decide you finished, think, speak. Each swappable engine has exactly one
# home, which is what makes "where do I configure speech recognition" a
# question with an answer (AC-45). Order is load-bearing and asserted in
# test/test_seams.py -- dict order is what the page renders.
#
# Two headlines name the closest honest setting rather than the engine, and
# say so here because the gap is not obvious from the page: `tts` shows the
# VOICE, and `stt` shows `asr_model`. Which TTS adapter and which ASR adapter
# are loaded is named in config/models.json (4.9) and is not a setting at all,
# so there is nothing truer to show. `listen` has no headline for the opposite
# reason: no engine sits there, and a wake threshold is not a fact about a
# seam -- inventing one would be a lie in a box the owner reads at a glance.
GROUPS = _validated_groups({
    "listen": Group(
        "Listening", 1, None,
        "Where sound arrives: how loud speech has to be before Butler "
        "counts it, and how readily the wake word fires.",
        section="voice"),
    "stt": Group(
        "Recognition", 2, "asr_model",
        "Turns the captured audio into text: which model does it, on what "
        "hardware, and in which language.",
        section="voice"),
    "turn": Group(
        "Turn taking", 3, "turn_mode",
        "How Butler decides you have finished speaking, and when it gives "
        "up waiting. Everything after this point is held up by it.",
        section="voice"),
    "brain": Group(
        "Brain", 4, "brain_mode",
        "What answers you: the router that reads every utterance, and the "
        "model behind it.",
        section="voice"),
    "harness": Group(
        "Coding agent", 5, "harness",
        "The coding agent Butler hands work to: which one runs, where it "
        "runs, and how much of its toolset it keeps.",
        section="voice"),
    "tts": Group(
        "Speech", 6, "voice",
        "The voice Butler answers in, and the tones that mark heard-you, "
        "about-to-submit and taken.",
        section="voice"),
    "look": Group(
        "Appearance", None, "theme",
        "How this page looks. Kept on the daemon like every other "
        "preference, so two devices cannot disagree.",
        quick=True, section="you"),
    # Not a stage of the audio path, so no stage and no headline: the value
    # is a paragraph of prose, which a summary box cannot show (AC-370).
    "identity": Group(
        "Identity", None, None,
        "Who Butler is and who you are, kept in two files in Butler's home "
        "folder, SOUL.md and USER.md, which you can also edit by hand. Read "
        "on every turn by the conversation model, and by any coding agent "
        "Butler starts.",
        section="you"),
    "notify": Group(
        "Notifications", None, "notify_default",
        "Where a finished automation sends its result: each channel is a "
        "bot, kept as one file in Butler's records, and its token never "
        "comes back to this page.",
        collection="/api/notify", section="connected"),
    # docs/proactive.md PR-14..PR-16: not a stage, so no stage and no
    # headline, after the notifications whose spoken half it holds.
    "quiet": Group(
        "Quiet hours", None, None,
        "Spoken lines nobody asked for wait until morning. Your own turns, "
        "reminders and phone messages are not held.",
        section="you"),
    # docs/proactive.md PR-26: not a stage, so no stage and no headline; the
    # sources are files with secrets, listed by the collection (AC-496).
    "triggers": Group(
        "Triggers", None, None,
        "What can start an automation besides its schedule: a mailbox, a "
        "calendar, or another program calling Butler. Each source is one "
        "file in Butler's records; its password, link or token never comes "
        "back to this page. A run started by one gets read-only tools, "
        "because what arrived was written by someone else.",
        collection="/api/triggers", section="connected"),
    # docs/messaging.md MS-5: not a stage, so no stage and no headline; the
    # pairing block says the link's state better than one value could.
    "whatsapp": Group(
        "WhatsApp", None, None,
        "Link your WhatsApp so Butler can send messages for you and answer "
        "you from your phone. Do not link a number where another assistant "
        "already answers your own chat: the two would answer each other.",
        pairing="/api/whatsapp/pairing", section="connected"),
    # docs/desktop-app.md DA-15: not a stage, so no stage and no headline;
    # the pairing block shows the phone address as a QR code (AC-553).
    "devices": Group(
        "Devices", None, None,
        "Open Butler on your phone: this PC's Tailscale address as a code "
        "to scan, and the one button that sets it up. Only devices on your "
        "own Tailscale network can reach it.",
        pairing="/api/phonelink", section="connected"),
    # docs/remote-access.md RA-8..RA-11: the block is `/api/devices`, the
    # owner's device list and pair codes (owner-only).
    "access": Group(
        "Access", None, None,
        "Who can open Butler from another device. Your own phone signed "
        "in to your Tailscale account needs nothing; any other device is "
        "paired once with a code from here.",
        pairing="/api/devices", section="safety"),
})

# Spoken phrases that select a turn mode. Matched against the WHOLE
# utterance, so they only fire when the user says nothing else.
SPOKEN_MODES = {
    "endword": ("long form", "long mode", "let me think", "take your time",
                "dictation mode"),
    "smart": ("smart turn", "normal mode", "conversation mode"),
    "patient": ("pause mode", "patient mode", "wait for me"),
}

# Spoken phrases that start a fresh harness conversation (AC-35,
# docs/harness-mode.md 7.6). Matched on the WHOLE utterance for the same
# reason the table above is: rotating throws the agent's context away, and
# doing that because somebody mentioned the feature in passing is the worst
# version of a substring match. Deliberately disjoint from SPOKEN_MODES --
# two whole-utterance matchers run in one `_on_final`, and a phrase both of
# them claim is decided by branch order rather than by meaning.
SPOKEN_NEW_CONVERSATION = ("new conversation", "start a new conversation",
                           "new chat", "start a new chat",
                           "fresh conversation")

# Memory (docs/identity-memory.md 4.5, AC-378, AC-379). "remember that" is a
# PREFIX and the rest of the utterance is the note, VERBATIM; "forget that"
# is the WHOLE utterance, because "forget that idea and fix the tests" is work
# rather than a deletion. English only, as every command table here is
# (language.md D5); Portuguese phrases are deferred (spec section 8). Disjoint
# from the three matchers above by construction: none of them can claim an
# utterance that starts "remember that" or is exactly "forget that".
SPOKEN_REMEMBER_PREFIX = "remember that"
SPOKEN_FORGET = ("forget that",)


class SettingsError(ValueError):
    """A refused write. Loud, and the old value survives."""


def _migrate(key: str, value):
    """Carry an older on-disk value into its current shape.

    Only one migration exists so far, and it is the model settings becoming
    `{conn, model}` pairs (AC-30). A bare string was the whole value when
    there was one endpoint; it becomes the MODEL half with no connection,
    which is the honest reading -- the owner picked a model, and nothing ever
    asked them which connection it was on.

    Migrating rather than rejecting, because the alternative is a daemon that
    will not start after an update; migrating rather than dropping, because
    the alternative is losing a choice they made without saying so.
    """
    spec = BY_KEY.get(key)
    if spec is not None and spec.kind == "pair" and isinstance(value, str):
        return {"conn": "", "model": value}
    return value


class Settings:
    def __init__(self, path: Path, files: dict | None = None):
        """`files` maps each `FILE_KEYS` key to the object that owns its file
        (`persona.PersonaFile`: `body()`, `write(body)`, `effective()`,
        `problem()`). The daemon always passes it (`build_hub`, D-18). Without
        it a file-backed setting reads blank and REFUSES a write, rather than
        pretending to keep a persona somewhere nobody will read it.
        """
        self.path = Path(path)
        self._files = dict(files or {})
        if files is not None and set(self._files) != FILE_KEYS:
            raise ValueError(f"files must cover exactly {sorted(FILE_KEYS)}, "
                             f"got {sorted(self._files)}")
        self._values = {k: v for k, v in DEFAULTS.items()
                        if k not in FILE_KEYS}
        self._values.update(self._load())
        self._listeners: list = []
        # What THIS process was built with, for every restart-flagged
        # setting: the store is constructed at boot (`build_hub`), so a value
        # that moves away from this is one the running daemon has not applied
        # (`pending`, AC-716).
        self._booted = {s.key: self.get(s.key) for s in SCHEMA if s.restart}

    # -- persistence -------------------------------------------------------- #
    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as e:
            # Loud, and NOT fatal: unlike a permission mode, a preference
            # cannot make Butler unsafe, so refusing to boot over one would
            # be the worse failure.
            print(f"[butler] settings file {self.path} unreadable ({e}); "
                  f"using defaults")
            return {}
        if not isinstance(raw, dict):
            # Valid JSON, wrong shape: `null`, `[]`, `42`. This reached
            # `raw.items()` and raised AttributeError out of build_hub, so
            # the daemon would not BOOT -- the one outcome this module's
            # docstring rules out, since a preference cannot make Butler
            # unsafe and refusing to start leaves no microphone at all (D-1).
            print(f"[butler] settings file {self.path} is not an object "
                  f"({type(raw).__name__}); using defaults")
            return {}
        out = {}
        for key, value in raw.items():
            renamed = RENAMES.get(key)
            if renamed is not None and renamed not in raw:
                # Carry the VALUE to the new key (AC-152, D4). Above the drop
                # below on purpose: that branch fires first for any key not in
                # the schema, so a rename handled by `_migrate` would never be
                # reached -- `_migrate` takes a key and returns a VALUE, and
                # has no rename axis at all. `docs/language.md` D4 named the
                # wrong mechanism; this is the right place for it.
                #
                # `renamed not in raw` so an explicit new value always wins
                # over a leftover old one, rather than depending on dict order.
                print(f"[butler] settings: {key!r} is now {renamed!r}; "
                      f"carrying its value over", flush=True)
                key = renamed
            if key in FILE_KEYS:
                # Kept in its FILE, never here (AC-426). A copy in this file
                # is one somebody wrote by hand, and honouring it would give
                # the persona two sources.
                print(f"[butler] ignoring {key!r} in {self.path.name}: it is "
                      f"kept in {BY_KEY[key].file}")
                continue
            if key not in BY_KEY:
                # A key we no longer honour. Dropping it silently is right --
                # keeping it would make the file look like it still does
                # something.
                print(f"[butler] ignoring unknown setting {key!r} in "
                      f"{self.path.name}")
                continue
            try:
                out[key] = self.validate(key, _migrate(key, value))
            except SettingsError as e:
                print(f"[butler] ignoring bad setting in {self.path.name}: {e}")
        return out

    def _write(self) -> None:
        """Atomic replace: a half-written settings file on the next boot is a
        confusing way to lose an evening."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._values, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- reading ------------------------------------------------------------ #
    def __getitem__(self, key: str):
        if key in FILE_KEYS:
            return self._file_value(key)
        return self._values[key]

    def get(self, key: str, fallback=None):
        if key in FILE_KEYS:
            return self._file_value(key)
        return self._values.get(key, fallback)

    def _file_value(self, key: str) -> str:
        """The file's body as it is on disk -- what the page shows, even when
        it is over its cap and not in use, so the owner can see what to trim."""
        owner = self._files.get(key)
        return owner.body() if owner is not None else ""

    def _changed(self, spec) -> bool:
        if spec.key in FILE_KEYS:
            # A file is "changed" when it SAYS something: a template-only file
            # sends nothing and is the shipped state (AC-422).
            owner = self._files.get(spec.key)
            return bool(owner is not None and owner.effective())
        return self._values[spec.key] != spec.default

    def _problem(self, key: str) -> str:
        owner = self._files.get(key) if key in FILE_KEYS else None
        return owner.problem() if owner is not None else ""

    @property
    def turn_mode(self) -> str:
        return self._values["turn_mode"]

    @property
    def patience(self) -> str:
        return self._values["patience"]

    def as_dict(self) -> dict:
        return {**self._values, **{k: self.get(k) for k in FILE_KEYS}}

    # Which settings the CURRENT `brain_mode` makes inert, and the sentence
    # the page shows on each (AC-66, AC-67). This table exists because adding
    # `harness_only` created three controls that save, report success and do
    # nothing -- `router_model`, `router_reasoning`, `router_max_tokens`, with
    # no router in the path -- and that is the exact failure AC-7b names. It
    # is not new to this mode: `endpoint` has always left the whole harness
    # group inert, and `harness` has always left the conversation model inert,
    # and neither said so.
    #
    # Whole GROUPS are named where a group is entirely inert, so the page can
    # dim a pipeline box without knowing what a harness is. Nothing here
    # decides anything: a setting marked inert is still stored, still
    # validated and still editable, because the mode it belongs to is one
    # restart away and a value the owner set for it must survive the trip.
    INERT = {
        "endpoint": ({"harness"}, (),
                     "used only when a coding agent answers (Brain mode: "
                     "Chat + coding agent, or Coding agent only)"),
        "harness": (set(), ("endpoint_model", "reply_reasoning",
                            "reply_max_tokens"),
                    "the coding agent answers in this mode, not a "
                    "conversation model"),
        "harness_only": (set(), ("endpoint_model", "reply_reasoning",
                                 "reply_max_tokens", "router_model",
                                 "router_reasoning", "router_max_tokens"),
                         "there is no router and no conversation model in "
                         "this mode"),
    }

    # WHICH SETTING'S MODEL ANSWERS, per brain mode -- the other face of
    # `INERT` above. The page prints it on the header's model control, and
    # may not pick it itself (AC-41): taking the first `quick` setting served
    # printed "Brain <model>" in `harness_only`, where that model decides
    # nothing (review 2026-09-26). An unknown mode names nothing, and the page
    # then keeps the first quick setting, for `_inert`'s reason.
    # In the harness modes no SETTING answers: each chat's own agent and model
    # do, on the composer's chat chip (docs/chat-model.md CM-6). None tells the
    # quick pill to name nothing rather than fall back to the Brain model,
    # which decides nothing there (review 2026-09-26).
    ANSWERS = {
        "endpoint": "endpoint_model",
        "harness": None,
        "harness_only": None,
    }

    # The SECOND thing that can make a control inert, and it is independent of
    # `brain_mode` (AC-213). `language = auto` means Butler picks the voice per
    # message, so `voice` still saves and still validates and no longer decides
    # anything -- which is exactly what AC-66's dimming is for. Its own table
    # rather than rows added to `INERT` above, because that one is keyed on one
    # setting's value and a second axis folded into it would have to repeat
    # every brain mode three times.
    INERT_UNDER_AUTO_LANGUAGE = (
        ("voice", "Butler is choosing the voice to match what you say"),
    )

    def _inert(self) -> tuple[set, dict]:
        """`(inert group keys, {setting key: why})` for the current mode.

        Unknown or absent `brain_mode` marks nothing, deliberately: a value
        this table has not been taught about must render as fully live rather
        than as fully dead, because dimming a control that works is the same
        lie as not dimming one that does not, and only one of the two is
        recoverable by the owner.

        TWO independent passes. The second is `language = auto` and it is a
        pass rather than a branch because the two conditions are orthogonal:
        a harness_only daemon with automatic language has six inert controls
        from the first and one from the second, and neither knows about the
        other. A later `why` wins over an earlier one, which only matters if a
        key is ever in both tables -- and the honest sentence is then the
        narrower one.
        """
        groups, keys, why = self.INERT.get(
            str(self._values.get("brain_mode", "")), (set(), (), ""))
        inert = {k: why for k in keys}
        for spec in SCHEMA:
            if spec.group in groups:
                inert[spec.key] = why
        if str(self._values.get("language", "")) == AUTO_LANGUAGE:
            for key, reason in self.INERT_UNDER_AUTO_LANGUAGE:
                inert[key] = reason
        # A THIRD pass, keyed on the setting's own value (AC-808): Chat only
        # (`endpoint`) holds no conversations, so a word set to `new` talks to the one on
        # screen. `screen` and `off` mean what they say there and stay live.
        if str(self._values.get("brain_mode", "")) == "endpoint":
            for w in WAKE_WORDS:
                if self._values.get(wake_key(w.id)) == "new":
                    inert[wake_key(w.id)] = self.WAKE_NEW_UNDER_ENDPOINT
        return set(groups), inert

    WAKE_NEW_UNDER_ENDPOINT = (
        "Chat only keeps no conversations, so this word talks to the "
        "conversation on screen")

    def pending(self) -> list[str]:
        """Restart-flagged keys whose value differs from what this process
        booted with, in schema order (AC-716). The page's restart bar and the
        automatic restart (`apply_settings`) both read this one answer, and a
        value put back leaves it."""
        return [k for k, v in self._booted.items() if self.get(k) != v]

    def describe(self) -> dict:
        """The schema, the current values, and what differs from default.

        Served to the page so the form is generated from the daemon's own
        list of settings. A hardcoded form drifts the first time a setting is
        added, and the drift is invisible until the new one silently does
        nothing.
        """
        inert_groups, inert = self._inert()
        return {
            # `stage`, `headline`, `blurb` and `inactive` cross the wire
            # because the page cannot derive any of them: naming a setting in
            # `web/` is what AC-41's grep gate forbids, so the chain of
            # pipeline boxes, the value each one shows, the sentence under
            # each heading, and which of them the current mode switches off
            # are all facts the daemon states (AC-45, AC-66).
            # `quick` is the same rule one step further: which settings are
            # reachable WITHOUT opening this surface is the daemon's to say,
            # or the page holds a second list of keys that drifts.
            # `answers` is the key whose model is answering in this mode, or
            # None where no setting answers (the harness modes: each chat's
            # own chip does, docs/chat-model.md CM-6).
            "answers": self.ANSWERS.get(
                str(self._values.get("brain_mode", ""))),
            # What the running daemon has not applied yet (AC-716): the
            # page's bar names these, so it never keeps a baseline of its
            # own that misses a change made before it loaded.
            "pending": self.pending(),
            "groups": [{"key": k, "label": g.label, "stage": g.stage,
                        "headline": g.headline, "blurb": g.blurb,
                        "quick": g.quick, "collection": g.collection,
                        "pairing": g.pairing,
                        "inactive": k in inert_groups,
                        "section": g.section}
                       for k, g in GROUPS.items()],
            # The settings sheet's navigation, in order (AC-700): the page
            # lists these and files each group under its `section`.
            "sections": [{"key": k, "label": v} for k, v in SECTIONS.items()],
            "settings": [
                {
                    "key": s.key, "group": s.group, "label": s.label,
                    "help": s.help, "kind": s.kind,
                    "choices_from": s.choices_from,
                    "choices": list(s.choices), "labels": dict(s.labels),
                    "min": s.minimum,
                    "max": s.maximum, "default": s.default,
                    "favourites": s.favourites, "advanced": s.advanced,
                    "restart": s.restart,
                    # "" for everything that lives only in this surface, else
                    # the short word this setting goes by under the chat input
                    # -- which the page may not shorten for itself (AC-41).
                    "quick": s.quick,
                    # "" when this setting does something in the current mode,
                    # else the reason it does not (AC-7b, AC-66).
                    "inactive": inert.get(s.key, ""),
                    "value": self.get(s.key),
                    "changed": self._changed(s),
                    # "" when nothing is wrong, else why the value on screen
                    # is not the one in use -- today only a hand-edited file
                    # over its cap or unreadable (AC-424). Generic, so the
                    # page names no file (AC-41).
                    "problem": self._problem(s.key),
                    # True where the page draws the choices as preview cards
                    # (AC-704), which it may not decide by name (AC-41).
                    "preview": s.preview,
                }
                for s in SCHEMA
            ],
        }

    # -- writing ------------------------------------------------------------ #
    def validate(self, key: str, value):
        spec = BY_KEY.get(key)
        if spec is None:
            raise SettingsError(f"unknown setting {key!r}")
        if spec.kind == "bool":
            if not isinstance(value, bool):
                raise SettingsError(f"{key} must be true or false")
            return value
        if spec.kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SettingsError(f"{key} must be a number")
            if spec.minimum is not None and value < spec.minimum:
                raise SettingsError(
                    f"{key} must be at least {spec.minimum}, got {value}")
            if spec.maximum is not None and value > spec.maximum:
                raise SettingsError(
                    f"{key} must be at most {spec.maximum}, got {value}")
            return type(spec.default)(value)
        if spec.kind == "list":
            if not isinstance(value, list) or not all(
                    isinstance(v, str) for v in value):
                raise SettingsError(f"{key} must be a list of strings")
            cleaned = [v.strip().lower() if spec.lowercase else v.strip()
                       for v in value if v.strip()]
            if not cleaned and not spec.allow_empty:
                raise SettingsError(f"{key} cannot be empty")
            if spec.form:
                pattern, words = TEXT_FORMS[spec.form]
                bad = [v for v in cleaned if not pattern.fullmatch(v)]
                if bad:
                    raise SettingsError(f"{key}: each entry must be {words}, "
                                        f"got {bad[0]!r}")
            return cleaned
        if spec.kind == "pair":
            # {conn, model}. A bare id is ambiguous the moment two
            # connections serve one name, and unusable for any provider that
            # needs a key -- the connection is what carries the base URL and
            # the auth pointer (plan.md 4.4b, AC-30). Blank is legal in both
            # halves and means "not chosen"; Butler ships no default model.
            if not isinstance(value, dict):
                raise SettingsError(
                    f"{key} must be a {{conn, model}} object, got "
                    f"{type(value).__name__}")
            if set(value) != {"conn", "model"}:
                # BOTH halves, always. A partial object is far more likely a
                # caller that forgot the connection than one that meant "no
                # connection" -- and the legal way to say that is an explicit
                # empty string, which cannot be confused with an omission.
                raise SettingsError(
                    f"{key} must have exactly conn and model, got "
                    f"{sorted(value)}")
            conn = value["conn"]
            model = value["model"]
            if not isinstance(conn, str) or not isinstance(model, str):
                raise SettingsError(f"{key}: conn and model must be text")
            return {"conn": conn.strip(), "model": model.strip()}
        if spec.kind == "text":
            if not isinstance(value, str):
                raise SettingsError(f"{key} must be text")
            if spec.form:
                pattern, words = TEXT_FORMS[spec.form]
                if not pattern.fullmatch(value.strip()):
                    raise SettingsError(f"{key} must be {words}, got "
                                        f"{value!r}")
            return value.strip()
        if spec.kind == "multiline":
            # A block of prose the owner writes (docs/identity-memory.md 4.1,
            # AC-370). Its inner lines are the content, so only the ENDS are
            # trimmed. `maximum` is its length in characters, and a value past
            # it is REFUSED rather than cut: a truncated persona is one missing
            # its last paragraph with nothing anywhere saying so.
            if not isinstance(value, str):
                raise SettingsError(f"{key} must be text")
            cleaned = value.strip()
            if spec.maximum is not None and len(cleaned) > spec.maximum:
                raise SettingsError(
                    f"{key} is {len(cleaned)} characters and may be at most "
                    f"{int(spec.maximum)}")
            return cleaned
        if not isinstance(value, str) or value not in spec.choices:
            raise SettingsError(
                f"{key} must be one of {list(spec.choices)}, got {value!r}")
        return value

    def set(self, key: str, value) -> object:
        cleaned = self.validate(key, value)
        self.update({key: cleaned})
        return cleaned

    def update(self, changes: dict) -> dict:
        """Apply several settings, or none.

        Validated in full BEFORE anything is written, so a form with one bad
        field does not half-apply. Half-applied settings are the worst
        outcome here: the user sees an error, assumes nothing happened, and
        Butler behaves differently anyway.
        """
        cleaned = {k: self.validate(k, v) for k, v in changes.items()}
        for keys, refusal in DIFFERENT:
            after = [cleaned.get(k, self.get(k)) for k in keys]
            if any(k in cleaned for k in keys) and len(set(after)) == 1:
                raise SettingsError(refusal.format(after[0]))
        # AC-804: a save that leaves every wake word off would leave nothing
        # to start a spoken turn with. Refused like `DIFFERENT`, whole.
        wake = [wake_key(w.id) for w in WAKE_WORDS]
        if any(k in cleaned for k in wake) and all(
                cleaned.get(k, self.get(k)) == "off" for k in wake):
            raise SettingsError("At least one wake word must stay on.")
        applied ={k: v for k, v in cleaned.items() if self.get(k) != v}
        homeless = sorted(k for k in applied
                          if k in FILE_KEYS and k not in self._files)
        if homeless:
            # Refused BEFORE anything is written, for this method's rule.
            raise SettingsError(
                f"{homeless} are kept in files and this settings store was "
                f"built without them")
        for key, value in applied.items():
            if key in FILE_KEYS:
                self._files[key].write(value)
            else:
                self._values[key] = value
        if any(k not in FILE_KEYS for k in applied):
            self._write()
        for key, value in applied.items():
            for fn in list(self._listeners):
                fn(key, value)
        return applied

    def reset(self, group: str | None = None) -> dict:
        """Back to defaults, for one group or all of them."""
        keys = [s.key for s in SCHEMA if group is None or s.group == group]
        if group is not None and not keys:
            raise SettingsError(f"unknown group {group!r}")
        return self.update({k: DEFAULTS[k] for k in keys})

    def on_change(self, fn) -> None:
        self._listeners.append(fn)


def _whole_utterance(text: str) -> str:
    """The utterance, normalized for a whole-utterance match.

    One function for both matchers below, because two copies of the
    normalization drift and the drift is invisible: a phrase would start
    matching one matcher and not the other, and which one you got would
    depend on which file was edited last.
    """
    cleaned = " ".join((text or "").lower().split()).strip(" .,!?")
    for prefix in ("switch to ", "go to ", "use ", "change to "):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):]
    return cleaned


def spoken_mode_request(text: str) -> str | None:
    """Which turn mode this utterance is asking for, if any.

    Matched on the WHOLE utterance rather than as a substring. "switch to
    long form" is an instruction; "I think we should switch to long form
    later" is a sentence about one, and submitting a mode change instead of
    an answer would be maddening.
    """
    cleaned = _whole_utterance(text)
    for mode, phrases in SPOKEN_MODES.items():
        if cleaned in phrases:
            return mode
    return None


# How spoken project NAVIGATION is introduced (AC-31, AC-290). A PREFIX at the
# start of the utterance and the project's name after it, so "I would work in
# butler if I could" is a sentence about one rather than an instruction.
# NAVIGATION since 2026-09-26 (`docs/filing-and-pacing.md` 8, R-C): the
# phrase opens that project's newest chat, or births one there, and never
# moves a chat -- so "go to" joined "work in" (D-C1).
#
# Two grammars, because they carry different authority. The word "project"
# makes an utterance NAVIGATION and nothing else, so a name it does not know
# is refused out loud (AC-31). The bare form is ambiguous by nature -- "work
# on the endpointing module" and "work in butler and fix the failing tests"
# are work, and most sentences that begin this way are -- so it only counts
# when the name is EXACTLY a configured project, and otherwise falls through
# to the agent. Refusing those aloud made the feature a liability: the
# instruction never reached anybody.
EXPLICIT_PROJECT_PREFIXES = ("work in project ", "work on project ",
                             "switch to project ", "go to project ")
BARE_PROJECT_PREFIXES = ("work in ", "work on ", "switch to ", "go to ")


@dataclass(frozen=True)
class ProjectRequest:
    """A spoken navigation, and how much authority its phrasing carries.

    `explicit` is the word "project": it says the utterance can only be
    navigation, which is what earns the spoken refusal on a name that matches
    nothing. The caller needs both halves, and returning them together is
    what stops a second parse of the same string drifting from this one.
    """
    name: str
    explicit: bool


def spoken_project_request(text: str) -> ProjectRequest | None:
    """The project NAME this utterance is asking to go to, if any.

    Only the name: whether it exists is `match_project`'s question, and
    keeping them apart is what lets the refusal say what it HEARD (AC-31).

    Disjoint from the two whole-utterance matchers BY CONSTRUCTION, not by
    the order the daemon happens to test them in: "switch to long form"
    starts with a bare prefix here and is a turn-mode phrase there, and an
    utterance two matchers claim would be decided by which branch was written
    first. A project that shares a name with one of those phrases is still
    reachable by the explicit form.
    """
    cleaned = " ".join((text or "").lower().split()).strip(" .,!?")
    for prefix in EXPLICIT_PROJECT_PREFIXES:
        if cleaned.startswith(prefix):
            name = cleaned[len(prefix):].strip()
            return ProjectRequest(name, True) if name else None
    if spoken_mode_request(cleaned) or spoken_new_conversation(cleaned):
        return None
    for prefix in BARE_PROJECT_PREFIXES:
        if cleaned.startswith(prefix):
            name = cleaned[len(prefix):].strip()
            return ProjectRequest(name, False) if name else None
    return None


def _spoken_form(text: str) -> str:
    """Lower case, with `-` and `_` read as spaces -- nobody says "smart dash
    crawler". The ONE normalization every side of a spoken project name goes
    through: a folder's tail, a row's label, and what was heard or typed
    (`match_project`). Before this was shared, only the folder side went
    through it, so a typed hyphenated name never matched the spoken form of
    the same folder (docs/e2e-journeys.md S11).
    """
    return " ".join(str(text or "").lower().replace("-", " ")
                    .replace("_", " ").split())


def project_name(path: str) -> str:
    """The spoken form of a project: its final path component, normalized.

    Recorded in docs/harness-mode.md 7.7: a project is a directory spoken by
    its last part.
    """
    tail = PurePath(str(path).strip().rstrip("\\/")).name
    return _spoken_form(tail)


class AmbiguousProject(RuntimeError):
    """Two different configured directories answer to one spoken name.

    Unreachable while the list was a setting the owner curated by hand and one
    entry long; reachable now that the list is every row in the shortcuts pane
    (the owner's backlog item 4). `E:\\PROJECTS\\butler` and
    `E:\\ARCHIVE\\butler` are both "butler", and picking either is the guess
    AC-31 exists to refuse -- so this is a TIGHTENING of that criterion rather
    than a softening of it, and it fails the same way: aloud, naming what was
    heard, changing nothing.
    """

    def __init__(self, name: str, paths):
        self.name = name
        self.paths = list(paths)
        super().__init__(
            f"{name!r} names {len(self.paths)} configured projects "
            f"({self.paths}); Butler does not guess a working directory for a "
            f"tool-enabled agent (AC-31)")


def _row_names(row) -> set[str]:
    """Every spoken form ROW answers to: its folder name, always, and its
    LABEL when it has one.

    The tree shows the label, not the folder -- a row renamed "classes" over
    a folder called CRS101 was unreachable by the name on screen, because
    only the folder side was ever matched (docs/e2e-journeys.md S11). Both go
    through `_spoken_form`, the same normalization the heard/typed side uses.
    """
    names = {project_name(getattr(row, "path", ""))}
    label = getattr(row, "label", "")
    if label:
        names.add(_spoken_form(label))
    return names


def match_project(heard: str, rows) -> str | None:
    """The configured path this spoken name names, EXACTLY, or None.

    `rows` is a sequence of objects each carrying `.path` and `.label` -- a
    `Shortcut`, or anything shaped the same way (`hub.py` passes
    `self.shortcuts.all()`). No fuzz, no prefix, no nearest neighbour: the
    cost of a guess is a tool-enabled agent in the wrong repository, so an
    unrecognised project fails closed rather than falling back to anything
    (AC-31). A directory that is not on the list is unreachable by voice
    even if it exists on disk, and an empty list -- a daemon whose pane has
    no rows -- refuses everything.

    Raises `AmbiguousProject` when two DIFFERENT directories answer to the
    same name -- whether both by folder name, both by label, or one of each.
    Deduped on PATH first, so a row whose label equals its own folder name
    answers to itself from two directions and that is one project, not two.
    """
    wanted = _spoken_form(heard)
    if not wanted:
        return None
    hits = {str(getattr(row, "path", "")) for row in rows or ()
            if wanted in _row_names(row)}
    if not hits:
        return None
    if len(hits) > 1:
        raise AmbiguousProject(wanted, sorted(hits))
    return next(iter(hits))


def spoken_remember(text: str) -> str | None:
    """The note this utterance asks Butler to keep, or None if it is not a
    "remember that" request.

    "" means the phrase with nothing after it -- the caller asks "Remember
    what?" and writes nothing (AC-378). The rest is returned as the owner's
    own words, only the joining punctuation after the phrase trimmed, so what
    is stored is what was heard (AC-378). A WORD match: "remember thatcher"
    is not the phrase.
    """
    said = (text or "").strip()
    head = SPOKEN_REMEMBER_PREFIX
    if not said.lower().startswith(head):
        return None
    rest = said[len(head):]
    if rest and rest[0].isalnum():
        return None
    rest = rest.lstrip(" ,:;-").strip()
    return rest if any(c.isalnum() for c in rest) else ""


def spoken_forget(text: str) -> bool:
    """Is this utterance "forget that", and nothing else? (AC-379)"""
    return _whole_utterance(text) in SPOKEN_FORGET


def spoken_new_conversation(text: str) -> bool:
    """Is this utterance asking to start the harness conversation over?

    Whole-utterance, like the mode phrases, and for a sharper reason: this
    one throws the agent's context away. "add a new conversation view to the
    settings page" is work, not an instruction to forget the work (AC-35).
    """
    return _whole_utterance(text) in SPOKEN_NEW_CONVERSATION


# -- the current chat's model by voice (docs/chat-model.md CM-10, AC-787) --- #
# A PREFIX and the model's name after it, `BARE_PROJECT_PREFIXES`' grammar and
# its reason: "use the logger in the parser" is work. The EXPLICIT forms carry
# the word "model" or name the chat, so they can only be a model choice and a
# name matching nothing is refused aloud; a BARE "use X" / "switch to X" is a
# choice only when X matches a model, and otherwise falls through to the
# agent. Disjoint from the turn-mode and new-conversation phrases by the
# exclusion in `spoken_model_request`, from the explicit project form by
# refusing a name that starts "project", and from a bare project name by
# `Hub._names_a_project` (a configured project's name is never a model).
SPOKEN_MODEL_DEFAULT = ("back to the default model",
                        "go back to the default model",
                        "switch back to the default model")
EXPLICIT_MODEL_PREFIXES = ("change the model to ", "switch the model to ",
                           "set the model to ", "use model ",
                           "switch to model ")
BARE_MODEL_PREFIXES = ("use ", "switch to ")
MODEL_CHAT_SUFFIXES = (" on this chat", " for this chat", " in this chat",
                       " on this conversation", " for this conversation",
                       " in this conversation")


@dataclass(frozen=True)
class ModelRequest:
    """A spoken model choice for the current chat. `name` "" is "back to the
    agent's default"; `explicit` earns the spoken refusal of an unknown name."""
    name: str
    explicit: bool


def spoken_model_request(text: str) -> ModelRequest | None:
    """The model NAME this utterance asks the current chat to use, if any.
    Whether it exists is `match_models`' question, so a refusal can say what
    was HEARD."""
    cleaned = _cleaned(text)
    if cleaned in SPOKEN_MODEL_DEFAULT:
        return ModelRequest("", True)
    if spoken_mode_request(cleaned) or spoken_new_conversation(cleaned):
        return None
    for prefixes, explicit in ((EXPLICIT_MODEL_PREFIXES, True),
                               (BARE_MODEL_PREFIXES, False)):
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                return _model_named(cleaned[len(prefix):], explicit)
    return None


def _model_named(rest: str, explicit: bool) -> ModelRequest | None:
    for suffix in MODEL_CHAT_SUFFIXES:
        if rest.endswith(suffix):
            rest, explicit = rest[:-len(suffix)], True
            break
    if rest == "model" or rest.endswith(" model"):
        rest, explicit = rest[:-len("model")].strip(), True
    words = rest.split()
    rest = " ".join(words[1:] if words[:1] == ["the"] else words)
    if rest == "default":
        return ModelRequest("", True)
    if not rest or rest.split()[0] == "project":
        return None
    return ModelRequest(rest, explicit)


def _model_words(name: str) -> str:
    """A model name as words: lower case, `- _ / :` read as spaces, so
    "deepseek chat" is heard in `deepseek/deepseek-chat`."""
    for sep in "-_/:":
        name = name.replace(sep, " ")
    return " ".join(name.lower().split())


def match_models(heard: str, favourites, catalogue) -> list[str]:
    """Every model `heard` names, from the favourites the agent lists first,
    then the agent's whole catalogue (CM-10). Within a tier an EXACT name (the
    whole name or its part after the last "/") beats a substring; a substring
    must start at a word. Several are returned, never one picked: the caller
    asks which (AC-787)."""
    wanted = _model_words(heard)
    if not wanted:
        return []
    listed = list(dict.fromkeys(catalogue))
    known = set(listed)
    favs = [m for m in dict.fromkeys(favourites or ()) if m in known]
    for pool in (favs, listed):
        exact = [m for m in pool if wanted in (
            _model_words(m), _model_words(m.rsplit("/", 1)[-1]))]
        if exact:
            return exact
        hits = [m for m in pool if f" {wanted}" in f" {_model_words(m)}"]
        if hits:
            return hits
    return []


def model_spoken_names(models) -> list[str]:
    """How each model is SAID: its part after the last "/" as words, or its
    whole name when two of those would sound the same."""
    tails = [_model_words(m.rsplit("/", 1)[-1]) for m in models]
    if len(set(tails)) == len(tails):
        return tails
    return [_model_words(m) for m in models]


# -- recipes and tasks by voice (docs/automations.md section 8) ------------- #
# English only, like every command table here (language.md D5). Each matcher
# is disjoint from the ones above BY CONSTRUCTION -- none of their phrases
# starts with "run", "add task", "mark" or "what are my tasks" -- and a test
# holds them apart, because an utterance two matchers claim is decided by
# branch order rather than by meaning.
#
# "run recipe X" can only be a run, so a name matching nothing is refused
# aloud. A BARE "run X" is a run only when X is EXACTLY a recipe's spoken
# name (`match_recipes`); anything else -- "run the tests" -- falls through
# to the agent as work, `BARE_PROJECT_PREFIXES`' own rule and its reason.
EXPLICIT_RUN_PREFIXES = ("run recipe ",)
BARE_RUN_PREFIXES = ("run ",)
SPOKEN_TASK_ADD_PREFIXES = ("add a task", "add task")
SPOKEN_TASK_LIST = ("what are my tasks", "list my tasks", "read my tasks")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_TASK_DONE = re.compile(r"^mark (.+?) (?:as )?done$")


@dataclass(frozen=True)
class RunRequest:
    """A spoken recipe run, and whether the word "recipe" made it explicit."""
    name: str
    explicit: bool


@dataclass(frozen=True)
class TaskDone:
    """ "mark task N done" (a number just read back) or "mark X done"."""
    number: int | None
    text: str | None


def _cleaned(text: str) -> str:
    return " ".join((text or "").lower().split()).strip(" .,!?")


def spoken_run_request(text: str) -> RunRequest | None:
    """The recipe NAME this utterance asks to run, if any. Whether it exists
    is `match_recipes`' question, so the refusal can say what was HEARD."""
    cleaned = _cleaned(text)
    for prefixes, explicit in ((EXPLICIT_RUN_PREFIXES, True),
                               (BARE_RUN_PREFIXES, False)):
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                name = cleaned[len(prefix):].strip()
                if explicit or not name.startswith("recipe"):
                    return RunRequest(name, explicit) if name else None
                return None
    return None


def match_recipes(heard: str, recipes) -> list:
    """Every recipe whose name, as spoken, IS what was heard (AC-451). No
    fuzz and no prefix: a run on a guess is a coding agent started on a
    guess. The caller refuses two."""
    wanted = _spoken_form(heard)
    return [r for r in recipes or () if wanted
            and _spoken_form(getattr(r, "name", "")) == wanted]


def spoken_task_add(text: str) -> str | None:
    """The task this utterance adds, VERBATIM after the phrase, or None.
    "" means the phrase alone -- the caller asks "Add what?". A WORD match:
    "add tasks to the backlog" is work."""
    said = (text or "").strip()
    low = said.lower()
    for head in SPOKEN_TASK_ADD_PREFIXES:
        if not low.startswith(head):
            continue
        rest = said[len(head):]
        if rest and rest[0].isalnum():
            return None
        rest = rest.lstrip(" ,:;-").strip().rstrip(".!?").strip()
        return rest if any(c.isalnum() for c in rest) else ""
    return None


def spoken_task_list(text: str) -> bool:
    """Is this utterance asking to hear this project's open tasks?"""
    return _cleaned(text) in SPOKEN_TASK_LIST


def spoken_task_done(text: str) -> TaskDone | None:
    """ "mark task 2 done" / "mark task two done" / "mark X (as) done"."""
    m = _TASK_DONE.match(_cleaned(text))
    if not m:
        return None
    what = m.group(1).strip()
    head, _, tail = what.partition(" ")
    if head == "task" and tail:
        if tail.isdigit():
            return TaskDone(int(tail), None)
        if tail in _NUMBER_WORDS:
            return TaskDone(_NUMBER_WORDS[tail], None)
    if what in ("it", "that", "this", "task"):
        return None
    return TaskDone(None, what)


# -- reminders by voice (docs/proactive.md PR-20, AC-490) -------------------- #
# Every shape starts "remind me", which no phrase above starts with -- not
# "run ", "add task", "mark ", "what are my tasks", "remember that" -- so the
# matchers stay disjoint BY CONSTRUCTION (test_reminders.py holds them
# apart). English only, like every command table here (language.md D5);
# every other phrasing and language reaches the brain and its `reminder_add`
# tool. A shape that does not parse WHOLE -- an hour of 13 pm, a minute of
# 75, no "to" -- is not a command and falls through to the brain too.
_HOUR_WORDS = {**_NUMBER_WORDS, "eleven": 11, "twelve": 12}
_WORDS = "|".join(_HOUR_WORDS)
_CLOCK = (r"(?P<h>\d{1,2}|" + _WORDS + r")(?::(?P<m>\d{2}))?"
          r"(?:\s?(?P<ap>[ap])\.?\s?m\.?)?")
_REMIND_AT = (
    # "remind me to X at T" / "remind me to X tomorrow at T"
    re.compile(r"remind me to (?P<what>.+?)(?P<tom> tomorrow)? at " + _CLOCK,
               re.IGNORECASE),
    # "remind me at T to X" / "remind me tomorrow at T to X"
    re.compile(r"remind me (?P<tom>tomorrow )?at " + _CLOCK
               + r",? to (?P<what>.+)", re.IGNORECASE),
)
_REMIND_IN = re.compile(r"remind me in (?P<n>\d{1,3}|an?|" + _WORDS
                        + r") (?P<unit>minutes?|mins?|hours?) to "
                        r"(?P<what>.+)", re.IGNORECASE)
#: A pending schedule proposal's answer (PR-21). Whole utterances only, and
#: matched ONLY while a proposal waits in that conversation, so "yes" means
#: nothing to Butler at any other time. "sim"/"nao" because the read-back is
#: English but the person answering may not be.
SPOKEN_YES = ("yes", "yes please", "yeah", "yep", "sure", "do it",
              "create it", "ok", "okay", "sim")
SPOKEN_NO = ("no", "nope", "no thanks", "cancel", "don't", "do not",
             "nao", "n\u00e3o")


@dataclass(frozen=True)
class RemindRequest:
    """One spoken reminder: the owner's words, and WHEN as heard -- either
    `minutes` from now, or a wall clock `hour`:`minute` with an optional
    am/pm (`meridiem` "a"/"p") and whether it said `tomorrow`. Turning that
    into an instant needs the clock and the zone, so it is
    `automations.remind_instant`'s, not this parser's."""
    text: str
    minutes: int | None = None
    hour: int = 0
    minute: int = 0
    meridiem: str | None = None
    tomorrow: bool = False


def _count(word: str) -> int:
    word = word.lower()
    if word in ("a", "an"):
        return 1
    return int(word) if word.isdigit() else _HOUR_WORDS[word]


def spoken_remind(text: str) -> RemindRequest | None:
    """The reminder this utterance sets, or None. The text is the owner's
    own words, case kept, trailing punctuation trimmed."""
    said = " ".join((text or "").split()).strip(" .,!?")
    m = _REMIND_IN.fullmatch(said)
    if m:
        n = _count(m.group("n"))
        what = m.group("what").strip(" .,!?")
        if not n or not any(c.isalnum() for c in what):
            return None
        hours = m.group("unit").lower().startswith("h")
        return RemindRequest(what, minutes=n * 60 if hours else n)
    for shape in _REMIND_AT:
        m = shape.fullmatch(said)
        if not m:
            continue
        what = m.group("what").strip(" .,!?")
        h = _count(m.group("h"))
        minute = int(m.group("m")) if m.group("m") else 0
        ap = m.group("ap").lower() if m.group("ap") else None
        if minute > 59 or (ap and not 1 <= h <= 12) or h > 23 \
                or not any(c.isalnum() for c in what):
            return None
        return RemindRequest(what, hour=h, minute=minute, meridiem=ap,
                             tomorrow=bool(m.group("tom")))
    return None


def spoken_confirm(text: str) -> bool | None:
    """True for a yes, False for a no, None for anything else."""
    said = _cleaned(text)
    if said in SPOKEN_YES:
        return True
    if said in SPOKEN_NO:
        return False
    return None

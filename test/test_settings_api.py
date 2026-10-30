"""Every setting reachable from the page it serves. AC-8, D-2, AC-10.

The spec these encode: nothing that changes behaviour should be reachable
only by editing a file, two devices must never disagree, a bad value must
change nothing, and a setting that needs a restart must say so rather than
appearing to work.
"""
import asyncio
import json
import sys
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import attach_session, make_hub                   # noqa: E402
from test_playback_gate import confirm_playback_when_asked      # noqa: E402
from daemon.settings import (BY_KEY, DEFAULTS, GROUPS, SCHEMA,  # noqa: E402
                             Settings, SettingsError)


# --------------------------------------------------------------------------- #
# the schema itself
# --------------------------------------------------------------------------- #
def test_every_setting_belongs_to_a_named_group():
    """The page renders by group; an orphan would be invisible on the form
    while still affecting behaviour."""
    for s in SCHEMA:
        assert s.group in GROUPS, f"{s.key} is in unknown group {s.group!r}"


def test_every_setting_has_a_label_and_a_valid_default():
    for s in SCHEMA:
        assert s.label, f"{s.key} has no label"
        Settings.validate(Settings.__new__(Settings), s.key, s.default)


def test_numbers_declare_their_range():
    """A number with no bounds is a free-text box that can wedge the daemon
    -- a 0 ms endpoint check would spin, a 0 ms ceiling would end every turn
    instantly."""
    for s in SCHEMA:
        if s.kind == "number":
            assert s.minimum is not None and s.maximum is not None, (
                f"{s.key} is a number with no range")


def test_choices_exist_for_every_choice_setting():
    for s in SCHEMA:
        if s.kind == "choice":
            assert s.choices, f"{s.key} is a choice with no choices"


def test_every_label_names_a_choice_that_exists():
    """AC-66. `labels` is a second table beside `choices`, and two tables that
    describe the same thing drift: a label for a value that was renamed leaves
    a button the page shows and the daemon rejects, and a MISSING label leaves
    one option reading as a raw identifier next to two English ones.

    Checked both ways for that reason -- every label names a choice, and a
    spec that carries labels at all labels ALL of them."""
    for s in SCHEMA:
        if not s.labels:
            continue
        assert s.kind == "choice", (
            f"{s.key} carries labels but is a {s.kind}; nothing renders them")
        assert set(s.labels) == set(s.choices), (
            f"{s.key}'s labels and choices disagree: "
            f"{sorted(set(s.labels) ^ set(s.choices))}")
        for value, text in s.labels.items():
            assert text.strip(), f"{s.key}[{value}] has a blank label"


def test_the_three_brain_modes_are_offered_with_readable_labels(tmp_path):
    """AC-66, on the wire the page actually reads.

    The VALUES must stay `endpoint` and `harness`: an existing settings.json
    holds one of them, and renaming a stored value to make a control read
    better boots that install into a mode nobody chose. So the labels change
    and the values do not, and both halves cross the wire.

    Every value must also be something `build_hub` can construct
    (`BRAIN_MODES`), or the owner picks a mode, restarts, and the daemon does
    not come back -- AC-7b with a restart in the middle of it.
    """
    from daemon import butlerd

    entry = next(d for d in Settings(tmp_path / "s.json").describe()["settings"]
                 if d["key"] == "brain_mode")

    assert entry["choices"] == ["endpoint", "harness", "harness_only"], (
        f"the stored values changed, so an existing settings.json now names a "
        f"mode that no longer exists: {entry['choices']}")
    # AC-813: "coding agent", never "harness", to a person.
    assert entry["labels"] == {"endpoint": "Chat only",
                               "harness": "Chat + coding agent",
                               "harness_only": "Coding agent only"}, (
        entry["labels"])
    assert set(entry["choices"]) == set(butlerd.BRAIN_MODES), (
        f"the page offers a mode the daemon cannot build, or hides one it "
        f"can: {entry['choices']} against {sorted(butlerd.BRAIN_MODES)}")
    assert entry["restart"] is True, (
        "brain_mode lost its restart flag; the Brain composite is still built "
        "once in build_hub")


@pytest.mark.parametrize("mode,dead,live", [
    # brain only: the whole harness seam does nothing, the router does.
    ("endpoint", ("harness", "harness_model", "harness_tools"),
     ("router_model", "endpoint_model")),
    # brain + harness: the coding agent answers, so the conversation model
    # does nothing -- true since harness mode shipped and never said so.
    ("harness", ("endpoint_model", "reply_reasoning"),
     ("router_model", "harness_model")),
    # harness only: no router either, which is the three NEW dead controls
    # this mode created.
    ("harness_only", ("router_model", "router_reasoning", "endpoint_model"),
     ("harness_model", "harness_tools")),
])
def test_each_mode_says_which_controls_it_switches_off(tmp_path, mode, dead,
                                                       live):
    """AC-66/AC-7b: a control that saves, reports success and changes nothing.

    Adding `harness_only` created three of them -- the router settings, with
    no router in the path -- so the schema has to say so or the page shows the
    owner a model picker that decides nothing. Asserted in BOTH directions,
    because marking everything inert would pass a one-sided check while
    dimming controls that work, and that is the same lie from the other side.

    The value is a REASON rather than a boolean, so the page can say why
    without naming a setting or a mode (AC-41).
    """
    s = Settings(tmp_path / "s.json")
    s.set("brain_mode", mode)
    described = {d["key"]: d for d in s.describe()["settings"]}

    for key in dead:
        assert described[key]["inactive"], (
            f"{key} does nothing in {mode!r} and the page is not told")
    for key in live:
        assert described[key]["inactive"] == "", (
            f"{key} was dimmed in {mode!r}, where it decides something")
    assert described["brain_mode"]["inactive"] == "", (
        "the mode switch dimmed itself, so the owner cannot leave the mode")


def test_a_group_is_only_marked_inactive_when_all_of_it_is(tmp_path):
    """The pipeline box the page dims (AC-45, AC-66).

    A group flag exists so the page can grey a whole stage without knowing
    what a harness is. It must mean what it says: the brain group holds
    `brain_mode` itself and so is never entirely inert, and greying that box
    would hide the one control the owner needs to get back out of the mode.
    """
    s = Settings(tmp_path / "s.json")
    for mode in ("endpoint", "harness", "harness_only"):
        s.set("brain_mode", mode)
        groups = {g["key"]: g for g in s.describe()["groups"]}
        settings = s.describe()["settings"]
        assert groups["brain"]["inactive"] is False, (
            f"the brain box was greyed in {mode!r}, hiding the mode switch")
        assert groups["harness"]["inactive"] is (mode == "endpoint"), (
            f"the harness box's state is wrong in {mode!r}")
        for key, group in [(g["key"], g) for g in groups.values()]:
            members = [d for d in settings if d["group"] == key]
            if group["inactive"]:
                assert all(d["inactive"] for d in members), (
                    f"group {key!r} is marked inactive while some of its "
                    f"settings are live")


def test_the_engine_settings_are_marked_restart():
    """AC: a setting that needs a restart must say so. Model and backend are
    read once when the recognizer is constructed and load a GGUF, so a UI
    that reported them as live would be lying; since AC-715 the daemon
    restarts ITSELF to apply them once nothing is running, and the flag is
    what tells it (and the page) so.

    `brain_mode` is the same shape one seam further out: `butlerd.build_hub`
    reads it once to build the Brain composite.

    `asr_latency` and `endpointer` carried the flag until 2026-10-27 and are
    swapped live now (AC-713): asserted in `test_settings_apply.py`, beside
    the caller-side proof that the next capture uses the new engine.

    `harness` is the counter-example, and it is asserted rather than merely
    omitted. It carried the flag until AC-46 was replaced on 2026-08-18;
    since a child is built per message from the current setting, a restart
    warning there would tell the owner to restart a daemon that has already
    taken the change. A stale flag is AC-7b pointing the other way -- the
    control works and the page says it does not.
    """
    for key in ("asr_model", "asr_backend", "brain_mode"):
        assert BY_KEY[key].restart, f"{key} is not marked as needing a restart"
    # `language` was `asr_language` and carried this flag until 2026-09-10.
    # It is not on the list above because it is no longer true of it: the
    # locale is an argument to `stream()`, the pipeline is rebuilt every turn
    # and the GGUF is cached per (path, backend), so nothing reloads and the
    # change lands on the next thing said. Asserted rather than merely
    # omitted, for `harness`'s reason -- a stale flag is AC-7b pointing the
    # other way, telling the owner to restart for a change already taken.
    assert not BY_KEY["language"].restart, (
        "the `language` setting is flagged as needing a restart, but the "
        "turn path reads it per turn (AC-152)")
    assert not BY_KEY["harness"].restart, (
        "the `harness` setting is flagged as needing a restart, but the "
        "adapter is built per message from it (AC-46)")


def test_how_the_page_looks_is_a_server_setting_not_browser_state():
    """The satellite page has a style, a theme, a wave placement, a reading
    face and a text size, and not one of them may live in the browser.

    localStorage is per-origin, and this project has already paid for that
    once: one laptop held four disagreeing copies of a voice setting because
    the app answered on four origins (D-2, AC-8). A look preference is not
    exempt -- the phone and the desk showing different themes is the same
    defect wearing a smaller hat, and there is a grep gate over the shipped
    client files that would fail the moment someone reached for a shortcut.

    The restart flag is asserted in the direction that is true of all of them
    (AC-167, AC-173, AC-678): the page re-reads them per change, so a badge
    telling the owner to restart would be AC-7b pointing the other way --
    the control works and the panel says it does not.
    """
    for key in ("ui_style", "theme", "viz_placement", "reading_face",
                "text_scale"):
        spec = BY_KEY.get(key)
        assert spec is not None, f"{key} is not in the schema"
        assert spec.kind == "choice" and spec.choices, f"{key} has no choices"
        assert not spec.restart, f"{key} must apply without a restart"
        assert spec.group == "look", (
            f"{key} is how the page looks but lives in {spec.group!r}")


def test_text_scale_is_capped_at_1_15_and_defaults_a_little_larger():
    """R7 (CLAUDE.md rulings of 2026-09-16): the owner's ask was "a little
    larger... for older users", and the cap is DELIBERATE -- 1.15 is what the
    existing fixed-px boxes (44px controls, `.chatrow`, `.act`, settings
    rows) hold before an edge clips; moving those to `em` was declined for
    this pass. The default moves from what shipped (1.0, unstated) to 1.08
    rather than to the cap, so nobody who never opens this control is handed
    the largest size untested.
    """
    spec = BY_KEY["text_scale"]
    assert spec.choices == ("1.0", "1.08", "1.15"), (
        f"text_scale offers {spec.choices!r}, not the capped three the "
        f"owner ruled on")
    assert spec.default == "1.08", (
        f"text_scale defaults to {spec.default!r}, not the owner's "
        f"'a little larger' value")


def test_the_appearance_group_is_the_one_offered_outside_the_settings_page():
    """The owner's ask of 2026-09-11: the header pill offers every appearance
    option, not dark and light.

    Asserted on the DAEMON because that is where the answer has to live. The
    page renders whichever groups carry `quick` and holds no list of keys of
    its own (AC-41), so this flag IS the feature: with it off the pill opens
    on nothing, and with it on a fifth appearance setting appears there with
    no edit in `web/`.

    The second half is the rule `_validated_groups` enforces, asserted here on
    the group Butler actually ships rather than only on a constructed one: a
    restart-flagged setting on that pill would save, report success and change
    nothing, with no restart badge and no commit bar anywhere near it (AC-7b,
    AC-86).
    """
    quick = [key for key, g in GROUPS.items() if g.quick]
    assert quick == ["look"], (
        f"the appearance group is what the pill offers; quick groups are "
        f"{quick}")
    owed = [s.key for s in SCHEMA if s.group == "look" and s.restart]
    assert owed == [], (
        f"{owed} is drawn on the main page and needs a restart to take "
        f"effect, which that surface has no way to say")


def test_a_quick_group_may_not_hold_a_restart_flagged_setting():
    """D-13's rule applied to the guard above: the refusal is at import, where
    the table is DEFINED, so it cannot be reached by reading the shipped
    schema. Driven here over a constructed one.

    The legal case is half the instrument -- a validator that refused every
    quick group would pass the illegal case and be deleted the first time
    anybody looked at it.
    """
    from daemon.settings import Group, _validated_groups

    # `look` holds no restart-flagged setting, so marking it quick is legal.
    ok = {"look": Group("Appearance", None, "theme", quick=True,
                         section="you")}
    assert _validated_groups(ok) is ok
    # `stt` holds `asr_engine`, which is restart-flagged.
    restarting = [s.key for s in SCHEMA if s.group == "stt" and s.restart]
    assert restarting, (
        "this case needs a group with a restart-flagged setting in it; "
        "nothing in `stt` is flagged any more, so pick another group")
    with pytest.raises(ValueError, match="quick"):
        _validated_groups({"stt": Group("Recognition", 1, None, quick=True,
                                         section="voice")})


def test_the_composer_offers_three_models_and_the_tool_posture():
    """The owner's backlog item 5, 2026-09-11: pickers under the chat input for
    the brain, the harness and the vision model, plus read_only versus full.
    The vision model (Eyes) left on 2026-10-29 (AC-815): the model that
    answers sees the image itself.

    Asserted on the DAEMON for the same reason the appearance pill is: the page
    renders whatever carries `quick` and holds no list of keys of its own
    (AC-41). The flag IS the feature -- with it empty the row under the composer
    draws nothing, and a fifth picker appears there with no edit in `web/`.

    `quick` is a STRING and not a bool because the row has no space for
    "Conversation model". The short word is the daemon's to choose for the same
    reason the group label on the appearance pill is: a page that writes its own
    name for a setting is naming one.

    The ROUTER is deliberately absent. The owner asked for three models -- the
    brain, the harness and the vision model -- and a fourth control on a phone
    costs more than the router is reached for.

    The HARNESS model left this row on 2026-10-29 (docs/chat-model.md CM-6):
    it is now chosen PER CHAT on the composer's agent chip, and the setting
    is only the default (`test_chat_model.py`).
    """
    quick = {s.key: s.quick for s in SCHEMA if s.quick}
    assert quick == {"endpoint_model": "Brain", "harness_tools": "Tools"}, (
        f"the composer offers the brain model and the tool "
        f"posture (the agent's model is per chat); the "
        f"schema marks {sorted(quick)}")
    owed = sorted(s.key for s in SCHEMA if s.quick and s.restart)
    assert owed == [], (
        f"{owed} is drawn under the composer and needs a restart to take "
        f"effect, which that row has no way to say")


def test_a_quick_setting_may_not_be_restart_flagged():
    """The same rule `_validated_groups` applies to a quick GROUP, applied to
    the per-setting flag, and refused at import where the schema is defined.

    A picker sitting against the text box that saves, reports success and
    changes nothing until a restart nobody mentioned is AC-7b at the closest
    possible range: there is no restart badge and no commit bar within reach of
    that row.

    The legal case is half the instrument -- a validator that refused every
    quick setting would pass the illegal case and be deleted the first time
    anybody looked at it (D-13).
    """
    from daemon.settings import Setting, _validated_settings

    ok = (Setting("a", "", "brain", "A", quick="A"),
          Setting("b", "", "brain", "B", restart=True))
    assert _validated_settings(ok) is ok
    bad = (Setting("c", "", "brain", "C", quick="C", restart=True),)
    with pytest.raises(ValueError, match="quick"):
        _validated_settings(bad)


def test_a_setting_entry_tells_the_page_it_belongs_under_the_composer(tmp_path):
    """`quick` crosses the wire per setting, beside `favourites` and `restart`.

    The exact field set is pinned for the same reason the group entry's is: the
    page reads these by name and a field that silently stops being served is a
    control that silently stops being drawn.
    """
    served = Settings(tmp_path / "s.json").describe()["settings"]
    entry = next(d for d in served if d["key"] == "endpoint_model")
    assert set(entry) == {"key", "group", "label", "help", "kind",
                          "choices_from", "choices", "labels", "min", "max",
                          "default", "favourites", "advanced", "restart",
                          "quick", "inactive", "value", "changed",
                          # docs/identity-memory.md 11.2, AC-424
                          "problem",
                          # docs/lantern-ui.md AC-704: style cards
                          "preview"}, (
        f"a setting entry carries {sorted(entry)}")
    for d in served:
        assert isinstance(d["quick"], str), (
            f"{d['key']}'s quick flag is {d['quick']!r}; the page prints it as "
            f"the word on the control and True would read as 'true'")


def test_the_tool_posture_is_drawn_as_two_named_buttons():
    """AC-66's rule reaching the composer row. `read_only` and `full` are
    identifiers, and a dropdown of identifiers in a row of pills is the control
    nobody reads.

    A spec carrying `labels` is the daemon saying "lay these out as named
    buttons" (settings.js buildChoiceButtons), so the labels are what make the
    `[Read only | Full]` segment in the owner's chosen mock-up possible without
    `web/` naming either value.
    """
    spec = BY_KEY["harness_tools"]
    assert spec.labels == {"read_only": "Read only", "full": "Full"}, (
        f"harness_tools carries {spec.labels!r}; without labels the composer "
        f"row draws a select")
    assert set(spec.labels) == set(spec.choices), (
        "every choice needs a label or the button row omits one silently")


def test_the_visualiser_placement_offers_three_and_one_of_them_is_off():
    """AC-167, narrowed by CU-3 (docs/lantern-ui.md): the wave band retired,
    so the placements are the mic button, the full page and off. The DAEMON
    names them, including the one that draws nothing.

    `off` is asserted by name because it is the value with a rule attached:
    it stops the DRAWING and never the seven `viz.*` call sites, which
    `test_visualiser_contract.py` reads off `app.js`.
    """
    spec = BY_KEY["viz_placement"]
    assert spec.choices == ("mic", "ambient", "off"), (
        f"CU-3 leaves three placements; the schema offers {list(spec.choices)}")
    assert spec.default in spec.choices, spec.default
    assert set(spec.labels) == set(spec.choices), (
        "the placements are laid out as a row of named buttons, so every one "
        "of them needs a label (AC-66)")


def test_a_stored_band_placement_falls_back_loudly_once(tmp_path, capsys):
    """CU-3: `band` is no longer a placement. A settings file that still says
    so loads the default, says it once, and keeps the rest of the file."""
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"viz_placement": "band", "theme": "light"}),
                    encoding="utf-8")
    loaded = Settings(path)

    assert loaded["viz_placement"] == BY_KEY["viz_placement"].default
    assert loaded["theme"] == "light", "the stale band took the file with it"
    out = capsys.readouterr().out
    assert out.count("viz_placement") == 1, (
        f"the dropped band must be said exactly once, got: {out!r}")


def test_the_voice_effect_offers_eleven_and_the_default_is_the_lit_border():
    """AC-252, AC-262, reversed on the default by CU-3 (docs/lantern-ui.md,
    AC-690): `lantern` -- the composer's own border lit from the level -- is
    the new default, and `lattice` stays one choice among the rest.

    `aurora` was CUT (RULING 2, CLAUDE.md 2026-09-16 backlog) for its SHAPE
    rather than tuned for its brightness.
    """
    spec = BY_KEY["voice_effect"]
    assert spec.group == "look", (
        f"voice_effect is what the message box draws but lives in "
        f"{spec.group!r}")
    assert spec.default == "lantern", (
        f"voice_effect defaults to {spec.default!r}; CU-3 makes the lit "
        f"border the default")
    assert list(spec.choices) == [
        "off", "lantern", "lattice", "mist", "ember", "bloom", "ribbon",
        "sonar", "filaments", "comet", "dust",
    ], f"the schema offers {list(spec.choices)}"
    assert not spec.restart, (
        "voice_effect must apply without a restart, exactly like "
        "viz_placement beside it")


def test_the_viz_placement_help_says_it_only_applies_to_lattice():
    """AC-262 (AC-7b's rule): a control that cannot act on the current value
    must say so. `viz_placement` now answers a question -- where does the
    wave go -- that only matters when `voice_effect` is `lattice`; for the
    other ten values it is inert, and its help text must state that rather
    than let the owner turn a dead knob.
    """
    # In the words the sheet prints (AC-813): the setting's LABEL and the
    # value's label, never the key `voice_effect`.
    effect = BY_KEY["voice_effect"]
    help_text = BY_KEY["viz_placement"].help
    assert effect.label in help_text and effect.labels["lattice"] in help_text, (
        f"viz_placement's help does not say it only applies when "
        f"{effect.label} is {effect.labels['lattice']}: {help_text!r}")
    assert "voice_effect" not in help_text


@pytest.mark.asyncio
async def test_the_voice_effect_setting_round_trips_through_the_api(daemon):
    """Modelled on `test_the_api_serves_the_schema` /
    `test_a_rejected_write_returns_400_and_names_the_options`: a `look`
    setting reaches the page through the same route every other one does,
    and a bad value is refused rather than silently clamped.
    """
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{daemon}/api/settings",
                             json={"voice_effect": "sonar"}) as r:
            assert r.status == 200, await r.text()
            body = await r.json()
        posted = next(d for d in body["settings"] if d["key"] == "voice_effect")
        assert posted["value"] == "sonar", (
            f"the write answered {posted!r}, not the value just set")

        async with http.get(f"{daemon}/api/settings") as r:
            assert r.status == 200
            body = await r.json()
        stored = next(d for d in body["settings"] if d["key"] == "voice_effect")
        assert stored["value"] == "sonar", (
            "the setting did not persist between the POST and the GET")

        async with http.post(f"{daemon}/api/settings",
                             json={"voice_effect": "not-a-real-effect"}) as r:
            assert r.status == 400, await r.text()
            body = await r.json()
        assert "sonar" in body["error"] or "lattice" in body["error"], body

        # Leave the daemon as we found it for the rest of the session.
        await http.post(f"{daemon}/api/settings", json={"voice_effect": "lattice"})


#: AC-700's navigation, in the order the sheet lists it.
SECTIONS = [{"key": "voice", "label": "Voice"}, {"key": "you", "label": "You"},
            {"key": "connected", "label": "Connected"},
            {"key": "safety", "label": "Safety"}]


def test_every_group_names_the_settings_section_it_is_listed_under(tmp_path):
    """AC-700 (docs/lantern-ui.md CU-11). The settings sheet's navigation is
    the daemon's: the page may not decide that Brain is under Voice (AC-41),
    so the sections and their order are served, and every group says which
    one it belongs to."""
    expected = {"listen": "voice", "stt": "voice", "turn": "voice",
                "brain": "voice", "harness": "voice", "tts": "voice",
                "identity": "you", "look": "you", "quiet": "you",
                "notify": "connected", "triggers": "connected",
                "whatsapp": "connected", "devices": "connected",
                "access": "safety"}
    assert {k: g.section for k, g in GROUPS.items()} == expected
    described = Settings(tmp_path / "s.json").describe()
    assert described["sections"] == SECTIONS, described["sections"]
    for g in described["groups"]:
        assert g["section"] == expected[g["key"]], g


def test_a_group_outside_the_served_sections_is_refused_at_import():
    """A group whose section is not served would be drawn under no heading,
    which on a sheet of categories is not drawn at all. Refused where the
    table is defined; a blank section is the same defect (D-13: the legal
    case first)."""
    from daemon.settings import Group, _validated_groups

    ok = {"brain": Group("Brain", 1, "brain_mode", section="voice")}
    assert _validated_groups(ok) is ok
    for section in ("", "audio"):
        with pytest.raises(ValueError, match="section"):
            _validated_groups({"brain": Group("Brain", 1, "brain_mode",
                                              section=section)})


def test_ui_style_picks_lantern_or_caption_and_is_drawn_as_preview_cards(
        tmp_path):
    """AC-678, AC-704 (docs/lantern-ui.md CU-2). One `look` setting picks the
    style for every device, and Lantern is what an install that never opens
    the control gets. It is the FIRST setting of the group, because
    Appearance's first row is the style.

    Not restart-flagged: the page writes `data-style` on every settings read.
    `preview` crosses the wire because the page may not name the setting it
    draws as cards (AC-41); on every other setting it must stay off or that
    setting becomes cards.
    """
    spec = BY_KEY["ui_style"]
    assert spec.group == "look" and spec.kind == "choice"
    assert spec.choices == ("lantern", "caption"), spec.choices
    assert spec.labels == {"lantern": "Lantern", "caption": "Caption"}
    assert spec.default == "lantern", spec.default
    assert not spec.restart, "the page applies the style without a restart"
    assert spec.help.strip(), "the style needs help text in plain words"
    look = [s.key for s in SCHEMA if s.group == "look"]
    assert look[0] == "ui_style", f"the look group starts with {look[0]!r}"
    served = Settings(tmp_path / "s.json").describe()["settings"]
    previewed = sorted(d["key"] for d in served if d["preview"] is True)
    assert previewed == ["ui_style"], (
        f"preview cards are for the style alone; the schema serves "
        f"{previewed}")


def test_a_preview_flag_needs_a_labelled_choice():
    """A card shows a choice by NAME, so `preview` on a setting with no
    labels, or on something that is not a choice, would draw unnamed cards.
    Refused at import, beside the other per-setting flags. The legal case is
    half the instrument (D-13)."""
    from daemon.settings import Setting, _validated_settings

    ok = (Setting("a", "x", "look", "A", choices=("x", "y"),
                  labels={"x": "X", "y": "Y"}, preview=True),)
    assert _validated_settings(ok) is ok
    for bad in (Setting("b", "x", "look", "B", choices=("x", "y"),
                        preview=True),
                Setting("c", "", "look", "C", kind="text", preview=True)):
        with pytest.raises(ValueError, match="preview"):
            _validated_settings((bad,))


def test_reading_face_offers_the_style_default_first_and_keeps_every_face():
    """docs/lantern-ui.md CU-5: `reading_face` becomes an override of the
    face Butler's replies are set in. The empty value is "Style default",
    first and the new default; the page writes no face for it, so the
    style's own `--read` stands. No face that shipped is taken away.

    EVERY value is labelled, "" included: an unlabelled "" draws as a blank
    entry nobody can read, and AC-66 allows every label or none. The faces'
    labels are their own names."""
    spec = BY_KEY["reading_face"]
    assert spec.choices[0] == "" and spec.default == "", (
        f"reading_face offers {spec.choices!r} defaulting to "
        f"{spec.default!r}; the style default must be first and the default")
    faces = ("IBM Plex Sans", "Figtree", "Plus Jakarta Sans", "Inter",
             "Nunito Sans", "Atkinson Hyperlegible")
    assert spec.choices[1:] == faces, spec.choices
    assert spec.labels == {"": "Style default", **{f: f for f in faces}}, (
        spec.labels)


def test_theme_follows_the_device_on_auto():
    """CU-6, AC-698: `theme` gains `auto`, which follows the operating
    system's light or dark preference. Dark stays the default, so nobody's
    page changes on upgrade; every value is labelled, so it stays a row of
    buttons."""
    spec = BY_KEY["theme"]
    assert spec.choices == ("dark", "light", "auto"), spec.choices
    assert spec.default == "dark", spec.default
    assert set(spec.labels) == set(spec.choices), spec.labels


def test_colour_scheme_is_RETIRED_and_a_stored_value_is_not_carried(
        tmp_path, capsys):
    """CU-4 (docs/lantern-ui.md): each style owns its colours, so the five
    surface schemes fold into the two styles and the setting goes. A stored
    value is dropped by `_load`'s unknown-key line, LOUDLY, and costs nothing
    else in the file -- the `mic_style` retirement's shape."""
    assert "colour_scheme" not in BY_KEY, (
        "colour_scheme is back in the schema, so the page offers schemes "
        "that no style draws")

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"colour_scheme": "dusk", "theme": "light"}),
                    encoding="utf-8")
    loaded = Settings(path)

    assert loaded.get("colour_scheme") is None, "the retired key is readable"
    assert loaded["theme"] == "light", (
        "a retired key took the rest of the settings file with it")
    out = capsys.readouterr().out
    assert out.count("colour_scheme") == 1, (
        f"the dropped scheme must be said exactly once, got: {out!r}")


def test_vision_model_is_RETIRED_and_a_stored_value_is_dropped_loudly(
        tmp_path, capsys):
    """AC-815: the model that answers has the eyes, so the separate vision
    model goes. A settings.json written before that still holds a pair; it
    is dropped by `_load`'s unknown-key line, said ONCE, and the file loads
    (boot reads settings exactly this way) with the rest of it intact."""
    assert "vision_model" not in BY_KEY, (
        "vision_model is back in the schema, so the page offers Eyes again")

    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "vision_model": {"conn": "local", "model": "with-eyes"},
        "endpoint_model": {"conn": "local", "model": "chatty"}}),
        encoding="utf-8")
    loaded = Settings(path)

    assert loaded.get("vision_model") is None, "the retired key is readable"
    assert loaded["endpoint_model"] == {"conn": "local", "model": "chatty"}, (
        "a retired key took the rest of the settings file with it")
    out = capsys.readouterr().out
    assert out.count("vision_model") == 1, (
        f"the dropped vision model must be said exactly once, got: {out!r}")


def test_mic_style_is_RETIRED_and_a_stored_value_is_not_carried(tmp_path,
                                                                capsys):
    """docs/familiar-ui.md 6.4, asserted from both ends.

    The five microphone treatments are gone from the schema, because in the
    composer the mic is a 40 px button beside a text field. Asserted because
    a retirement that leaves the key readable is the worst version of the
    change: the page would stop offering the control while something went on
    reading whatever was last written to the file.

    The other end is the migration, and it is a DELETION rather than a
    rename: `asr_language` had a successor to carry its value to and this has
    none, so `_load`'s unknown-key drop is the whole of it. What matters is
    that the drop is LOUD and that it costs nothing else in the file -- a
    settings.json written by yesterday's daemon must still boot, with every
    other preference in it intact.
    """
    assert "mic_style" not in BY_KEY, (
        "mic_style is back in the schema, so the page offers five mic "
        "treatments and only one of them can be drawn")

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"mic_style": "arc", "theme": "light"}),
                    encoding="utf-8")
    loaded = Settings(path)

    assert loaded.get("mic_style") is None, "the retired key is still readable"
    assert loaded["theme"] == "light", (
        "a retired key took the rest of the settings file with it")
    assert "mic_style" in capsys.readouterr().out, (
        "the retired value disappeared silently; the owner picked `arc` and "
        "is owed the line saying it was dropped")


def test_the_per_turn_settings_are_not_marked_restart():
    """The other half: anything the pipeline reads per turn must be live, or
    the label is a lie in the opposite direction.

    `harness_model` and `harness_cwd` are read into the DESIRED spec on every
    turn (brain_harness._Host.refresh), and a child whose spec no longer
    matches is disposed and respawned there and then -- so they take effect on
    the next thing the owner says, exactly like the voice.
    """
    for key in ("turn_mode", "patience", "smart_threshold", "rms_on",
                "voice", "end_phrases", "harness_model", "harness_cwd"):
        assert not BY_KEY[key].restart, f"{key} is wrongly marked restart-only"


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_a_bad_value_changes_nothing(tmp_path):
    s = Settings(tmp_path / "s.json")
    before = s.as_dict()
    with pytest.raises(SettingsError):
        s.set("smart_threshold", 5.0)
    assert s.as_dict() == before


def test_a_number_out_of_range_names_the_bound(tmp_path):
    s = Settings(tmp_path / "s.json")
    with pytest.raises(SettingsError) as e:
        s.set("wake_cooldown_ms", 99999)
    assert "at most" in str(e.value)


def test_a_partial_form_does_not_half_apply(tmp_path):
    """The worst outcome: an error appears, the user assumes nothing
    happened, and Butler behaves differently anyway."""
    s = Settings(tmp_path / "s.json")
    with pytest.raises(SettingsError):
        s.update({"turn_mode": "endword", "patience": "glacial"})
    assert s.turn_mode == "patient", "the good field was applied anyway"


def test_booleans_are_not_confused_with_numbers(tmp_path):
    s = Settings(tmp_path / "s.json")
    with pytest.raises(SettingsError):
        s.set("cue_volume", True)


def test_end_phrases_cannot_be_emptied(tmp_path):
    """An empty list in long-form mode means nothing can ever submit."""
    s = Settings(tmp_path / "s.json")
    with pytest.raises(SettingsError):
        s.set("end_phrases", [])


def test_end_phrases_are_normalised(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.set("end_phrases", ["  Send It ", "", "OVER"]) == \
        ["send it", "over"]


# --------------------------------------------------------------------------- #
# persistence and describe()
# --------------------------------------------------------------------------- #
def test_settings_survive_a_restart(tmp_path):
    Settings(tmp_path / "s.json").set("turn_mode", "endword")
    assert Settings(tmp_path / "s.json").turn_mode == "endword"


def test_a_stale_key_in_the_file_is_dropped_loudly(tmp_path, capsys):
    """Keeping it would make the file look like it still does something."""
    (tmp_path / "s.json").write_text('{"turn_mode":"smart","ancient":1}',
                                     encoding="utf-8")
    s = Settings(tmp_path / "s.json")
    assert s.turn_mode == "smart"
    assert "ancient" not in s.as_dict()
    assert "ancient" in capsys.readouterr().out


def test_a_bad_value_in_the_file_falls_back_without_losing_the_rest(
        tmp_path, capsys):
    (tmp_path / "s.json").write_text(
        '{"turn_mode":"endword","smart_threshold":99}', encoding="utf-8")
    s = Settings(tmp_path / "s.json")
    assert s.turn_mode == "endword", "one bad field discarded a good one"
    assert s["smart_threshold"] == DEFAULTS["smart_threshold"]
    assert "smart_threshold" in capsys.readouterr().out


def test_describe_carries_everything_the_page_needs(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set("turn_mode", "endword")
    described = s.describe()
    keys = {d["key"] for d in described["settings"]}
    assert keys == set(DEFAULTS), "describe() and the schema disagree"
    entry = next(d for d in described["settings"] if d["key"] == "turn_mode")
    assert entry["value"] == "endword"
    assert entry["default"] == "patient"
    assert entry["changed"] is True
    assert entry["choices"]


def test_describe_tells_the_page_where_each_group_sits_and_what_it_shows(
        tmp_path):
    """AC-45. The page draws a chain of stages, and cannot work out either half.

    Nothing in `web/` may name a setting (AC-41, web/AGENTS.md), so the page
    cannot decide that Brain is stage 4 or that its headline value is
    `brain_mode`. Both facts cross the wire per group or the boxes render as
    bare labels in whatever order the page felt like.

    `section` joined for AC-700 (docs/lantern-ui.md): the settings sheet's
    navigation is drawn from it.
    """
    groups = Settings(tmp_path / "s.json").describe()["groups"]
    assert groups, "describe() served no groups at all"
    for g in groups:
        assert set(g) == {"key", "label", "stage", "headline", "blurb",
                          "quick", "collection", "pairing", "inactive",
                          "section"}, (
            f"a group entry carries {sorted(g)}; the page needs exactly key, "
            f"label, stage, headline, blurb, quick, collection, pairing, "
            f"inactive and section")
        assert isinstance(g["quick"], bool), (
            f"{g['key']}'s quick flag is {g['quick']!r}; the page renders a "
            f"whole group on the main page from it and a truthy string would "
            f"put every setting Butler has on the header pill")
        assert isinstance(g["inactive"], bool), (
            f"{g['key']}'s inactive flag is {g['inactive']!r}; the page dims "
            f"a box on it and a truthy string would dim every box")
        assert isinstance(g["key"], str) and g["key"] in GROUPS
        assert isinstance(g["label"], str) and g["label"]
        assert g["stage"] is None or isinstance(g["stage"], int), (
            f"{g['key']} has a non-integer stage {g['stage']!r}")
        assert g["headline"] is None or g["headline"] in DEFAULTS, (
            f"{g['key']} headlines {g['headline']!r}, which is not a setting")
    staged = [g["stage"] for g in groups if g["stage"] is not None]
    assert staged == list(range(1, len(staged) + 1)), (
        f"the served stages are not a chain from 1: {staged}")


def test_every_group_is_served_a_sentence_saying_what_it_does(tmp_path):
    """docs/settings-page.md 5, AC-87. The page prints it and may not write it.

    Nothing in `web/` may name a setting or a seam (AC-41), so a section
    heading that explains itself has to be explained by the daemon. The mock
    invented the field for exactly this and the page will print whatever is
    served, so the failure this catches is a heading with nothing under it --
    or, worse, a page author filling the gap in and tripping the grep gate.

    A blurb is OPTIONAL by contract: the default is "" and `_validated_groups`
    does not require one, because a section with no sentence prints none,
    exactly as a None headline shows no value. What is asserted is that the
    KEY always crosses the wire, and that every group Butler actually ships
    has a real sentence in it.
    """
    from daemon.settings import Group, _validated_groups

    for g in Settings(tmp_path / "s.json").describe()["groups"]:
        assert isinstance(g["blurb"], str), (
            f"{g['key']}'s blurb is {g['blurb']!r}; the page prints it as "
            f"text and a null would render as the word None")
        assert g["blurb"].strip(), (
            f"{g['key']} is served no sentence; every group in GROUPS has "
            f"one, and a heading with a blank under it reads as unfinished")
        assert g["blurb"].strip().lower() != g["label"].strip().lower(), (
            f"{g['key']}'s blurb only repeats its label")

    assert Group("Nameless", 1, None).blurb == "", (
        "a group must be constructible without a blurb")
    blurbless = {"brain": Group("Brain", 1, "brain_mode", section="voice")}
    assert _validated_groups(blurbless) is blurbless, (
        "_validated_groups must not start requiring a blurb")


def test_a_headline_naming_a_missing_setting_is_refused_at_import(tmp_path):
    """Fail fast, where the groups are DEFINED, not where the box is drawn.

    The alternative is the AC-7b failure in miniature: the daemon serves a
    group whose headline names nothing, the page renders a labelled box with
    an empty value, and the owner reads that as "not set" rather than "this
    setting was renamed and one box has been lying since".
    """
    from daemon.settings import Group, _validated_groups

    ok = {"brain": Group("Brain", 1, "brain_mode", section="voice")}
    assert _validated_groups(ok) is ok

    with pytest.raises(ValueError) as e:
        _validated_groups({"brain": Group("Brain", 1, "brian_mode",
                                          section="voice")})
    assert "brian_mode" in str(e.value), str(e.value)

    with pytest.raises(ValueError):
        _validated_groups({"a": Group("A", 1, None, section="voice"),
                           "b": Group("B", 3, None, section="voice")})
    with pytest.raises(ValueError):
        _validated_groups({"a": Group("A", 2, None, section="voice"),
                           "b": Group("B", 1, None, section="voice")})


def test_reset_returns_a_group_to_defaults(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set("turn_mode", "endword")
    s.set("rms_on", 0.05)
    s.reset("turn")
    assert s.turn_mode == "patient"
    assert s["rms_on"] == 0.05, "reset spilled into another group"


def test_reset_of_an_unknown_group_is_refused(tmp_path):
    with pytest.raises(SettingsError):
        Settings(tmp_path / "s.json").reset("nonsense")


# --------------------------------------------------------------------------- #
# over HTTP
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# the settings reach the engines (D-18: assert on the CALLER)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_voice_setting_reaches_the_speech_engine(tmp_path):
    """AC-7b's hard half: a live setting must actually change what happens.

    Everything else about `voice` can be green while it does nothing. The
    schema marks it `restart: False`, `describe()` returns the new value, the
    broadcast fires, and `speak_start` carries it on the wire -- and the room
    still hears the old voice, because the value stops at whichever layer
    drops it. This asserts the last hop, on the CALLER: the reply path reads
    the setting per reply and hands it to the Tts port (D-18).
    """
    prefs = Settings(tmp_path / "s.json")
    prefs.set("voice", "bm_george")
    hub = make_hub(tmp_path, settings=prefs)
    s = attach_session(hub)
    s.turn = 20
    s.state = "thinking"

    helper = asyncio.create_task(confirm_playback_when_asked(hub, s))
    await asyncio.wait_for(hub._reply(s, 20, "what time is it"), timeout=10)
    helper.cancel()

    assert hub.tts.voices == ["bm_george"], (
        f"the reply was synthesized with {hub.tts.voices}, not the voice the "
        f"settings ask for")


@pytest.mark.asyncio
async def test_the_engine_is_asked_with_the_RESOLVED_voice(tmp_path):
    """AC-212, and a DELIBERATE CORRECTION of what this file used to assert.

    Until 2026-09-11 the claim above was the whole claim: the engine is asked
    with exactly the raw `voice` setting. That was satisfied by a live defect.
    `_speak` passed `self._voice()` straight through while `_begin_speaking`
    announced `_announced_voice()`, which resolved a BLANK voice inside the
    chosen language -- so with `voice = ""` and a Portuguese language the wire
    said a Portuguese voice and the engine was handed `""` and used its own
    English default. AC-7b's exact shape, inside the method whose docstring
    said it existed to prevent it, and `test_seams.py` missed it because it
    checked the announced name against the UNFILTERED voice list, where an
    English default is a perfectly legal answer.

    So this is the STRONGER claim rather than a weakened one: the engine is
    asked with the SAME string `speak_start` announced, whatever that string
    was resolved from. The raw-setting version is still asserted above,
    because a named voice must still win.
    """
    prefs = Settings(tmp_path / "s.json")
    prefs.set("language", "pt-BR")           # and `voice` stays blank
    hub = make_hub(tmp_path, settings=prefs)
    s = attach_session(hub)
    s.turn = 30
    s.state = "thinking"

    helper = asyncio.create_task(confirm_playback_when_asked(hub, s))
    await asyncio.wait_for(hub._reply(s, 30, "what time is it"), timeout=10)
    helper.cancel()

    starts = [json.loads(m) for m in s.ws.sent
              if isinstance(m, str) and json.loads(m)["type"] == "speak_start"]
    assert starts, f"no speak_start was sent: {s.ws.text_types()}"
    announced = starts[0]["voice"]
    assert hub.tts.voices and set(hub.tts.voices) == {announced}, (
        f"speak_start announced {announced!r} and the engine was asked with "
        f"{hub.tts.voices!r} -- the wire and the room disagree (AC-7b)")
    assert "" not in hub.tts.voices, (
        "the engine was handed a blank voice, so it used its own default "
        "while the wire named a voice for the chosen language")
    assert (hub._voice_language(announced) or "").lower() == "pt-br", (
        f"{announced!r} does not speak the language that was chosen")


@pytest.mark.asyncio
async def test_the_language_setting_reaches_the_recognizer(tmp_path):
    """AC-152, and D-18 on the half that had no caller-side assertion at all.

    This is the test whose absence let `asr_language` ship inert. Nothing
    outside `settings.py` read that key: the schema was right, `describe()`
    returned the value, the page rendered a control, the restart flag said a
    restart would apply it -- and `build_hub` built the recognizer from
    `config/models.json` and never asked the store. Every one of those layers
    was green, which is exactly the D-18 shape.

    So this asserts the LAST hop and only the last hop: the turn path reads
    the setting and hands it to the Asr port. `FakeAsr` stamps whatever
    `AsrConfig` it was opened with onto its `Final`, so the value observed
    here came back through the port rather than being read from the store a
    second time -- a test that re-read the setting would pass with the wire
    still cut.
    """
    prefs = Settings(tmp_path / "s.json")
    prefs.set("language", "pt-BR")
    hub = make_hub(tmp_path, settings=prefs)

    assert hub._asr_config().language == "pt-BR", (
        "the language setting does not reach the AsrConfig the turn path "
        "opens the recognizer with")

    seen = []
    hub.asr.open(hub._asr_config(), lambda _p: None, seen.append).finalize()
    assert seen and seen[0].lang == "pt-BR", (
        f"the recognizer was opened without the owner's language: {seen!r}")


def test_the_recognition_settings_reach_the_recognizer(tmp_path, monkeypatch):
    """D-36 closed, and the test whose absence let it ship.

    `asr_model`, `asr_backend` and `asr_latency` were inert for their whole
    existence: `build_hub` read the adapter's options out of
    `config/models.json` and built the settings store four lines later, so
    the store could never have been consulted. Every layer was green --
    schema, describe(), the broadcast, the restart badge -- which is D-18
    exactly, and `test_the_engine_settings_are_marked_restart` asserted the
    FLAG rather than a reader.

    Asserted on the CALLER: the adapter records the options it was
    constructed with, so what is checked here came through `ports.build`
    rather than being read back out of the store.
    """
    from daemon import butlerd

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("BUTLER_STATE", str(state))
    prefs = Settings(state / "settings.json")
    prefs.update({"asr_backend": "cpu", "asr_latency": "low"})

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake"},
                             "asr": {"model": "parakeet-unified-en-0.6b"}})
    assert hub.asr.engine_options["backend"] == "cpu", (
        f"asr_backend never reached the recognizer: "
        f"{hub.asr.engine_options!r}")
    assert hub.asr.engine_options["latency"] == "low", (
        f"asr_latency never reached the recognizer: "
        f"{hub.asr.engine_options!r}")


def test_a_setting_beats_a_stale_config_key_without_refusing_to_boot(
        tmp_path, monkeypatch, capsys):
    """Two owners for one value is D-35 arriving through the back door --
    and the cure must not be worse than the disease.

    This raised SystemExit for about an hour on 2026-09-10 and took the
    owner's live daemon down: their `config/models.json` has carried
    `asr_engine: {"backend": "cpu"}` since long before these settings did
    anything, so a refusal aimed at a stale key fired on a config that had
    always been correct. There was then no microphone AND no page to fix it
    from, which is the one outcome `build_hub` must never produce (D-1).

    So: the setting wins, the dead key is NAMED so it can be deleted, and
    the daemon boots. Both halves asserted, because a warning nobody can act
    on and an override nobody is told about are each half a fix.
    """
    from daemon import butlerd

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("BUTLER_STATE", str(state))
    Settings(state / "settings.json").set("asr_backend", "vulkan")

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake"},
                             "asr_engine": {"backend": "cpu"}})
    assert hub.asr.engine_options["backend"] == "vulkan", (
        f"the stale config key beat the setting: {hub.asr.engine_options!r}")
    said = capsys.readouterr().out
    assert "asr_engine.backend" in said and "asr_backend" in said, (
        f"the owner is not told which key to delete: {said!r}")


def test_the_setting_wins_even_at_its_default(tmp_path, monkeypatch):
    """And that is a real consequence, pinned rather than discovered.

    These three settings all have a NON-BLANK default, so "never chose" and
    "chose the default" are indistinguishable. An owner whose config file
    says `backend: cpu` and who has never opened the Recognition page will
    therefore get `auto` on the next restart. That follows from the ruling
    and is the honest reading of "the schema IS the contract" -- but it is a
    behaviour change on somebody else's machine, so it is asserted here
    rather than left to be found.
    """
    from daemon import butlerd

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("BUTLER_STATE", str(state))
    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake"},
                             "asr_engine": {"backend": "cpu"}})
    assert hub.asr.engine_options["backend"] == "auto", (
        f"the schema default did not win over the stale config key: "
        f"{hub.asr.engine_options!r}")


def test_a_blank_language_asks_the_engine_for_nothing(tmp_path):
    """Blank means "the model's own default" and must not become a locale.

    The failure this pins is the one just removed from `stt_engine`: a blank
    language quietly becoming en-US, so a multilingual model decoded English
    and the panel said nothing about it. `None` is the only honest way to
    say "not chosen" across the port -- an empty string is a value.
    """
    prefs = Settings(tmp_path / "s.json")
    hub = make_hub(tmp_path, settings=prefs)
    assert hub._asr_config().language is None, (
        "a blank language setting must reach the port as None, not as '' or "
        "a guessed locale")


def test_the_language_setting_replaced_asr_language(tmp_path):
    """AC-152 and D4: ONE key names the language, and the old value carries.

    Two failures in one, because they are the same defect from both sides: a
    schema that still carries `asr_language` gives the decision two owners
    (D-35), and a rename that drops the stored value silently loses a choice
    the owner made.
    """
    assert "asr_language" not in BY_KEY, (
        "asr_language is back in the schema; the language decision has two "
        "owners again (AC-152)")

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"asr_language": "pt-BR"}), encoding="utf-8")
    assert Settings(path).get("language") == "pt-BR", (
        "a stored asr_language was not carried over to language")

    # And a value no engine accepts is DROPPED rather than carried into a
    # recognizer that would refuse every turn. The owner's real stored value
    # was "PT", which is not a locale any model here speaks.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"asr_language": "PT"}), encoding="utf-8")
    assert Settings(bad).get("language") == "", (
        "an unusable stored language was carried over instead of dropped")


@pytest.mark.asyncio
async def test_the_api_serves_the_schema(daemon):
    async with aiohttp.ClientSession() as http:
        async with http.get(f"{daemon}/api/settings") as r:
            assert r.status == 200
            body = await r.json()
    assert body["groups"] and body["settings"]
    assert {d["key"] for d in body["settings"]} == set(DEFAULTS)


@pytest.mark.asyncio
async def test_a_rejected_write_returns_400_and_names_the_options(daemon):
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{daemon}/api/settings",
                             json={"turn_mode": "whenever"}) as r:
            assert r.status == 400
            body = await r.json()
    assert "endword" in body["error"], body


@pytest.mark.asyncio
async def test_raising_the_permission_mode_works_from_the_ui(daemon):
    """AC-10's UI half, which had no client path at all before this.

    Escalation is refused by voice for everyone and refused over the
    WebSocket; without this endpoint the mode could only ever go down.
    """
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{daemon}/api/mode", json={"mode": "trusted"}) as r:
            assert r.status == 200, await r.text()
            assert (await r.json())["mode"] == "trusted"
        async with http.post(f"{daemon}/api/mode",
                             json={"mode": "paranoid"}) as r:
            assert (await r.json())["mode"] == "paranoid"
        async with http.post(f"{daemon}/api/mode", json={"mode": "sideways"}) as r:
            assert r.status == 400
        # Leave the daemon as we found it for the rest of the session.
        await http.post(f"{daemon}/api/mode", json={"mode": "secretary"})


@pytest.mark.asyncio
async def test_a_settings_change_broadcasts_the_live_capture_ceiling(
        daemon, satellite):
    """AC-92's other half, on the THIRD frame that already echoes
    `turn_mode` on every settings change (`_announce_settings`), not a
    fourth mechanism. A settings-page toggle of `turn_mode` must update the
    page's own watchdog exactly as a spoken change does (test_turn_modes.py)."""
    from daemon.endpointing import END_WORD_MAX_MS
    async with aiohttp.ClientSession() as http:
        try:
            async with http.post(f"{daemon}/api/settings",
                                 json={"turn_mode": "endword"}) as r:
                assert r.status == 200, await r.text()
            frame = await satellite.next("settings", timeout=10)
            assert frame.get("capture_ceiling_ms") == END_WORD_MAX_MS, (
                f"the settings broadcast did not carry the live ceiling: "
                f"{frame}")
        finally:
            # Leave the daemon as we found it for the rest of the session.
            await http.post(f"{daemon}/api/settings",
                            json={"turn_mode": "patient"})


# --------------------------------------------------------------------------- #
# Model settings are {conn, model} pairs (AC-30, plan.md 4.4b)
# --------------------------------------------------------------------------- #
def test_a_model_setting_is_a_connection_and_a_model(tmp_path):
    """A bare model id is ambiguous the moment two connections serve one name.

    It is also unusable for any provider needing a key, which is every cloud
    one -- the connection is what carries the base URL and the auth pointer.
    """
    from daemon.settings import Settings, SettingsError

    prefs = Settings(tmp_path / "settings.json")
    cleaned = prefs.set("router_model",
                        {"conn": "llama-swap", "model": "qwen3-4b"})
    assert cleaned == {"conn": "llama-swap", "model": "qwen3-4b"}

    for bad in ("qwen3-4b", {"model": "x"}, {"conn": 1, "model": "x"}, None):
        with pytest.raises(SettingsError):
            prefs.set("router_model", bad)


def test_a_legacy_string_model_setting_is_migrated_not_lost(tmp_path):
    """The owner's `state/settings.json` predates the pair.

    Refusing to load it would mean the daemon will not start after an update,
    and silently dropping the value would lose a choice they made. The model
    survives with no connection, which is the honest reading: they picked a
    model, and nothing ever asked them which connection it was on.
    """
    from daemon.settings import Settings

    path = tmp_path / "settings.json"
    path.write_text('{"router_model": "bartowski/Ornith-1.0-9B-Q5_K_L"}',
                    encoding="utf-8")

    prefs = Settings(path)

    assert prefs.get("router_model") == {
        "conn": "", "model": "bartowski/Ornith-1.0-9B-Q5_K_L"}, (
        f"a legacy value was lost or crashed the load: "
        f"{prefs.get('router_model')!r}")


def test_a_blank_pair_is_legal_and_means_nothing_chosen(tmp_path):
    """Butler ships no default model (4.4b), so the shipped value must be a
    legal one rather than something the schema would reject."""
    from daemon.settings import BY_KEY, Settings

    prefs = Settings(tmp_path / "settings.json")
    assert prefs.get("router_model") == BY_KEY["router_model"].default
    prefs.set("router_model", {"conn": "", "model": ""})


def test_a_settings_file_that_is_not_an_object_is_not_fatal(tmp_path):
    """Valid JSON, wrong shape. `null`, `[]`, `42` all parse.

    These reached `raw.items()` and raised AttributeError out of build_hub,
    so the daemon would not BOOT -- the one outcome this module rules out. A
    preference cannot make Butler unsafe, and refusing to start leaves the
    owner with no microphone at all (D-1).
    """
    from daemon.settings import BY_KEY, Settings

    for shape in ("null", "[]", "42", '"oops"'):
        path = tmp_path / f"s{abs(hash(shape))}.json"
        path.write_text(shape, encoding="utf-8")
        prefs = Settings(path)          # must not raise
        assert prefs.get("turn_mode") == BY_KEY["turn_mode"].default, (
            f"{shape} did not fall back to defaults")


# --------------------------------------------------------------------------- #
# the speech seam's two new preferences (AC-107, AC-110)
# --------------------------------------------------------------------------- #
def test_speaking_speed_is_bounded_where_the_word_caps_still_hold(tmp_path):
    """AC-107, and the reason the range is narrow rather than generous.

    `speech.py` caps a synthesis segment in WORDS, and those caps exist to
    bound MILLISECONDS: time to first audio (AC-4) and how long an
    interrupted segment keeps playing (AC-5). Speed is the constant in the
    words-to-seconds conversion, so a wide range silently stretches two time
    budgets nothing restates. docs/voice-control.md 6 offers three ways out
    and this is option 3 -- bound the range so the drift cannot matter. The
    numbers are asserted here rather than only in the schema, because
    widening them later is exactly the change that must not be quiet.
    """
    spec = BY_KEY["tts_speed"]
    assert spec.kind == "number" and spec.group == "tts"
    assert spec.default == 1.0, (
        f"the shipped default must be the engine's own rate, got "
        f"{spec.default!r}")
    assert (spec.minimum, spec.maximum) == (0.8, 1.3), (
        f"the range moved to {(spec.minimum, spec.maximum)}; at 0.8 an "
        f"eight-word opener takes 25% longer to speak than the caps in "
        f"speech.py assume (AC-4, AC-5)")
    assert not spec.restart, (
        "tts_speed is read per turn like `voice`, so flagging it for restart "
        "would tell the owner a lie about their own setting")

    prefs = Settings(tmp_path / "settings.json")
    for good in (0.8, 1.0, 1.3):
        assert prefs.set("tts_speed", good) == good
    for bad in (0.5, 1.5, 2.0, "fast", True):
        with pytest.raises(SettingsError):
            prefs.set("tts_speed", bad)
    assert prefs.get("tts_speed") == 1.3, (
        "a refused speed changed the stored value; settings fail loud and "
        "the old value survives")


def test_muting_speech_is_a_separate_setting_from_the_cues(tmp_path):
    """AC-110. `cues` is the earcons and `speak` is the answer.

    They were one thing in the owner's request and are two in the schema,
    because turning the tones off to stop a bedside phone chirping must not
    also lose the answer, and going quiet must not cost the heard-you tone
    that says the microphone is alive. Both live in the `tts` group, so the
    only thing keeping them apart is that they are separate keys -- which is
    what this asserts, since the daemon has no cue path of its own to drive.
    """
    spec = BY_KEY["speak"]
    assert spec.kind == "bool" and spec.group == "tts"
    assert spec.default is True, (
        "Butler ships speaking; a satellite that arrives silent is "
        "indistinguishable from a broken one (AC-7b)")
    assert not spec.restart, (
        "speak is read per turn, so a restart flag would be a lie")

    prefs = Settings(tmp_path / "settings.json")
    prefs.set("speak", False)
    assert prefs.get("cues") is BY_KEY["cues"].default, (
        "muting the spoken reply changed the audible cues")
    prefs.set("cues", False)
    assert prefs.get("speak") is False, (
        "the two settings are not independent")

    with pytest.raises(SettingsError):
        prefs.set("speak", "no")

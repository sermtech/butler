"""Engine vocabulary stays inside its adapter. AC-41, plan.md 4.10.

A grep gate, in the style of AC-8's localStorage ban, and for the same
reason: the rule is easy to state, easy to believe you are following, and
invisible when you are not. `daemon/ports.py` opens by saying "Nothing here
imports an engine... if it ever does, the seam has leaked" -- but naming an
engine's vocabulary leaks the seam just as effectively as importing it, and
nothing checked.

What this catches, concretely. Swap Kokoro for Piper and the new adapter can
pass the whole Tts conformance suite while Butler still offers the owner
eleven voices Piper has never heard of, because the list lived in the
settings schema. The engine that knows its own voices is the only thing that
should be naming them.

The bottom of the file holds two gates of the same SHAPE aimed at the other
end of the same seam, where the schema meets the page (AC-173, AC-680): the
faces the daemon offers must be families `web/fonts.css` actually ships, and
only a style (`[data-style]`) may declare a state colour (AC-680). Both read
the shipped artefact rather than a copy of it, and both guard the guard -- a
gate that cannot fail is the thing D-13 is about.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

from conftest import attach_session, make_hub

from daemon import ports
from daemon.settings import BY_KEY, Settings

REPO = Path(__file__).resolve().parents[1]
DAEMON = REPO / "daemon"

# The files that make up Butler's core: policy, protocol, the port contract
# and the settings schema. Adapters are exempt by definition -- naming your
# own engine's vocabulary is their job.
CORE = [
    DAEMON / "ports.py",
    DAEMON / "hub.py",
    DAEMON / "settings.py",
    DAEMON / "pipeline.py",
    DAEMON / "butlerd.py",
]


def _voice_names() -> tuple[str, ...]:
    """Read from the ENGINE's own voice packs, never restated here.

    A hardcoded copy in this file would be one more place the list lives,
    which is the defect the test exists to prevent.

    This used to import `VOICES` from the adapter, and AC-108 deleted that
    tuple: it declared eleven voices against a pool that reported 28, so a
    list living in the daemon was wrong by a factor of two and a half and
    wrong invisibly. There is now no list in Python to read, which is the
    point -- so this reads the filenames kokoro-js ships, which is the
    engine's own artefact rather than anybody's copy of it, and is a WIDER
    net than the old tuple: it catches a core file naming any voice the
    engine has, not just one of the eleven somebody happened to write down.
    """
    packs = REPO / "node_modules" / "kokoro-js" / "voices"
    names = tuple(sorted(p.stem for p in packs.glob("*.bin"))) \
        if packs.is_dir() else ()
    # Guard the guard. This gate asserts that NONE of these appear in the
    # core, so an empty tuple would make it pass no matter what the core
    # says -- a gate that cannot fail, which is the thing D-13 is about.
    # `node_modules` is gitignored, so a fresh clone that has not run npm
    # install SKIPS rather than passing vacuously.
    if not names:
        pytest.skip("kokoro-js voice packs are not installed, so this gate "
                    "would be vacuous (node_modules is gitignored)")
    return names


@pytest.mark.parametrize("path", CORE, ids=lambda p: p.name)
def test_no_engine_voice_name_in_the_daemon_core(path):
    """AC-41. Kokoro's voices belong to Kokoro.

    `hub.py` carried "af_heart" twice as a default, and `settings.py` listed
    all eleven as the `voice` setting's choices -- so the browser inherited
    Kokoro's vocabulary from the schema, and swapping the engine would have
    left the picker offering voices nothing could speak.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    found = sorted({v for v in _voice_names() if v in text})
    assert found == [], (
        f"{path.name} names engine-specific voices {found}. The voice list "
        f"belongs to the adapter and reaches the page through "
        f"Tts.info['voices'] (AC-41, AC-42).")


@pytest.mark.asyncio
async def test_speak_start_names_a_voice_the_engine_actually_used(tmp_path):
    """AC-41 and AC-7b: what the wire claims and what the room hears agree.

    `voice=""` is legal at the PORT -- it means "the adapter's own default",
    the same way a blank router_model means the connection's default. It is
    NOT legal on the wire. The satellite displays this string, so an empty
    one renders as a blank label and the page cannot tell "defaulted" from
    "broken".

    This is AC-7b's exact shape and it has shipped here once: the sidecar
    bound a voice at boot, so the settings page reported success, the wire
    said bm_george, and the room heard af_heart. The rule that came out of it
    is that the daemon resolves the default BEFORE announcing it, rather than
    announcing a placeholder and hoping.
    """
    hub = make_hub(tmp_path)
    s = attach_session(hub)
    s.turn = 21
    s.state = "thinking"

    async def confirm_playback():
        while True:
            await asyncio.sleep(0.02)
            if s.state == "speaking" and not s.playback_done.is_set():
                await hub._playback_done(s, {"turn": 21})

    helper = asyncio.create_task(confirm_playback())
    try:
        await asyncio.wait_for(hub._reply(s, 21, "what time is it"), timeout=15)
    finally:
        helper.cancel()

    starts = [json.loads(m) for m in s.ws.sent
              if isinstance(m, str) and json.loads(m)["type"] == "speak_start"]
    assert starts, f"no speak_start was sent: {s.ws.text_types()}"
    spoken = hub.tts.voices                     # what the ENGINE was asked for
    for start in starts:
        assert start.get("voice"), (
            f"speak_start carried no voice: {start}. The page renders this, "
            f"so an empty string is a blank the owner cannot interpret.")
        speakable = ports.voice_ids(hub.tts.info)
        assert start["voice"] in speakable, (
            f"speak_start announced {start['voice']!r}, which is not a voice "
            f"this engine reports it can speak: {speakable}")
    assert spoken, "the engine was never asked to speak anything"


def test_the_picker_offers_the_voices_for_the_chosen_language(tmp_path):
    """AC-154 and AC-156. Choosing a language reshapes the voice picker.

    This gate is the reason both fakes speak two languages. A review on
    2026-09-10 mutated `voice_ids` to ignore its `language` argument and the
    WHOLE suite stayed green -- 676 passed, exit 0 -- because every caller
    passed one argument and the parameter was dead. That is D-13 exactly: a
    filter nothing exercised, and two-language fakes that proved nothing.

    Both directions, because a filter that returns everything and a filter
    that returns nothing are both wrong and only one of them looks wrong.
    """
    from daemon import butlerd

    hub = make_hub(tmp_path, settings=Settings(tmp_path / "s.json"))
    every = ports.voice_ids(hub.tts.info)
    assert len({v["language"] for v in hub.tts.info["voices"]}) >= 2, (
        f"this gate cannot fail while the engine speaks one language: "
        f"{hub.tts.info['voices']!r}")

    hub.settings.set("language", "pt-BR")
    offered = butlerd.LOCAL_TARGETS["tts_voices"](hub)
    assert offered and set(offered) < set(every), (
        f"choosing a language did not narrow the picker: {offered!r} out of "
        f"{every!r}")
    for name in offered:
        entry = next(v for v in hub.tts.info["voices"] if v["id"] == name)
        assert entry["language"].lower() == "pt-br", (
            f"{name!r} speaks {entry['language']!r} and was offered for "
            f"pt-BR")

    # AC-156: a blank voice must resolve INSIDE that language, or a
    # Portuguese conversation is answered in an English voice by default --
    # the failure this feature exists to remove, through the one control the
    # owner never touched.
    resolved = hub._voice_for(hub._default_language())
    assert resolved in offered, (
        f"a blank voice resolved to {resolved!r}, which is not "
        f"one of the {offered!r} that speak the chosen language")
    assert (hub._voice_language(resolved) or "").lower() == "pt-br", (
        f"speak_start would announce lang={hub._voice_language(resolved)!r}")


def test_a_language_no_voice_speaks_still_offers_a_picker(tmp_path):
    """AC-108 survives AC-154: filtering must never empty this list.

    An empty voice list RAISES one layer out, where empty means "the engine
    could not be asked". A language with no voice installed is a different
    fact and must not borrow that failure -- the owner would see a picker
    with nothing in it and conclude the speech engine was broken.
    """
    from daemon import butlerd

    hub = make_hub(tmp_path, settings=Settings(tmp_path / "s.json"))
    hub.settings.set("language", "ja-JP")          # no fake voice speaks it
    offered = butlerd.LOCAL_TARGETS["tts_voices"](hub)
    assert offered == ports.voice_ids(hub.tts.info), (
        f"a language nothing speaks emptied the picker instead of falling "
        f"back to every voice: {offered!r}")


def test_a_language_the_recognizer_cannot_speak_is_not_sent_to_it(tmp_path):
    """The 15-second dead turn, closed. Found by review, 2026-09-10.

    MEASURED before this guard: with `pt-BR` set and the English-only
    parakeet loaded, `open()` SUCCEEDED, the engine emitted one error per
    audio frame -- about 150 lines on a 5 s turn -- and `finalize` then waited
    out its full 15 s floor before ending with "I could not finish
    transcribing that one". Every turn, forever, blaming the decoder for a
    settings mismatch.

    It is reachable because the schema offers every locale the CATALOGUE's
    models can do while the LOADED model speaks whatever it speaks, and
    `asr_model` is still inert (D-36) so the owner cannot switch to the model
    that would make their choice work.
    """
    hub = make_hub(tmp_path, settings=Settings(tmp_path / "s.json"))
    hub.settings.set("language", "pt-BR")

    class OneLanguage:
        info = {"languages": ["en"]}

    hub.asr = OneLanguage()
    assert hub._asr_config().language is None, (
        "a language the loaded recognizer does not speak was handed to it "
        "anyway; that is a 15 s dead turn on every utterance")

    hub.asr = type("Speaks", (), {"info": {"languages": ["en", "pt-BR"]}})()
    assert hub._asr_config().language == "pt-BR", (
        "the guard is refusing a language the recognizer DOES speak, which "
        "is the same defect pointing the other way")

    # An engine that will not say what it speaks does not get a veto.
    hub.asr = type("Silent", (), {"info": {}})()
    assert hub._asr_config().language == "pt-BR", (
        "an adapter reporting no languages blocked the owner's choice")


def test_every_voice_the_port_reports_says_what_language_it_speaks():
    """AC-155. The ENGINE names the language; the daemon never infers one.

    This is the gate that makes D3 real rather than a convention. Without it
    the shape is whatever the last adapter author felt like, and the first
    caller to want a language does the cheap thing instead -- reads the
    `af_`/`pf_` prefix -- which puts one engine's naming convention in the
    core and is what the AC-41 gate above forbids from the other direction.

    Checked on every REGISTERED adapter rather than on one, because the
    lesson `Tts.info` already taught here is that a port change breaks the
    implementations nobody remembered: adding it broke four in two files.
    Adapters that need a running engine report an empty list before `warm()`,
    and an empty list passes vacuously -- so the fake, which needs nothing,
    is asserted to be non-empty to keep this from becoming a gate that cannot
    fail (D-13).
    """
    for name in sorted(ports.TTSS):
        entries = ports.build(ports.TTSS, name).info.get("voices") or []
        for v in entries:
            assert isinstance(v, dict), (
                f"{name} reports a voice as {type(v).__name__}, not a "
                f"{{id, language}} entry: {v!r} (AC-155)")
            assert v.get("id"), f"{name} reports a voice with no id: {v!r}"
            assert "language" in v, (
                f"{name} reports voice {v['id']!r} without saying what "
                f"language it speaks (AC-155)")

    fake = ports.build(ports.TTSS, "fake").info["voices"]
    assert len(fake) >= 2 and len({v["language"] for v in fake}) >= 2, (
        f"the fake must offer voices in at least two languages or every "
        f"test of language filtering passes without a filter: {fake!r}")


# --------------------------------------------------------------------------- #
# a language with no region is a PRIMARY SUBTAG match (AC-208)
# --------------------------------------------------------------------------- #
# Three vocabularies name one language and none of them agrees with the
# others: the detector answers ISO-639-1 `pt`, Kokoro reports `pt-br`, the
# recognizer catalogue says `pt-BR`. Reconciling them is a RULE at this seam
# and never a table in the core -- `voice_ids`' own docstring made that
# promise for the case-folding half, and this is the same promise kept for
# the region half.
_SUBTAG_INFO = {"voices": [{"id": "en_a", "language": "en-US"},
                           {"id": "en_b", "language": "en-GB"},
                           {"id": "pt_a", "language": "pt-BR"},
                           {"id": "pt_b", "language": "pt-PT"},
                           {"id": "bare", "language": "de"}]}


@pytest.mark.parametrize("want,expect", [
    # A bare subtag widens: this is the case that returned NOTHING before
    # AC-208, so automatic language detection had no voice to choose and
    # every turn fell through to the engine's English default.
    ("pt", ["pt_a", "pt_b"]),
    ("en", ["en_a", "en_b"]),
    # A REGION still means exactly that region. A detector never produces one
    # of these; an owner picking a voice by hand does, and widening their
    # choice would be this seam overruling them.
    ("pt-BR", ["pt_a"]),
    ("en-GB", ["en_b"]),
    # Case is the half that already worked, asserted here so a rewrite of the
    # matching cannot quietly drop it.
    ("PT-br", ["pt_a"]),
    ("PT", ["pt_a", "pt_b"]),
    # An entry with no region is matched by its own bare subtag and NOT by a
    # regioned want -- `de` is not evidence that a `de-AT` voice exists.
    ("de", ["bare"]),
    ("de-AT", []),
    # Blank is "no filter", which is what makes this safe to call before
    # anything has a language.
    ("", ["en_a", "en_b", "pt_a", "pt_b", "bare"]),
])
def test_a_language_with_no_region_matches_the_primary_subtag(want, expect):
    """AC-208. The rule, both directions, because only one of them is new."""
    assert ports.voice_ids(_SUBTAG_INFO, want) == expect


def test_the_detector_is_offered_only_languages_a_voice_speaks():
    """AC-207. The candidate set is the ENGINE's, reduced to subtags.

    Two claims, and the second is the one with teeth. Regions COLLAPSE, so a
    `pt-BR` and a `pt-PT` voice are one candidate rather than two the
    classifier has to choose between -- it names a language and has no
    opinion about a region. And nothing without a voice is in the list: a
    detector free to answer a language Butler cannot speak would be right
    while the room heard the engine's default.
    """
    assert ports.voice_languages(_SUBTAG_INFO) == ["de", "en", "pt"]
    assert ports.voice_languages({}) == []
    assert ports.voice_languages({"voices": [{"id": "x", "language": ""}]}) == []

    fake = ports.build(ports.TTSS, "fake").info
    assert len(ports.voice_languages(fake)) >= 2, (
        f"the fake speaks one language, so nothing that depends on a "
        f"candidate set can fail: {fake['voices']!r}")


# One engine's deployment vocabulary. Not a general ban on the word: the
# ADAPTER must say "vulkan", and `settings.py` legitimately offers the owner a
# backend choice. This is about the shared CONTRACT file, whose own docstring
# says "Nothing here imports an engine... if it ever does, the seam has
# leaked" -- and which then explained transcribe.cpp's chunking presets.
# "gguf" joined on review: a local model-file format, so a cloud recognizer
# has none, and the contract file was explaining where capabilities come from
# in terms only one family of engines has.
ASR_ENGINE_VOCABULARY = ("vulkan", "(70,7,7)", "gguf")


def test_the_contract_file_explains_no_engine_internals():
    """AC-41, ASR half. `ports.py` describes seams, not deployments.

    `AsrConfig` carried `backend: "auto | vulkan | cpu"` and
    `latency: "balanced"  # the spike's (70,7,7) 560 ms chunking`. Vulkan is a
    transcribe.cpp deployment fact and the presets are parakeet's; Deepgram
    has no backend axis and no chunking preset, so a cloud recognizer would
    have arrived to find two fields it must accept and ignore.

    Deliberately NOT banned here: "Kokoro", which appears beside
    SAMPLE_RATE_OUT. That comment records WHY the port fixes 24 kHz, and the
    number genuinely came from that engine. A port stating its wire format and
    citing its provenance is the contract doing its job; a port carrying a
    field only one engine can use is the leak.
    """
    text = (DAEMON / "ports.py").read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    found = sorted({v for v in ASR_ENGINE_VOCABULARY if v.lower() in lowered})
    assert found == [], (
        f"ports.py explains one engine's internals: {found}. Engine-specific "
        f"configuration is a constructor kwarg on the adapter, named in "
        f"config/models.json, not a field every recognizer must accept "
        f"(AC-41).")


# The pipeline, in the order audio actually travels it (plan.md 4.2), which is
# the order the settings page presents. Encoded here rather than only in
# settings.py so that a reshuffle has to be deliberate: the sequence IS the
# organising idea, and a dict literal is easy to reorder by accident.
STAGE_ORDER = ["listen", "stt", "turn", "brain", "harness", "tts", "look",
               # Who Butler is (docs/identity-memory.md 4.1, AC-370): not a
               # stage of the audio path, so it sits after them, unstaged.
               "identity",
               # Where a finished automation is sent (docs/automations.md 7).
               "notify",
               # When unrequested speech waits for morning (docs/proactive.md
               # PR-14).
               "quiet",
               # What can start an automation besides its schedule
               # (docs/proactive.md PR-26).
               "triggers",
               # Linking the user's WhatsApp (docs/messaging.md MS-5).
               "whatsapp",
               # The phone address as a QR code (docs/desktop-app.md DA-15).
               "devices",
               # Who may open Butler, paired devices (docs/remote-access.md).
               "access"]

# The subset of those that are PIPELINE STAGES: sound reaches each of them in
# turn. `look` is not one -- how the page is painted is not a step in the
# audio path -- and the page draws the chain from `stage`, so the two lists
# have to be different things rather than one list with an exception at the
# end.
PIPELINE_STAGES = ["listen", "stt", "turn", "brain", "harness", "tts"]

# The engine seams that must each have a settings home of their own. A reader
# configuring speech recognition should not have to know that its options live
# in a group called "engines" beside the end-of-turn model. `harness` joined
# on 2026-08-18 with the code that reads its settings (docs/harness-mode.md
# 7.4): it is the fifth port, and a seam gets one home.
SEAM_GROUPS = {"stt", "tts", "brain", "harness"}


def test_settings_groups_follow_the_pipeline():
    """AC-45. The settings page is organised by SEAM, in pipeline order.

    Before this, groups were organised by how fiddly a setting felt:
    "Engines (need a restart)" held speech recognition next to the end-of-turn
    model, "Voice and cues" held the TTS voice, and the audio gate lived in
    "Advanced: listening". Nothing told a reader where to configure speech
    recognition, because recognition was not a place.
    """
    from daemon.settings import GROUPS
    assert list(GROUPS) == STAGE_ORDER, (
        f"settings groups must run in pipeline order {STAGE_ORDER}, got "
        f"{list(GROUPS)}. The order is the organising idea: you configure "
        f"Butler in the order your voice travels through it.")


def test_pipeline_stages_are_contiguous_and_follow_the_key_order():
    """AC-45. The stage number is DATA now, not a digit glued to a label.

    The order used to be faked into the label text -- "1. Listening",
    "5. Harness" -- so the only thing that knew the pipeline was a string the
    page printed. A stage that skips a number, repeats one, or disagrees with
    the key order draws a chain with a hole in it, and the page cannot tell
    because the page is not allowed to know what any of these groups are
    (AC-41, web/AGENTS.md).

    Two failures this catches that the key-order test cannot: a group given a
    stage that does not match its position, and a label that starts numbering
    itself again.
    """
    from daemon.settings import GROUPS
    staged = [(k, g.stage) for k, g in GROUPS.items() if g.stage is not None]
    assert [k for k, _ in staged] == PIPELINE_STAGES, (
        f"the groups carrying a stage must be the pipeline {PIPELINE_STAGES} "
        f"in that order, got {[k for k, _ in staged]}")
    assert [n for _, n in staged] == list(range(1, len(PIPELINE_STAGES) + 1)), (
        f"stages must run contiguously from 1 in key order, got "
        f"{[n for _, n in staged]}")
    for key in set(GROUPS) - set(PIPELINE_STAGES):
        assert GROUPS[key].stage is None, (
            f"{key} is not a stage of the audio path but carries stage "
            f"{GROUPS[key].stage}")
    numbered = [k for k, g in GROUPS.items() if g.label[:1].isdigit()]
    assert numbered == [], (
        f"these labels number themselves: {numbered}. The order is `stage` "
        f"now; a label that restates it is the display hack this replaced.")


def test_every_engine_seam_group_shows_a_headline_setting():
    """Each seam's box on the page names one current fact about that seam.

    A box reading only "Brain" tells the owner nothing they did not already
    know. The one that matters -- which brain, which harness, which voice --
    is a SETTING, and the page may not choose it: naming a setting in `web/`
    is what AC-41's grep gate forbids, so the daemon says which.
    """
    from daemon.settings import GROUPS
    blank = sorted(k for k in SEAM_GROUPS if not GROUPS[k].headline)
    assert blank == [], (
        f"these engine seams have no headline setting: {blank}. The page "
        f"renders their box with a label and no value.")


def test_every_headline_names_a_setting_that_exists():
    """A headline pointing at nothing serves a box with a blank in it.

    Renaming a setting is how it happens, and the rename looks complete: the
    schema is consistent, the page renders, and one box quietly stops saying
    anything. The daemon refuses to import instead (fail fast); this is the
    witness that the refusal is aimed at the right thing.
    """
    from daemon.settings import BY_KEY, GROUPS
    orphans = sorted(g.headline for g in GROUPS.values()
                     if g.headline is not None and g.headline not in BY_KEY)
    assert orphans == [], (
        f"these headlines name settings that do not exist: {orphans}")


def test_every_engine_seam_has_its_own_settings_group():
    """Each swappable engine is configured in one named place."""
    from daemon.settings import GROUPS
    missing = sorted(SEAM_GROUPS - set(GROUPS))
    assert missing == [], (
        f"these seams have no settings group of their own: {missing}")


def test_no_settings_group_is_empty():
    """An empty group renders as a bare heading with nothing under it.

    The likely way to get one is a reorganisation that moves the last setting
    out of a group and leaves the group behind, which is exactly what this
    change risked.
    """
    from daemon.settings import GROUPS, SCHEMA
    used = {s.group for s in SCHEMA}
    empty = sorted(set(GROUPS) - used)
    assert empty == [], f"groups with no settings in them: {empty}"


def test_advanced_is_a_setting_flag_not_a_group():
    """AC-45. A seam's advanced options belong WITH that seam.

    They used to live in global "Advanced: ..." groups, so the end-of-turn
    thresholds sat in a different section of the page from the turn mode they
    modify. Splitting one seam across two places is what this change exists to
    stop, so the group names must no longer encode the tier.
    """
    from daemon.settings import GROUPS, SCHEMA
    tiered = [k for k in GROUPS if k.startswith("advanced")]
    assert tiered == [], (
        f"advanced is a per-setting flag now, not a group: {tiered}")
    assert any(s.advanced for s in SCHEMA), (
        "no setting is marked advanced, so the everyday view would carry "
        "every threshold in the system")


# --------------------------------------------------------------------------- #
# Where the schema meets the page: appearance (AC-173, AC-680)
# --------------------------------------------------------------------------- #
WEB = REPO / "web"

# The state colours (docs/lantern-ui.md CU-4, AC-680). Hue is how the page
# says what Butler is doing -- blue while it listens and hears, amber while it
# thinks and speaks, red for an error, a lost link or a recording room -- so
# the colour VALUES live in exactly one place: each style's own block, which
# declares `--blue`, `--amber` and `--red` per theme. Every other token that
# carries state is DERIVED from those three by reference: the `[data-state]`
# rows' `--sa`/`--sa-dim`/`--g1`/`--g2`, the warning and running colours,
# and the voice-effect pair `--in`/`--out`. A literal colour in any of them is
# a second copy of the decision, and the copy is the one that stops agreeing
# when a style is retuned. This REPLACES AC-174's gate ("no palette block
# declares region 1"): the palettes retired with `colour_scheme`.
#
# `--run` joined 2026-09-16 (docs/triage-list.md, D-12 fix) and is held to the
# rule `--warn` is: a background conversation's colour, never the page's own
# turn accent.
STYLE_COLOURS = ("--blue", "--amber", "--red")
STATE_TOKENS = ("--sa", "--sa-dim", "--g1", "--g2", "--warn", "--run",
                "--in", "--out")


def _stylesheet() -> str:
    """Every CSS rule the page ships, with comments stripped.

    `web/index.html` carries the whole stylesheet inline today; a `web/*.css`
    beside it would be the same shipped surface, so both are read rather than
    the one that happens to hold it this week. `fonts.css` is excluded: it
    declares faces only, no colour at all.

    Comments go first, or a paragraph explaining a scheme reads as a rule.
    """
    parts = []
    html = (WEB / "index.html").read_text(encoding="utf-8", errors="replace")
    parts += re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    parts += [p.read_text(encoding="utf-8", errors="replace")
              for p in sorted(WEB.glob("*.css")) if p.name != "fonts.css"]
    return re.sub(r"/\*.*?\*/", " ", "\n".join(parts), flags=re.S)


def _rules(css: str) -> list[tuple[str, str]]:
    """`(selector, body)` for every innermost rule; a rule inside `@media`
    is matched too, because the inner rule is what the regex lands on."""
    return [(sel.strip(), body)
            for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)]


# What may sit in a DERIVED state token's value: references, the mixing
# function and its colour space, `transparent`, and numbers. Anything left
# after removing those is a literal colour (a hex, an `oklch(...)`, a name).
_REFERENCE_ONLY = re.compile(
    r"var\(--[\w-]+\)|color-mix\(|\bin\s+(?:oklab|oklch|srgb)\b|"
    r"\btransparent\b|[\d.]+%?|[(),\s]")


def _state_colour_faults(css: str) -> list[str]:
    """Every way a stylesheet declares a state colour outside a style.

    A list rather than an assertion so the gate and its own D-13 witness
    share one implementation: two copies of an instrument drift."""
    faults = []
    for selector, body in _rules(css):
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body):
            if name in STYLE_COLOURS and "data-style" not in selector:
                faults.append(
                    f"{selector!r} declares {name}, a state colour only a "
                    f"style block ([data-style]) may declare")
            if name in STATE_TOKENS and _REFERENCE_ONLY.sub("", value):
                faults.append(
                    f"{selector!r} gives {name} the literal colour "
                    f"{value.strip()!r}; a state token is derived from a "
                    f"style's --blue, --amber or --red by reference")
    return faults


def test_only_a_style_declares_a_state_colour():
    """AC-680 (docs/lantern-ui.md CU-4), replacing AC-174's palette gate.

    Each style owns its state colours, so the one decision "what colour is
    listening" is written once per style and theme. A `[data-state]` row, a
    `:root` default or a voice-effect token that spells a colour of its own
    is the second copy: it looks right in the style it was tuned in and
    wrong in the other, and only in one state.

    Guarded against vacuity: the gate must SEE the style colours and the
    state rows it is about, or a stylesheet that lost both would pass.
    """
    css = _stylesheet()
    rules = _rules(css)
    assert any("data-style" in sel and re.search(r"--blue\s*:", body)
               for sel, body in rules), (
        "no [data-style] block declares --blue, so this gate checks nothing")
    assert any("data-state" in sel and re.search(r"--sa\s*:", body)
               for sel, body in rules), (
        "no [data-state] row declares --sa, so this gate checks nothing")
    faults = _state_colour_faults(css)
    assert faults == [], "\n".join(faults)


def test_the_state_colour_gate_would_catch_one():
    """D-13 for the gate above, over stylesheets written by hand: one LEGAL
    page and four ways AC-680 fails. The legal case is half the instrument;
    a detector that flagged references would teach the next reader to delete
    the gate."""
    legal = """
      [data-theme="dark"][data-style="lantern"] { --bg: #111; --blue: #8cbdf2; }
      [data-style] { --warn: var(--red); --in: var(--blue); }
      [data-state="idle"] { --sa: color-mix(in oklab, var(--blue) 70%, var(--faint));
                            --sa-dim: color-mix(in oklab, var(--blue) 14%, transparent); }
    """
    assert _state_colour_faults(legal) == [], _state_colour_faults(legal)
    for bad in ('[data-state="speaking"] { --sa: oklch(0.87 0.17 94); }',
                ':root { --blue: #2c6ab3; }',
                '[data-style] { --warn: gold; }',
                ':root { --in: #78abff; }'):
        assert _state_colour_faults(bad), f"not caught: {bad}"


def test_the_charts_series_tone_is_not_the_styles_tone():
    """`--tone` is the export's surface token (a style declares it); the
    chart's per-series shade used to be a local `--tone` too, so a series
    rule and the style fought over one name inside a chart. Only a style
    block may declare `--tone`."""
    other = [sel for sel, body in _rules(_stylesheet())
             if re.search(r"--tone\s*:", body) and "data-style" not in sel]
    assert other == [], f"--tone is declared outside a style: {other}"


def test_the_wake_state_visual_is_deleted():
    """R5 (CLAUDE.md rulings of 2026-09-16): `[data-state="wake"]` styled a
    row the state machine can never enter -- `STATE_VIEW[WAKE].attr` in
    `app.js` is `"idle"` on purpose, so `data-state` is never assigned the
    string `"wake"` (D-30, AC-93). A styled state nothing can dress the page
    in is one decision written twice; this gate is the one that stays red if
    the row comes back without a caller.
    """
    css = _stylesheet()
    assert 'data-state="wake"' not in css, (
        'a [data-state="wake"] rule survives in the stylesheet, but nothing '
        'ever sets data-state to "wake" (STATE_VIEW[WAKE].attr is "idle") -- '
        'R5 ruled this dead row deleted, not merely unreachable')


def test_a_background_conversations_running_colour_is_not_the_page_turn_accent():
    """D-12 fix, docs/triage-list.md. Found by LOOKING at a screenshot, not
    by a test -- this is the test that should have caught it.

    The shipped rule read `.chatrow .rowstatus[data-status="running"] {
    color: var(--sa); }`. `--sa` is redeclared inside every `[data-state]`
    row (`[data-state="off"]` to grey, `[data-state="speaking"]` to bright
    gold), so a background conversation's colour tracked whatever the LOCAL
    page's own turn was doing rather than the fact it was meant to report.
    `--run` is the fix: declared once, outside every `[data-state]` row, and
    derived from a style colour by reference, exactly like `--warn`.
    """
    css = _stylesheet()
    rule = re.search(
        r'\.chatrow \.rowstatus\[data-status="running"\]\s*\{([^}]*)\}', css)
    assert rule, ("no rule styles .chatrow .rowstatus[data-status=\"running\"]"
                  " at all -- a running row would draw with no colour")
    body = rule.group(1)
    assert "var(--sa)" not in body, (
        f"the running row still reads {body!r} -- --sa is the PAGE's own "
        f"turn accent and is redeclared inside every [data-state] row, so a "
        f"background conversation's colour would track whatever this page's "
        f"own turn is doing (grey while off, gold while speaking)")
    assert re.search(r"var\(--run\)", body), (
        f"the running row does not read var(--run): {body!r}")

    # --run must be declared OUTSIDE every [data-state] row, or the same
    # borrowing defect returns one token later under a new name.
    state_rows = [sel for sel, b in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
                  if "--run" in b and "data-state" in sel]
    assert state_rows == [], (
        f"--run is declared inside a [data-state] rule: {state_rows} -- "
        f"that makes it a second state token that varies with the page's "
        f"own turn, the exact defect this test exists to catch")


def test_every_reading_face_is_a_family_the_page_actually_ships():
    """AC-173 and AC-42. The daemon names the faces; the page draws them.

    The failure this catches is the one `asr_latency` already shipped here:
    a control offering a value nothing downstream can honour, which saves,
    reports success and silently does something else -- AC-7b by the front
    door. A face the page cannot draw falls back to the browser's default
    sans, so the control appears to work and reads as "that font is ugly"
    rather than "that font is not here".

    Read off `fonts.css`, the tracked file the page links, so this
    is the shipped artefact rather than anybody's copy of it. The woff2
    files themselves are gitignored and deliberately not checked: their
    absence is a checkout that has not run the fetch script, not a schema
    that lies.
    """
    css = (WEB / "fonts.css").read_text(encoding="utf-8", errors="replace")
    declared = set(re.findall(r"font-family:\s*['\"]([^'\"]+)['\"]", css))
    assert declared, (
        "web/fonts.css declares no @font-face family at all, so this gate "
        "would pass whatever the schema offered")
    # "" is "Style default" (docs/lantern-ui.md CU-5): the page writes no
    # face for it, so it is the ONE value that is not a family.
    missing = sorted(set(BY_KEY["reading_face"].choices) - {""} - declared)
    assert missing == [], (
        f"the schema offers reading faces the page does not ship: {missing}. "
        f"web/fonts.css has {sorted(declared)} -- add its @font-face "
        f"there and its file to scripts/fetch-fonts.py, or take the face "
        f"out of the schema (AC-173, AC-42).")


def test_a_long_labelled_choice_is_a_select_not_a_row_of_buttons():
    """docs/lantern-ui.md CU-5. Labels make a choice readable AND, until now,
    made it a row of buttons (AC-66). `reading_face` labels all seven values
    so "Style default" is not a blank entry, and seven buttons is a worse
    dropdown -- so `settings.js` keeps a labelled choice on buttons only up
    to a COUNT, and a longer one is a select showing the labels. The page
    names no setting for this (AC-41); the test may.

    Both sides of the line are pinned: the widest button row today
    (`viz_placement`) stays buttons, the seven faces become a select, and the
    rule is the one `buildChoice` actually consults."""
    js = (WEB / "settings.js").read_text(encoding="utf-8")
    m = re.search(r"const BUTTONS_MAX = (\d+);", js)
    assert m, "settings.js states no BUTTONS_MAX"
    limit = int(m.group(1))
    assert len(BY_KEY["viz_placement"].choices) <= limit, (
        "the wave-placement row would turn into a select")
    assert len(BY_KEY["reading_face"].choices) > limit, (
        "seven reading faces would be drawn as seven buttons")
    body = js[js.index("function buildChoice(spec, host, prefix)"):]
    body = body[:body.index("function buildChoiceButtons(")]
    assert "BUTTONS_MAX" in body, "buildChoice does not consult the count"
    assert re.search(r"named \? labels\[c\] : c", body), (
        "the select does not show the labels it was sent")


def test_every_font_size_is_expressed_against_the_text_scale():
    """R7 (CLAUDE.md rulings of 2026-09-16). `text_scale` writes ONE custom
    property, `--scale`, and every font-size in the page must read it --
    a second px table that `text_scale` does not touch is the drifted copy
    AC-42's own argument is about, applied to size instead of family.

    Relative units are exempt on purpose: `em`/`rem`/`inherit` already scale
    with their parent's computed size, so wrapping them in `calc(var(
    --scale) * ...)` would apply the multiplier twice.
    """
    css = _stylesheet()
    declared = re.findall(r"font-size\s*:\s*([^;]+);", css)
    assert declared, "no font-size declaration found at all"
    unscaled = [v.strip() for v in declared
                if re.search(r"\d+(\.\d+)?px", v)
                and "var(--scale)" not in v]
    assert unscaled == [], (
        f"{len(unscaled)} font-size declarations use a bare px value with "
        f"no var(--scale): {unscaled[:5]}... -- text_scale would resize "
        f"some of the page and leave the rest, which is AC-7b in miniature")


def _scheme_lit_rules(css: str) -> list[tuple[str, str]]:
    """`(selector, body)` for every rule that declares `color-scheme`.

    Shares `_stylesheet()` with the gates above, so this reads the shipped
    artefact and not a copy of it -- the property of those gates worth
    keeping.
    """
    return [(sel.strip(), body)
            for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
            if re.search(r"\bcolor-scheme\s*:", body)]


def _lit_faults(rules) -> list[str]:
    """Every way the page fails to tell the browser which way it is lit.

    A list rather than an assertion, so the gate below and its own D-13
    witness share one implementation: two copies of an instrument drift, and
    a drifted instrument is a test passing for the wrong reason.
    """
    values = {v for _, body in rules
              for v in re.findall(r"\bcolor-scheme\s*:\s*([\w-]+)", body)}
    faults = []
    if "dark" not in values:
        faults.append(
            "no rule declares `color-scheme: dark`, so the browser draws its "
            "own scrollbars, selects, steppers and range rails in LIGHT over "
            "the dark theme")
    if "light" not in values:
        faults.append(
            "no rule declares `color-scheme: light`, so those widgets stay "
            "dark after the owner switches to the light theme")
    lit_light = [sel for sel, body in rules
                 if re.search(r"\bcolor-scheme\s*:\s*light\b", body)]
    if lit_light and not any("light" in sel for sel in lit_light):
        faults.append(
            f"`color-scheme: light` is declared by {lit_light}, none of which "
            f"names the light theme -- so it applies in both themes or in "
            f"neither, which is the same as not declaring it")
    return faults


def test_the_page_tells_the_browser_which_way_it_is_lit():
    """`color-scheme`, and it is a GATE because this line was lost once.

    `web/mock.html` declares it on both themes and the port into
    `index.html` did not carry it across, so the shipped page ran with the
    user agent's colour scheme at `normal` while `data-theme` said `dark`.
    MEASURED on the page 2026-09-10: the sidebar's scrollbar was a pale grey
    band with Fluent arrow buttons, and 13 selects, 20 range sliders, 20
    number inputs, 5 checkboxes and a search field on the settings surface
    all drew light-mode chrome over a near-black panel.

    Nothing went red for it, and nothing could have: the probes read the DOM
    and pytest reads the wire, and this is the one part of the page NEITHER
    of them draws. That is the whole argument for a grep gate here rather
    than a note in a doc -- the same argument AC-8's localStorage ban and
    AC-680's state-colour gate are already built on.

    BOTH values are required, because declaring one is the defect wearing
    half a fix.
    """
    faults = _lit_faults(_scheme_lit_rules(_stylesheet()))
    assert faults == [], (
        "\n".join(faults) + "\nA scrollbar, a select popup and a number "
        "stepper are drawn by the BROWSER, and `color-scheme` is the only "
        "thing that tells it which way this page is lit.")


def test_the_lit_gate_would_catch_one():
    """D-13 for the gate above, kept in the tree rather than in a harness.

    Three arms, because the rule fails three ways and a detector that only
    caught the loud one would pass the day the quiet one shipped. The LEGAL
    arm is half the instrument: a detector that flagged everything would
    redden on a correct page and teach the next reader to delete it.
    """
    legal = """
      :root { color-scheme: dark; }
      [data-theme="light"] { color-scheme: light; }
    """
    assert _lit_faults(_scheme_lit_rules(legal)) == [], (
        f"a page declaring both was flagged; the gate would redden on a "
        f"correct page: {_lit_faults(_scheme_lit_rules(legal))}")

    assert _lit_faults(_scheme_lit_rules(":root { --bg: black; }")), (
        "a stylesheet declaring no colour-scheme at all was not caught, "
        "which is the exact state the shipped page was in")

    half = ':root { color-scheme: dark; }'
    assert _lit_faults(_scheme_lit_rules(half)), (
        "a page declaring only `dark` was not caught -- the light theme "
        "would keep dark widgets and look broken in the other direction")

    # The quiet one: both values present, but `light` bound to nothing that
    # names the light theme, so it wins in BOTH themes or in neither.
    unbound = """
      :root { color-scheme: dark; }
      .somewhere-else { color-scheme: light; }
    """
    assert _lit_faults(_scheme_lit_rules(unbound)), (
        "`color-scheme: light` on a selector that does not name the light "
        "theme was not caught; that declaration cannot do its job and the "
        "page looks exactly as broken as with no declaration at all")

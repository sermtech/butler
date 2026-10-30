"""Who Butler is, on every path, in any directory. docs/identity-memory.md.

Section 11 (AC-420..AC-429, the owner's ruling of 2026-09-26): TWO FILES in
the persona folder (`paths.Paths.persona` -- the data root, or the records
folder on the legacy layout), `SOUL.md` (who Butler is) and `USER.md` (who
the owner is). Each starts with an HTML comment of instructions for the
owner, stripped before anything is sent. They replace 4.1's one `identity`
setting, which is migrated into them once (AC-427).

Still asserted from phases 1-3: blank is byte-identical (AC-371 as amended
by AC-422), the TEXT never reaches the argv (AC-373), an edit replaces a held
child on its next turn (AC-374), `--no-context-files` is never sent (AC-376),
and the router is never shown any of it (4.4).

Four failures this file exists to catch, each a different one.

**Guidance costs nothing.** The instruction comment is for the owner. If it
reached a model it would be paid for on every turn and read as an order.

**A template is not content.** A fresh install has both files, holding only a
comment and a heading. That must send exactly what HEAD sent (AC-422).

**Boot never overwrites.** The owner's file is theirs; a template written
over it on a restart is their persona gone.

**Over the cap is refused, never cut** -- at write time through the page,
and at READ time for a hand edit, where the last good version stays in use.
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import StubResolver                              # noqa: E402
from daemon import butlerd, dialogue, files, guide, persona    # noqa: E402
from daemon import settings as settings_mod                    # noqa: E402
from daemon.adapters.brain_endpoint import (EndpointConverse,  # noqa: E402
                                            REPLY_SYSTEM)
from daemon.memory import MemoryStore                          # noqa: E402
from daemon.persona import Persona                             # noqa: E402
from daemon.ports import TurnContext                           # noqa: E402
from daemon.settings import (BY_KEY, GROUPS, Settings,         # noqa: E402
                             SettingsError)

TURN_BUDGET_S = 15.0
SOUL_TEXT = "# Soul\n\nYour name is Jeeves. You are calm & dry; say \"sure\" rarely."
USER_TEXT = "# User\n\nMy name is Owner. Call me Owner."
HISTORY = (("user", "what time is it"), ("assistant", "Just past four."))
ASKED = "what is my name"


def _ctx(history=HISTORY):
    return TurnContext(turn_id=1, mode="secretary", history=history)


async def _drain(stream) -> list:
    async def collect():
        return [intent async for intent in stream]
    return await asyncio.wait_for(collect(), TURN_BUDGET_S)


def _files(folder: Path) -> dict:
    """Both files, created from their templates, as boot does."""
    found = persona.files_for(folder)
    for f in found.values():
        f.ensure()
    return found


def _persona(tmp_path, soul="", user=""):
    found = _files(tmp_path)
    prefs = Settings(tmp_path / "settings.json", files=found)
    if soul:
        prefs.set("soul", soul)
    if user:
        prefs.set("user", user)
    return prefs, Persona(found, MemoryStore(tmp_path / "memory"),
                          prompt_dir=tmp_path / "prompt")


# --------------------------------------------------------------------------- #
# AC-426: two file-backed settings
# --------------------------------------------------------------------------- #
def test_soul_and_user_are_multiline_settings_backed_by_files():
    """AC-426, AC-424. Rendered by kind, so `web/` names neither (AC-41)."""
    assert "identity" not in BY_KEY, "the one-setting identity is retired"
    for key, name, cap in (("soul", "SOUL.md", 4000), ("user", "USER.md", 2000)):
        spec = BY_KEY[key]
        assert spec.kind == "multiline", spec
        assert spec.file == name, spec
        assert spec.maximum == cap, spec
        assert spec.group == "identity", spec
        assert spec.default == "" and spec.restart is False, spec
    assert GROUPS["identity"].label == "Identity"


def test_templates_are_created_only_when_absent(tmp_path):
    """AC-420. The FIRST boot writes each template; every later boot leaves
    the owner's file byte-identical."""
    found = persona.files_for(tmp_path)
    soul, user = found["soul"], found["user"]
    assert soul.path == tmp_path / "SOUL.md"
    assert user.path == tmp_path / "USER.md"
    assert soul.ensure() is True and user.ensure() is True
    for f, heading in ((soul, "# Soul"), (user, "# User")):
        text = f.path.read_text(encoding="utf-8")
        assert text.lstrip().startswith("<!--"), text[:40]
        assert "-->" in text and heading in text.split("-->", 1)[1], text
    mine = b"# Soul\r\n\r\nMine, hand-written.\r\n"
    soul.path.write_bytes(mine)
    assert soul.ensure() is False
    assert persona.files_for(tmp_path)["soul"].ensure() is False
    assert soul.path.read_bytes() == mine, "boot rewrote the owner's file"


def test_a_template_only_file_sends_nothing(tmp_path):
    """AC-421: a comment and a heading say nothing about anybody."""
    found = _files(tmp_path)
    assert found["soul"].effective() == ""
    assert found["user"].effective() == ""
    assert found["soul"].body() == "# Soul", found["soul"].body()


def test_every_comment_is_stripped_from_what_is_sent(tmp_path):
    """AC-421: the header is guidance for the owner, and so is any comment
    they add further down."""
    found = _files(tmp_path)
    soul = found["soul"]
    header = soul.path.read_text(encoding="utf-8").split("-->", 1)[0] + "-->"
    soul.path.write_text(header + "\n\n# Soul\n\nYour name is Jeeves.\n"
                         "<!-- note to self: maybe Alfred -->\nBe brief.\n",
                         encoding="utf-8")
    sent = soul.effective()
    assert "Your name is Jeeves." in sent and "Be brief." in sent
    assert "<!--" not in sent and "Alfred" not in sent, sent
    assert "Write here" not in sent, "the instruction header was sent"


# --------------------------------------------------------------------------- #
# AC-426: the settings page edits the files
# --------------------------------------------------------------------------- #
def test_a_settings_write_is_the_file_body_and_keeps_the_header(tmp_path):
    found = _files(tmp_path)
    header = found["soul"].path.read_text(encoding="utf-8").split("-->")[0]
    prefs = Settings(tmp_path / "settings.json", files=found)

    assert prefs.set("soul", "\n Your name is Alfred.  \n") == \
        "Your name is Alfred."

    text = found["soul"].path.read_text(encoding="utf-8")
    assert text.startswith(header), "the instruction comment was not kept"
    assert text.split("-->", 1)[1].strip() == "Your name is Alfred."
    assert prefs["soul"] == "Your name is Alfred."
    assert found["soul"].effective() == "Your name is Alfred."
    assert not (tmp_path / "settings.json").exists(), (
        "a file-only write touched settings.json")
    prefs.set("theme", "light")
    stored = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert "soul" not in stored and "user" not in stored, stored
    served = {s["key"]: s for s in prefs.describe()["settings"]}
    assert served["soul"]["value"] == "Your name is Alfred."
    assert served["soul"]["kind"] == "multiline"
    assert served["soul"]["max"] == 4000
    assert served["soul"]["changed"] is True
    assert served["user"]["value"] == "# User" and served["user"]["problem"] == ""
    assert served["user"]["changed"] is False, (
        "a template-only file sends nothing, so it is not a change")


def test_a_write_keeps_a_header_the_owner_rewrote(tmp_path):
    found = _files(tmp_path)
    found["user"].path.write_text("<!-- my own notes -->\n\nold\n",
                                  encoding="utf-8")
    Settings(tmp_path / "s.json", files=found).set("user", "new")
    assert found["user"].path.read_text(encoding="utf-8") == \
        "<!-- my own notes -->\n\nnew\n"


def test_a_page_write_over_the_cap_is_refused_and_the_file_survives(tmp_path):
    """AC-424: refused LOUDLY, never truncated."""
    found = _files(tmp_path)
    prefs = Settings(tmp_path / "s.json", files=found)
    prefs.set("user", "short")
    before = found["user"].path.read_bytes()
    with pytest.raises(SettingsError, match="2000"):
        prefs.set("user", "x" * 2001)
    assert found["user"].path.read_bytes() == before
    assert prefs["user"] == "short"
    assert prefs.set("user", "y" * 2000) == "y" * 2000


def test_without_files_a_file_setting_reads_blank_and_refuses_a_write(
        tmp_path):
    """A store built with no persona folder (the suite's bare constructions)
    must not pretend to save a persona anywhere."""
    prefs = Settings(tmp_path / "s.json")
    assert prefs["soul"] == ""
    with pytest.raises(SettingsError, match="soul"):
        prefs.set("soul", "Your name is Alfred.")
    assert prefs.reset("identity") == {}


def test_a_multiline_value_must_be_text(tmp_path):
    with pytest.raises(SettingsError):
        Settings(tmp_path / "s.json", files=_files(tmp_path)).set(
            "soul", ["not", "text"])


# --------------------------------------------------------------------------- #
# AC-424, AC-425: a hand edit
# --------------------------------------------------------------------------- #
def test_a_hand_edit_over_the_cap_keeps_the_last_good_and_says_so(
        tmp_path, capsys):
    found = _files(tmp_path)
    prefs = Settings(tmp_path / "s.json", files=found)
    prefs.set("user", "My name is Owner.")
    user = found["user"]
    assert user.effective() == "My name is Owner."

    header = user.path.read_text(encoding="utf-8").split("-->")[0] + "-->"
    user.path.write_text(header + "\n\n" + "z" * 2500 + "\n", encoding="utf-8")

    assert user.effective() == "My name is Owner.", "the over-cap file was sent"
    why = user.problem()
    assert "USER.md" in why and "2500" in why and "2000" in why, why
    assert "USER.md" in capsys.readouterr().out, "no log line named the file"
    served = {s["key"]: s for s in prefs.describe()["settings"]}
    assert served["user"]["problem"] == why
    assert served["user"]["value"] == "z" * 2500, (
        "the page must show the file as it is, so the owner can trim it")

    user.path.write_text(header + "\n\nMy name is Owner M.\n",
                         encoding="utf-8")
    assert user.effective() == "My name is Owner M."
    assert user.problem() == ""


def test_an_over_cap_file_at_boot_sends_nothing(tmp_path):
    """No last good version exists before the first good read."""
    (tmp_path / "SOUL.md").write_text("q" * 4001, encoding="utf-8")
    soul = persona.files_for(tmp_path)["soul"]
    assert soul.effective() == ""
    assert "4001" in soul.problem()


def test_a_file_that_is_not_utf8_never_fails_a_turn(tmp_path, capsys):
    found = _files(tmp_path)
    Settings(tmp_path / "s.json", files=found).set("soul", "Be brief.")
    found["soul"].path.write_bytes(b"\xff\xfe\x00bad")
    assert found["soul"].effective() == "Be brief."
    assert "SOUL.md" in found["soul"].problem()
    assert "SOUL.md" in capsys.readouterr().out


def test_a_file_is_re_read_only_when_its_stamp_changes(tmp_path):
    """AC-425. Same size and same mtime is the same file, so the cached copy
    answers; a new mtime is read again."""
    found = _files(tmp_path)
    soul = found["soul"]
    soul.path.write_text("Name: Jeeves.", encoding="utf-8")
    assert soul.effective() == "Name: Jeeves."
    stat = soul.path.stat()
    soul.path.write_text("Name: Alfie!.", encoding="utf-8")   # same length
    os.utime(soul.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert soul.effective() == "Name: Jeeves.", "re-read with an unchanged stamp"
    os.utime(soul.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert soul.effective() == "Name: Alfie!."


# --------------------------------------------------------------------------- #
# AC-427: the retired setting moves into the files
# --------------------------------------------------------------------------- #
OLD = ("Preamble line.\n\n## About you\nI am Owner.\n### Work\nProfessor.\n\n"
       "## About Butler\nI am Jeeves.")


def test_the_old_identity_is_split_into_the_files(tmp_path, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"identity": OLD, "theme": "dark"}),
                        encoding="utf-8")
    found = _files(tmp_path)

    placed, left = persona.migrate_identity(settings, found)

    assert sorted(placed) == ["SOUL.md", "USER.md"] and left == []
    assert found["soul"].body() == "# Soul\n\nPreamble line.\n\nI am Jeeves."
    assert found["user"].body() == "# User\n\nI am Owner.\n### Work\nProfessor."
    assert found["soul"].path.read_text(encoding="utf-8").lstrip().startswith(
        "<!--"), "the migrated file lost its instruction comment"
    stored = json.loads(settings.read_text(encoding="utf-8"))
    assert "identity" not in stored, "the key survived, so it would repeat"
    assert stored["theme"] == "dark", "another setting was lost"
    assert persona.migrate_identity(settings, found) == ([], [])


def test_the_migration_never_overwrites_an_owned_file(tmp_path, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"identity": OLD}), encoding="utf-8")
    found = _files(tmp_path)
    found["soul"].path.write_text("# Soul\n\nAlready mine.\n", encoding="utf-8")

    placed, left = persona.migrate_identity(settings, found)

    assert placed == ["USER.md"] and left == ["SOUL.md"]
    assert found["soul"].path.read_text(encoding="utf-8") == \
        "# Soul\n\nAlready mine.\n"
    assert "identity" in json.loads(settings.read_text(encoding="utf-8")), (
        "text with no home was deleted")
    assert "SOUL.md" in capsys.readouterr().out


def test_no_old_identity_is_a_no_op(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"theme": "dark"}', encoding="utf-8")
    before = settings.read_bytes()
    assert persona.migrate_identity(settings, _files(tmp_path)) == ([], [])
    assert persona.migrate_identity(tmp_path / "none.json",
                                    _files(tmp_path)) == ([], [])
    assert settings.read_bytes() == before


# --------------------------------------------------------------------------- #
# AC-428: D2 stands
# --------------------------------------------------------------------------- #
def test_nothing_butler_adds_names_a_language(tmp_path):
    """AC-428, language.md D2: the templates (comment included), the labels,
    the help and the group's blurb name no language."""
    found = _files(tmp_path)
    said = " ".join([f.path.read_text(encoding="utf-8") for f in found.values()]
                    + [BY_KEY[k].help + " " + BY_KEY[k].label
                       for k in ("soul", "user")]
                    + [GROUPS["identity"].blurb, dialogue.SOUL_HEADING,
                       dialogue.USER_HEADING]).lower()
    for word in ("language", "english", "portuguese", "portugu",
                 "answer in", "reply in", "respond in"):
        assert word not in said, (
            f"{word!r} appears in what Butler adds around the persona; "
            f"D2 rules the brain gets no language instruction")


def test_the_files_are_not_secrets_and_not_skipped():
    """AC-429: the explorer lists them; they are the owner's files."""
    for name in ("SOUL.md", "USER.md"):
        assert not files.is_secret(name)
        assert name.casefold() not in files.SKIP


def test_the_explorer_lists_both_files_in_the_root_project(tmp_path):
    from daemon import shortcuts as shortcuts_mod
    _files(tmp_path / "home")
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    row = store.upsert("", str(tmp_path / "home"), "Butler")
    got = {e["name"]: e for e in files.list_level(store, row.id, "")["entries"]}
    assert {"SOUL.md", "USER.md"} <= set(got), got
    assert not got["SOUL.md"]["locked"] and not got["USER.md"]["locked"]


# --------------------------------------------------------------------------- #
# AC-422, AC-423: the conversation model
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_template_only_files_leave_the_endpoint_request_byte_identical(
        tmp_path, fake_openai):
    """AC-422, spelled as the exact message list HEAD sent."""
    _, persona_ = _persona(tmp_path)
    brain = EndpointConverse(StubResolver(fake_openai.base_url),
                             persona_for=persona_.blocks)

    await _drain(brain.turn(ASKED, _ctx()))

    assert fake_openai.last_body()["messages"] == [
        {"role": "system", "content": REPLY_SYSTEM},
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "Just past four."},
        {"role": "user", "content": ASKED},
    ], "template-only files changed the request (AC-422)"


@pytest.mark.asyncio
async def test_soul_then_user_reach_the_conversation_model_labelled(
        tmp_path, fake_openai):
    """AC-423: SOUL, then USER, each its own system message after
    REPLY_SYSTEM and before history."""
    _, persona_ = _persona(tmp_path, SOUL_TEXT, USER_TEXT)
    brain = EndpointConverse(StubResolver(fake_openai.base_url),
                             persona_for=persona_.blocks)

    await _drain(brain.turn(ASKED, _ctx()))

    messages = fake_openai.last_body()["messages"]
    assert messages[0] == {"role": "system", "content": REPLY_SYSTEM}
    assert messages[1] == {"role": "system", "content":
                           f"{dialogue.SOUL_HEADING}\n\n{SOUL_TEXT}"}
    assert messages[2] == {"role": "system", "content":
                           f"{dialogue.USER_HEADING}\n\n{USER_TEXT}"}
    assert messages[3:] == [
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "Just past four."},
        {"role": "user", "content": ASKED}]
    assert "Write here" not in json.dumps(messages), "a header was sent"


def test_the_labels_say_whose_file_it_is_and_that_speaking_rules_win():
    """Section 3's finding: a marker worded as a hidden instruction was
    refused as an exfiltration payload. Each label says who wrote it, whom
    it describes, and that the speaking rules win."""
    soul = dialogue.SOUL_HEADING.lower()
    user = dialogue.USER_HEADING.lower()
    for label, whom in ((soul, "you, butler"), (user, "the person you are")):
        assert "secret" not in label and "hidden" not in label
        assert "owner wrote" in label, label
        assert whom in label, label
        assert "win" in label, label
    assert "soul.md" in soul and "user.md" in user
    assert dialogue.soul_note("") == "" and dialogue.user_note("  \n") == ""


def test_build_hub_creates_the_files_and_hands_the_brain_the_persona(
        tmp_path, monkeypatch):
    """D-18 and AC-420: the adapter reading `persona_for` proves nothing
    unless the DAEMON builds the files and passes them. Legacy layout plus
    BUTLER_STATE puts the persona folder in the scratch records folder."""
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "endpoint"}})

    assert (tmp_path / "SOUL.md").is_file() and (tmp_path / "USER.md").is_file()
    # AC-444 (amending AC-422): the endpoint brain HAS the memory tools, so
    # a template-only install sends the Memory rules and nothing else.
    # The Tasks rules go wherever the Memory rules go (docs/automations.md
    # AU-9, AC-454), and so do the Reminder rules (docs/proactive.md
    # PR-20, AC-491), and Butler's own feature index (docs/butler-help.md
    # AC-641).
    # And the Projects block right after the Task rules, built from the
    # pane's rows at boot: here the one root row (docs/todo-list.md AC-675).
    root = next(r for r in hub.shortcuts.all()
                if r.id == hub.shortcuts.root_id())
    projects = dialogue.projects_note(
        [(root.label or settings_mod.project_name(root.path), root.path,
          True)])
    assert projects.endswith(" (main)")
    assert hub.brain._converse._persona_for() == (dialogue.MEMORY_RULES,
                                                  dialogue.TASK_RULES,
                                                  projects,
                                                  dialogue.REMINDER_RULES,
                                                  dialogue.RECIPE_RULES,
                                                  guide.block())
    hub.settings.set("user", "My name is Owner.")
    assert "My name is Owner." in (tmp_path / "USER.md").read_text("utf-8")
    assert any("My name is Owner." in b
               for b in hub.brain._converse._persona_for()), (
        "the page's write did not reach the next turn")
    (tmp_path / "SOUL.md").write_text("Your name is Jeeves.", encoding="utf-8")
    assert any("Your name is Jeeves." in b
               for b in hub.brain._converse._persona_for()), (
        "a hand edit did not reach the next turn")


def test_build_hub_migrates_the_old_setting(tmp_path, monkeypatch):
    """D-18 for AC-427: the migration runs at boot, on the caller."""
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    (tmp_path / "settings.json").write_text(json.dumps({"identity": OLD}),
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "endpoint"}})

    assert "I am Jeeves." in (tmp_path / "SOUL.md").read_text("utf-8")
    assert "I am Owner." in (tmp_path / "USER.md").read_text("utf-8")
    assert hub.settings["user"].endswith("Professor.")
    stored = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert "identity" not in stored


# --------------------------------------------------------------------------- #
# Phase 2 -- AC-373, AC-374, AC-376: the harness child
# --------------------------------------------------------------------------- #
from daemon.adapters.brain_harness import HarnessBrain         # noqa: E402
from daemon.adapters.harness_fake import FakeHarness           # noqa: E402
from daemon.adapters.harness_pi import PiHarness               # noqa: E402
from daemon.chats import ChatStore                             # noqa: E402

APPEND_FLAG = "--append-system-prompt"


def _harness_persona(tmp_path, soul="", user=""):
    prefs, persona_ = _persona(tmp_path, soul, user)
    prefs.set("harness_cwd", str(tmp_path))
    chats = ChatStore(tmp_path / "chats")
    resolve = butlerd.harness_spec_resolver(prefs, chats, (),
                                            persona_.append_file)
    return prefs, persona_, chats, resolve


def test_template_only_files_put_no_append_flag_on_the_argv(tmp_path):
    """AC-422's harness half: no content, no flag at all -- not a flag
    pointing at an empty file."""
    _, _, _, resolve = _harness_persona(tmp_path)
    spec = resolve()
    assert spec.append_prompt is None
    assert APPEND_FLAG not in PiHarness()._argv(spec)
    assert not (tmp_path / "prompt").exists() or not any(
        (tmp_path / "prompt").iterdir()), "a blank persona wrote a file"


def test_the_persona_reaches_the_harness_by_a_minted_path(tmp_path):
    """AC-373: the PATH is on the argv and the TEXT never is."""
    _, _, _, resolve = _harness_persona(tmp_path, SOUL_TEXT, USER_TEXT)
    spec = resolve()
    path = Path(spec.append_prompt)
    content = path.read_bytes()
    sha12 = hashlib.sha256(content).hexdigest()[:12]
    assert path == tmp_path / "prompt" / f"append-{sha12}.md", path
    text = content.decode("utf-8")
    assert text.index(SOUL_TEXT) < text.index(USER_TEXT), "SOUL goes first"
    assert dialogue.SOUL_HEADING in text and dialogue.USER_HEADING in text
    assert "Write here" not in text, "an instruction header reached the child"

    argv = PiHarness()._argv(spec)
    assert argv[argv.index(APPEND_FLAG) + 1] == str(path)
    joined = " ".join(argv)
    for fragment in ("Owner", "Jeeves", "calm & dry"):
        assert fragment not in joined, (
            f"persona text reached the argv, which `cmd` re-parses: {argv}")


def test_editing_either_file_changes_the_rendered_path(tmp_path):
    """AC-423: same text, same file, written once; an edit to either file --
    through the page or by hand -- is a new path, so a new spec."""
    prefs, persona_, _, resolve = _harness_persona(tmp_path, SOUL_TEXT,
                                                    USER_TEXT)
    first = resolve().append_prompt
    stamp = Path(first).stat().st_mtime_ns
    assert resolve().append_prompt == first
    assert Path(first).stat().st_mtime_ns == stamp, "rewritten when unchanged"
    prefs.set("user", USER_TEXT + "\nOne more line.")
    second = resolve().append_prompt
    assert second != first
    soul = tmp_path / "SOUL.md"
    soul.write_text(soul.read_text("utf-8") + "\nBe brief.\n", encoding="utf-8")
    os.utime(soul, ns=(soul.stat().st_atime_ns,
                       soul.stat().st_mtime_ns + 10**9))
    assert resolve().append_prompt not in (first, second), (
        "a hand edit to SOUL.md did not change the spec")


def test_butler_never_passes_no_context_files(tmp_path):
    """AC-376: per-project identity IS the project's own AGENTS.md."""
    for n, soul in enumerate(("", SOUL_TEXT)):
        home = tmp_path / str(n)
        home.mkdir()
        _, _, _, resolve = _harness_persona(home, soul)
        argv = PiHarness()._argv(resolve())
        assert "--no-context-files" not in argv and "-nc" not in argv, argv


@pytest.mark.asyncio
async def test_a_persona_edit_replaces_the_held_child_on_its_next_turn(
        tmp_path, fake_openai):
    """AC-374 through AC-60 and nothing else. Same session file across the
    replacement."""
    prefs, _, chats, resolve = _harness_persona(tmp_path, SOUL_TEXT)
    prefs.set("project_agent_limit", 10)
    child = FakeHarness()
    brain = HarnessBrain(
        resolver=StubResolver(fake_openai.base_url), harness_for=lambda _chat=None: child,
        spec_for=resolve, chats=chats,
        cap_for=lambda: prefs["harness_agents_max"],
        project_limit_for=lambda: prefs["project_agent_limit"],
        project_grace_for=lambda: prefs["project_idle_grace_minutes"])

    async def turn(n):
        ctx = TurnContext(turn_id=n, mode="secretary")
        await _drain(brain.turn("what does the endpointing module do", ctx))

    await turn(1)
    await turn(2)
    assert len(child.specs) == 1, "an unchanged persona respawned the child"

    prefs.set("soul", "# Soul\n\nYour name is Alfred.")
    await turn(3)

    assert len(child.specs) == 2, (
        f"a persona edit did not replace the held child: {child.specs}")
    assert child.disposals == 1
    assert "Alfred" in Path(child.specs[1].append_prompt).read_text(
        encoding="utf-8")
    assert child.specs[1].session_file == child.specs[0].session_file


def test_build_hub_hands_the_harness_spec_the_persona_and_prunes(
        tmp_path, monkeypatch):
    """D-18 for the harness path, plus the boot prune."""
    (tmp_path / "settings.json").write_text(
        json.dumps({"harness": "fake", "harness_cwd": str(tmp_path)}),
        encoding="utf-8")
    (tmp_path / "SOUL.md").write_text(SOUL_TEXT, encoding="utf-8")
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    stale = tmp_path / "prompt" / "append-000000000000.md"
    stale.parent.mkdir()
    stale.write_text("old", encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "harness"}})

    assert not stale.exists(), "boot left a rendered file no spec names"
    spec = hub.brain.host.desired()
    assert spec.append_prompt and Path(spec.append_prompt).parent == (
        tmp_path / "prompt"), spec
    assert SOUL_TEXT in Path(spec.append_prompt).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Phase 3 -- AC-375: the router routes personal questions, and sees nothing
# --------------------------------------------------------------------------- #
from daemon.adapters.brain_endpoint import EndpointBrain       # noqa: E402


def test_the_router_prompt_sends_personal_questions_to_converse():
    """AC-375."""
    said = dialogue.ROUTER_SYSTEM.lower()
    assert "about the user" in said and "about butler" in said
    assert "remember" in said
    tail = said[said.index("about the user"):]
    assert "converse" in tail[:300], tail[:300]


@pytest.mark.asyncio
async def test_the_router_is_never_shown_the_persona(tmp_path, fake_openai):
    """4.4, asserted on the wire of the COMPOSITE the daemon builds."""
    _, persona_ = _persona(tmp_path, SOUL_TEXT, USER_TEXT)
    brain = EndpointBrain(StubResolver(fake_openai.base_url),
                          persona_for=persona_.blocks)

    await _drain(brain.turn(ASKED, _ctx()))

    router_ask = json.dumps(fake_openai.asks[0].body)
    assert ASKED in router_ask, "the first ask was not the router's"
    assert "Owner" not in router_ask and "Jeeves" not in router_ask, (
        f"the router was shown the persona: {router_ask!r}")
    reply_ask = json.dumps(fake_openai.asks[-1].body)
    assert "Owner" in reply_ask and "Jeeves" in reply_ask

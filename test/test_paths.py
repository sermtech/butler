"""`daemon/paths.py`: where the code, Butler's records and the root project's
home live. `docs/data-root.md`, AC-410..AC-414.

Every test passes its OWN `env` dict, so nothing here reads or writes the
process environment, the owner's `state/`, or `E:\\Butler-Data`.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from daemon import paths  # noqa: E402


def test_nothing_set_is_the_legacy_layout_byte_for_byte():
    """AC-410: with no BUTLER_DATA, every path is exactly what it was before
    this module existed -- the repo's state/, ssl/, and the root project is
    the repo."""
    p = paths.resolve(env={})
    assert p.code == REPO
    assert p.data == REPO
    assert p.records == REPO / "state"
    assert p.ssl == REPO / "ssl"
    assert p.logs == REPO / "state"          # voice-trace.log sat in state/
    assert p.attachments == REPO / "state" / "attachments"


def test_a_data_root_puts_records_in_one_hidden_folder(tmp_path):
    """AC-411: the owner's ruling -- records in <DATA>/.butler, attachments
    beside the owner's files where an agent can open them."""
    p = paths.resolve(env={"BUTLER_DATA": str(tmp_path)})
    assert p.data == tmp_path
    assert p.records == tmp_path / ".butler"
    assert p.ssl == tmp_path / ".butler" / "ssl"
    assert p.logs == tmp_path / ".butler" / "logs"
    assert p.attachments == tmp_path / "attachments"
    assert p.code == REPO


def test_an_explicit_data_root_that_does_not_exist_fails_fast(tmp_path):
    """AC-412: a typo must never boot an empty Butler somewhere else."""
    missing = tmp_path / "nope"
    with pytest.raises(paths.DataRootError) as err:
        paths.resolve(env={"BUTLER_DATA": str(missing)})
    assert str(missing) in str(err.value)
    assert not missing.exists(), "resolving must never create the data root"


def test_the_flag_wins_over_the_environment(tmp_path):
    """AC-412: flag > env > default."""
    flag, env_dir = tmp_path / "flag", tmp_path / "env"
    flag.mkdir()
    env_dir.mkdir()
    p = paths.resolve(data=str(flag), env={"BUTLER_DATA": str(env_dir)})
    assert p.data == flag


def test_butler_state_still_overrides_the_records_dir_alone(tmp_path):
    """AC-413: every scratch recipe and conftest set BUTLER_STATE; it keeps
    meaning "Butler's records live here" and nothing else moves with it."""
    state = tmp_path / "scratch-state"
    p = paths.resolve(env={"BUTLER_STATE": str(state)})
    assert p.records == state
    assert p.logs == state                   # the trace follows it, as before
    assert p.attachments == state / "attachments"
    assert p.data == REPO                    # the root project is unchanged
    assert p.ssl == REPO / "ssl"             # a scratch --tls keeps the cert


def test_butler_state_beside_a_data_root_moves_only_the_records(tmp_path):
    data, state = tmp_path / "data", tmp_path / "state"
    data.mkdir()
    p = paths.resolve(env={"BUTLER_DATA": str(data),
                           "BUTLER_STATE": str(state)})
    assert p.data == data
    assert p.records == state
    assert p.attachments == data / "attachments"
    assert p.ssl == data / ".butler" / "ssl"


def test_the_persona_files_live_in_the_data_root_or_the_records(tmp_path):
    """AC-420 (`docs/identity-memory.md` 11.1): SOUL.md and USER.md are the
    owner's files, so they sit in the data root beside AGENTS.md -- NOT in
    the hidden records folder. On the legacy layout the data root is the
    repository, so they go to the records folder instead of into git's
    working tree, and BUTLER_STATE (every scratch recipe) takes them along."""
    data, state = tmp_path / "data", tmp_path / "state"
    data.mkdir()
    assert paths.resolve(env={}).persona == REPO / "state"
    assert paths.resolve(env={"BUTLER_DATA": str(data)}).persona == data
    assert paths.resolve(env={"BUTLER_STATE": str(state)}).persona == state
    assert paths.resolve(env={"BUTLER_DATA": str(data),
                              "BUTLER_STATE": str(state)}).persona == data


def test_the_data_root_may_not_be_the_code_tree():
    """AC-412: the ruling's whole point -- an agent in the root project must
    not be working in Butler's own source."""
    with pytest.raises(paths.DataRootError):
        paths.resolve(env={"BUTLER_DATA": str(REPO)})
    with pytest.raises(paths.DataRootError):
        paths.resolve(env={"BUTLER_DATA": str(REPO / "TMP")})


def test_the_cli_prints_one_field_for_the_launcher(tmp_path, capsys):
    """start.bat asks this module rather than spelling the layout twice."""
    assert paths.main(["logs"], env={"BUTLER_DATA": str(tmp_path)}) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / ".butler" / "logs")
    assert paths.main(["logs"], env={"BUTLER_DATA": str(tmp_path / "x")}) == 2


def test_build_hub_reads_every_record_and_the_root_from_the_data_root(
        tmp_path, monkeypatch):
    """AC-415, AC-416, D-18: the resolver is only worth something if the
    DAEMON asks it. Asserted on what `build_hub` built, against a temp data
    root -- never the owner's."""
    from daemon import butlerd
    home = tmp_path / "Butler-Data"
    home.mkdir()
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.delenv("BUTLER_STATE", raising=False)
    monkeypatch.setenv("BUTLER_DATA", str(home))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")

    hub = butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                          "brain": "fake"}})

    assert hub.attachments.root == home / "attachments"
    root = hub.shortcuts.get(hub.shortcuts.root_id())
    assert root.path == str(home), "the root project is not the data root"
    assert (home / ".butler" / "shortcuts.json").is_file(), (
        "records were not written into <data>/.butler")

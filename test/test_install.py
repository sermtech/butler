"""scripts/install.py: the one-command installer and its --check mode.

Nothing here runs a real installer, writes the registry or touches a real
BUTLER_DATA: every subprocess goes through an injected `run` that records the
argv list, and every path lives under `tmp_path`.
"""
from __future__ import annotations

import importlib.util
import ntpath
import posixpath
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "install_script", REPO / "scripts" / "install.py")
inst = importlib.util.module_from_spec(_spec)
sys.modules["install_script"] = inst   # @dataclass looks the module up
_spec.loader.exec_module(inst)


# ---------------------------------------------------------------- resolution

def test_data_root_order_flag_then_env_then_registry_then_home(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    f, e, r = tmp_path / "f", tmp_path / "e", tmp_path / "r"
    resolve = inst.resolve_data_root
    assert resolve(str(f), str(e), str(r), home, repo) == f
    assert resolve(None, str(e), str(r), home, repo) == e
    assert resolve(None, None, str(r), home, repo) == r
    assert resolve(None, None, None, home, repo) == home / "Butler-Data"


def test_the_default_is_never_the_authors_E_drive(tmp_path):
    got = inst.resolve_data_root(None, None, None, tmp_path / "home",
                                 tmp_path / "repo")
    assert "E:" not in str(got) and "Butler-Data" in str(got)


@pytest.mark.parametrize("where", ["flag", "env", "registry"])
def test_a_data_root_inside_the_repo_is_refused(tmp_path, where):
    repo = tmp_path / "repo"
    bad = str(repo / "data")
    args = {"flag": None, "env": None, "registry": None}
    args[where] = bad
    with pytest.raises(inst.InstallError, match="inside the repo"):
        inst.resolve_data_root(args["flag"], args["env"], args["registry"],
                               tmp_path / "home", repo)


def test_the_repo_itself_is_refused_and_a_sibling_with_its_prefix_is_not(
        tmp_path):
    repo = tmp_path / "repo"
    with pytest.raises(inst.InstallError):
        inst.resolve_data_root(str(repo), None, None, tmp_path, repo)
    sibling = tmp_path / "repo-data"
    assert inst.resolve_data_root(str(sibling), None, None, tmp_path,
                                  repo) == sibling


# ------------------------------------------------------------------- persist

@pytest.mark.parametrize("os_name,current,wanted,flag,action", [
    ("nt", None, r"C:\Users\a\Butler-Data", False, "setx"),
    ("nt", r"C:\Users\a\Butler-Data", r"c:/users/a/butler-data", False, "ok"),
    ("nt", r"C:\old", r"C:\new", True, "refuse"),
    ("nt", r"C:\old", r"C:\new", False, "keep"),
    ("posix", None, "/home/a/mine", False, "manual"),
    ("posix", None, "/home/a/Butler-Data", False, "none_needed"),
    ("posix", "/home/a/mine", "/home/a/mine", False, "ok"),
    ("posix", "/home/a/old", "/home/a/new", True, "refuse"),
    ("posix", "/home/a/old", "/home/a/new", False, "keep"),
])
def test_persist_decision(os_name, current, wanted, flag, action):
    got = inst.persist_decision(current, wanted, flag, os_name,
                                posix_default="/home/a/Butler-Data")
    assert got.action == action


def test_refusal_names_the_existing_value_and_says_never_overwritten():
    got = inst.persist_decision("/x/old", "/x/new", True, "posix",
                                posix_default="/home/a/Butler-Data")
    assert "BUTLER_DATA is already /x/old" in got.message
    assert "refusing to change it" in got.message


# ------------------------------------------------------------- the runner

def test_runner_stops_at_the_first_failure(capsys):
    calls = []

    def step(name, status):
        def fn():
            calls.append(name)
            return inst.Result(status, "why")
        return name, fn

    code = inst.run_steps([step("a", "ok"), step("b", "FAILED"),
                           step("c", "ok")])
    out = capsys.readouterr().out
    assert code == 1 and calls == ["a", "b"]
    assert "[a] ok" in out and "[b] FAILED why" in out and "[c]" not in out


def test_runner_exits_0_when_every_step_passes(capsys):
    code = inst.run_steps([("a", lambda: inst.Result("ok", "")),
                           ("b", lambda: inst.Result("SKIP", "present"))])
    assert code == 0


# ---------------------------------------------------------------- fixtures

class Recorder:
    """Stands in for subprocess.run; records every argv LIST."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, argv, **kw):
        assert isinstance(argv, list), "argv must be a list, no shell"
        assert not kw.get("shell"), "no shell"
        self.calls.append(list(argv))
        rc = 1 if self.fail_on and self.fail_on in argv else 0
        return SimpleNamespace(returncode=rc, stdout="v20.11.0\n", stderr="")


TOOLS = {"uv": "/bin/uv", "node": "/bin/node", "npm": "/bin/npm"}


def make_ctx(tmp_path, os_name="posix", run=None, env=None, registry=None,
             data_flag=None, nemotron=False, which=None, check=False,
             version=(3, 13, 5)):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return inst.Context(
        repo=repo, os_name=os_name, run=run or Recorder(),
        which=(lambda n: TOOLS.get(n)) if which is None else which,
        env=env or {}, registry_value=registry, home=tmp_path / "home",
        data_flag=data_flag, nemotron=nemotron, local_port=8141,
        py_version=version)


# ------------------------------------------------------------ install order

def test_install_runs_the_steps_in_order(tmp_path):
    ctx = make_ctx(tmp_path, data_flag=str(tmp_path / "data"))
    assert inst.run_steps(inst.install_steps(ctx)) == 0
    py = str(ctx.repo / ".venv" / "bin" / "python")
    assert ctx.run.calls[0][0].endswith("node")      # prereq probe
    rest = [c for c in ctx.run.calls if c[0] != "/bin/node"]
    assert rest[0] == ["/bin/uv", "venv", ".venv", "--python", "3.13"]
    assert rest[1] == ["/bin/uv", "pip", "install", "--python", py,
                       "-r", "requirements.txt"]
    assert rest[2] == ["/bin/npm", "install"]
    assert rest[3] == [py, "-X", "utf8",
                       str(ctx.repo / "scripts" / "fetch-models.py")]
    assert rest[4][-1].endswith("fetch-fonts.py")
    assert rest[5][-1].endswith("fetch-xterm.py")


def test_nemotron_flag_reaches_fetch_models(tmp_path):
    ctx = make_ctx(tmp_path, nemotron=True, data_flag=str(tmp_path / "d"))
    inst.run_steps(inst.install_steps(ctx))
    models = [c for c in ctx.run.calls if c[-2:][0].endswith(
        "fetch-models.py") or c[-1] == "--nemotron"]
    assert models and models[0][-1] == "--nemotron"


def test_an_existing_venv_is_skipped(tmp_path):
    ctx = make_ctx(tmp_path, data_flag=str(tmp_path / "d"))
    py = inst.venv_python(ctx.repo, "posix")
    py.parent.mkdir(parents=True)
    py.write_text("")
    inst.run_steps(inst.install_steps(ctx))
    assert ["/bin/uv", "venv", ".venv", "--python", "3.13"] \
        not in ctx.run.calls


def test_a_missing_uv_stops_everything_after_prereqs(tmp_path, capsys):
    ctx = make_ctx(tmp_path, which=lambda n: None if n == "uv"
                   else TOOLS.get(n))
    code = inst.run_steps(inst.install_steps(ctx))
    out = capsys.readouterr().out
    assert code == 1 and "[prereqs] FAILED" in out and "uv" in out
    assert not [c for c in ctx.run.calls if "install" in c or "venv" in c]


def test_node_older_than_20_fails_prereqs(tmp_path, capsys):
    class Old(Recorder):
        def __call__(self, argv, **kw):
            super().__call__(argv, **kw)
            return SimpleNamespace(returncode=0, stdout="v18.19.0\n",
                                   stderr="")
    ctx = make_ctx(tmp_path, run=Old())
    assert inst.run_steps(inst.install_steps(ctx)) == 1
    assert "node" in capsys.readouterr().out


def test_a_failing_installer_command_stops_the_run(tmp_path, capsys):
    ctx = make_ctx(tmp_path, run=Recorder(fail_on="install"),
                   data_flag=str(tmp_path / "d"))
    code = inst.run_steps(inst.install_steps(ctx))
    assert code == 1
    assert not [c for c in ctx.run.calls
                if c[-1].endswith("fetch-models.py")]


# ---------------------------------------------------------- data root steps

def test_the_data_root_and_records_folder_are_created(tmp_path):
    data = tmp_path / "data"
    ctx = make_ctx(tmp_path, data_flag=str(data))
    inst.run_steps(inst.install_steps(ctx))
    assert (data / ".butler").is_dir()


def test_a_refused_persist_creates_nothing(tmp_path, capsys):
    data = tmp_path / "new"
    ctx = make_ctx(tmp_path, env={"BUTLER_DATA": str(tmp_path / "old")},
                   data_flag=str(data))
    code = inst.run_steps(inst.install_steps(ctx))
    assert code == 1 and not data.exists()
    assert "refusing to change it" in capsys.readouterr().out


def test_an_in_repo_data_root_fails_the_step(tmp_path, capsys):
    ctx = make_ctx(tmp_path)
    ctx.data_flag = str(ctx.repo / "d")
    assert inst.run_steps(inst.install_steps(ctx)) == 1
    assert "inside the repo" in capsys.readouterr().out
    assert not (ctx.repo / "d").exists()


# -------------------------------------------------------------------- setx

def persist(ctx, data):
    ctx.data = Path(data)
    return inst.persist_step(ctx)


def test_setx_runs_once_as_an_argv_list_when_unset_on_windows(tmp_path):
    ctx = make_ctx(tmp_path, os_name="nt")
    got = persist(ctx, tmp_path / "d")
    assert ctx.run.calls == [["setx", "BUTLER_DATA", str(tmp_path / "d")]]
    assert got.status == "done" and "NEW terminal" in got.detail


@pytest.mark.parametrize("env,registry", [
    ({"BUTLER_DATA": "SAME"}, None), ({}, "SAME")])
def test_setx_is_not_run_when_already_set_to_the_same_path(tmp_path, env,
                                                          registry):
    same = str(tmp_path / "d")
    env = {k: same for k in env}
    ctx = make_ctx(tmp_path, os_name="nt", env=env,
                   registry=same if registry else None)
    got = persist(ctx, same)
    assert ctx.run.calls == [] and got.status == "ok"


def test_setx_failure_is_a_failed_step(tmp_path):
    ctx = make_ctx(tmp_path, os_name="nt", run=Recorder(fail_on="setx"))
    assert persist(ctx, tmp_path / "d").status == "FAILED"


def test_posix_never_edits_a_profile_and_prints_the_export_line(tmp_path):
    ctx = make_ctx(tmp_path, os_name="posix")
    got = persist(ctx, tmp_path / "elsewhere")
    assert ctx.run.calls == []
    assert got.status == "done (manual)"
    assert f"export BUTLER_DATA={tmp_path / 'elsewhere'}" in got.detail


def test_posix_default_needs_nothing(tmp_path):
    ctx = make_ctx(tmp_path, os_name="posix")
    got = persist(ctx, tmp_path / "home" / "Butler-Data")
    assert got.status == "ok" and ctx.run.calls == []


# ---------------------------------------------------------------- the end

def test_start_message_per_platform_names_the_launcher_and_url():
    nt = inst.start_message("nt", 8141)
    posix = inst.start_message("posix", 8141)
    assert "start.bat" in nt and "http://127.0.0.1:8141" in nt
    assert ".venv/bin/python -X utf8 scripts/launch.py" in posix
    assert "certificate" in nt and "certificate" in posix


# ------------------------------------------------------------------ --check

def populate(ctx, data):
    """A fully installed fake repo."""
    py = inst.venv_python(ctx.repo, ctx.os_name)
    py.parent.mkdir(parents=True)
    py.write_text("")
    (ctx.repo / "node_modules").mkdir()
    for rel in inst.expected_files(ctx.repo, nemotron=ctx.nemotron):
        rel.parent.mkdir(parents=True, exist_ok=True)
        rel.write_text("x")
    (data / ".butler").mkdir(parents=True)


def test_check_makes_no_installing_call_and_changes_nothing(tmp_path):
    data = tmp_path / "data"
    ctx = make_ctx(tmp_path, data_flag=str(data))
    inst.run_check(ctx)
    assert not data.exists()
    assert not (ctx.repo / ".venv").exists()
    allowed = ({"--version"}, {"ls", "--depth=0"}, {"-c"})
    for argv in ctx.run.calls:
        tail = set(argv[1:])
        assert any(a <= tail for a in allowed), argv
        for word in ("install", "venv", "pip", "setx"):
            assert word not in argv
        assert not any(a.endswith(".py") and "fetch" in a for a in argv)


def test_check_is_all_ok_on_a_complete_install(tmp_path, capsys):
    data = tmp_path / "data"
    ctx = make_ctx(tmp_path, data_flag=str(data),
                   env={"BUTLER_DATA": str(data)})
    populate(ctx, data)
    code = inst.run_check(ctx)
    out = capsys.readouterr().out.strip().splitlines()
    m = out[-1].split(" of ")
    assert code == 0 and m[0] == m[1].split()[0]
    assert out[-1].endswith("ok")


def test_check_fails_and_names_a_missing_model(tmp_path, capsys):
    data = tmp_path / "data"
    ctx = make_ctx(tmp_path, data_flag=str(data),
                   env={"BUTLER_DATA": str(data)})
    populate(ctx, data)
    next(iter(inst.expected_files(ctx.repo, nemotron=False))).unlink()
    code = inst.run_check(ctx)
    out = capsys.readouterr().out
    assert code == 1 and "MISSING" in out


def test_check_reports_an_unpersisted_data_root_as_not_ok(tmp_path, capsys):
    data = tmp_path / "data"
    ctx = make_ctx(tmp_path, os_name="nt", data_flag=str(data))
    populate(ctx, data)
    assert inst.run_check(ctx) == 1
    assert "BUTLER_DATA" in capsys.readouterr().out


def test_nemotron_file_is_only_expected_when_asked(tmp_path):
    names = lambda n: [p.name for p in inst.expected_files(tmp_path, n)]
    assert not any("nemotron" in n for n in names(False))
    assert any("nemotron" in n for n in names(True))


def test_ntpath_and_posixpath_are_what_comparison_uses():
    # pins the comparison to the TARGET platform's rules, not the host's
    assert inst.same_path(r"C:\A\b", "c:/a/B", "nt")
    assert not inst.same_path("/a/B", "/a/b", "posix")
    assert ntpath and posixpath


def test_records_at_the_launchers_fallback_win_over_the_home_default(
        tmp_path):
    """The author's desk has no BUTLER_DATA: launch.py finds the live records
    through its Windows fallback (E:\Butler-Data). An install there without
    --data must ADOPT that folder; the home default would start Butler on an
    empty records folder and save BUTLER_DATA pointing at it."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    legacy = tmp_path / "legacy"
    (legacy / ".butler").mkdir(parents=True)
    got = inst.resolve_data_root(None, None, None, home, repo,
                                 legacy_default=legacy)
    assert got == legacy


def test_an_empty_fallback_folder_does_not_win(tmp_path):
    """Only RECORDS decide: a fallback folder without .butler is nobody's."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    got = inst.resolve_data_root(None, None, None, home, repo,
                                 legacy_default=legacy)
    assert got == home / "Butler-Data"

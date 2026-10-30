"""The file explorer's two ROUTES. `docs/file-explorer.md` 3.1, phase 2,
AC-304, AC-308, AC-310.

`test_files.py` proves the rules in `daemon/files.py`; this file proves the
wiring: each refusal reaches the wire with its status and its sentence, the
answer is JSON and never the file's own bytes, the disk work goes OFF the
event loop (D-25, asserted on the CALLER, D-18), every preview leaves one log
line, and no request parameter can choose the root.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import QuietTts                                    # noqa: E402
from daemon import butlerd, files, hub as hub_mod, ports         # noqa: E402
from daemon import shortcuts as shortcuts_mod                    # noqa: E402
from daemon.adapters.asr_fake import FakeAsr                     # noqa: E402
from daemon.adapters.brain_fake import FakeBrain                 # noqa: E402
from daemon.modes import ModeStore                               # noqa: E402
from daemon.settings import Settings                             # noqa: E402


@pytest.fixture
def served(tmp_path, monkeypatch):
    """The real app over a project row on a fixture tree, never a real repo."""
    root = tmp_path / "Repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_bytes(b"print('hi')\n")
    (root / ".git").mkdir()
    (root / ".env").write_bytes(b"TOKEN=x\n")
    (root / "page.html").write_bytes(b"<script>alert(1)</script>\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stolen.txt").write_bytes(b"not yours\n")
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "escape"),
                           str(outside)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    store = shortcuts_mod.ShortcutStore(tmp_path / "shortcuts.json")
    sid = store.upsert("", str(root), "Repo").id
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=FakeBrain(),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=Settings(tmp_path / "settings.json"),
                      shortcuts=store)
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    return store, sid, root, outside


async def call(method: str, path: str, params=None):
    """`(status, payload, content_type)`, the body read before the close."""
    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    try:
        answer = await client.request(method, path, params=params or {})
        kind = answer.content_type
        payload = await answer.json() if kind == "application/json" else \
            await answer.text()
        return answer.status, payload, kind
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_listing_is_json_and_one_level(served):
    _, sid, _, _ = served

    status, body, kind = await call("GET", f"/api/project/{sid}/files")

    assert status == 200 and kind == "application/json", body
    assert [e["name"] for e in body["entries"]] == \
        ["escape", "src", ".env", "page.html"]
    assert body["skipped"] == [".git"]


@pytest.mark.asyncio
async def test_a_preview_is_JSON_even_for_an_html_file(served):
    """AC-308: never the file under its own media type, or an `.html` in a
    project runs script in Butler's origin."""
    _, sid, _, _ = served

    status, body, kind = await call("GET", f"/api/project/{sid}/file",
                                    {"path": "page.html"})

    assert status == 200 and kind == "application/json", body
    assert body["text"] == "<script>alert(1)</script>\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["files", "file"])
async def test_an_unknown_row_is_a_404_naming_it(served, route):
    status, body, _ = await call("GET", f"/api/project/s-nope/{route}",
                                 {"path": "a"})
    assert status == 404 and "s-nope" in body["error"], body


@pytest.mark.asyncio
async def test_a_row_whose_directory_is_gone_is_a_409(served, tmp_path):
    store, _, _, _ = served
    sid = store.upsert("", str(tmp_path / "gone"), "Gone").id

    status, body, _ = await call("GET", f"/api/project/{sid}/files")
    assert status == 409 and "gone" in body["error"], body


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["files", "file"])
@pytest.mark.parametrize("rel, status, words", [
    ("../outside", 400, ".."),
    ("C:/Windows", 400, "drive"),
    ("a.txt:stream", 400, "':'"),
    ("escape", 403, "outside"),
    (".git", 404, "hidden"),
])
async def test_each_refusal_reaches_the_wire_NAMING_ITS_RULE(
        served, route, rel, status, words):
    _, sid, _, _ = served

    got, body, kind = await call("GET", f"/api/project/{sid}/{route}",
                                 {"path": rel})

    assert got == status and kind == "application/json", body
    assert words in body["error"], body


@pytest.mark.asyncio
async def test_a_secret_preview_is_a_403_and_a_directory_preview_a_400(served):
    _, sid, _, _ = served

    status, body, _ = await call("GET", f"/api/project/{sid}/file",
                                 {"path": ".env"})
    assert status == 403 and "secret" in body["error"], body
    status, body, _ = await call("GET", f"/api/project/{sid}/file",
                                 {"path": "src"})
    assert status == 400 and "directory" in body["error"], body


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["files", "file"])
async def test_no_request_parameter_can_choose_the_ROOT(served, route):
    """AC-304. A parameter other than `path` is refused by name rather than
    ignored: a caller who sent `root` believes it was honoured."""
    _, sid, _, outside = served

    status, body, _ = await call("GET", f"/api/project/{sid}/{route}",
                                 {"root": str(outside), "path": "stolen.txt"})

    assert status == 400 and "root" in body["error"], body


@pytest.mark.asyncio
async def test_every_preview_is_LOGGED(served, capsys):
    _, sid, _, _ = served

    status, _, _ = await call("GET", f"/api/project/{sid}/file",
                              {"path": "src/main.py"})

    assert status == 200
    assert f"file preview {sid} src/main.py 12" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("route", ["files", "file"])
async def test_the_explorer_WRITES_nothing(served, method, route):
    """AC-310: two GETs and nothing else on either path. The GET is asserted
    too, because the app's catch-all answers 405 for a path with NO route --
    without it this passes before the explorer exists."""
    _, sid, _, _ = served
    rel = "src/main.py" if route == "file" else "src"

    status, _, _ = await call("GET", f"/api/project/{sid}/{route}",
                              {"path": rel})
    assert status == 200
    status, _, _ = await call(method, f"/api/project/{sid}/{route}",
                              {"path": rel})
    assert status == 405


@pytest.mark.asyncio
async def test_the_disk_work_goes_OFF_the_event_loop(served, monkeypatch):
    """D-25, asserted on the CALLER (D-18): both routes hand `daemon.files`
    to `asyncio.to_thread` rather than calling it on the loop."""
    _, sid, _, _ = served
    seen = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        seen.append(fn)
        return await real(fn, *a, **k)

    monkeypatch.setattr(butlerd.asyncio, "to_thread", spy)

    await call("GET", f"/api/project/{sid}/files")
    await call("GET", f"/api/project/{sid}/file", {"path": "src/main.py"})

    assert files.list_level in seen and files.preview in seen, seen


@pytest.mark.asyncio
async def test_a_daemon_with_no_store_answers_404(tmp_path, monkeypatch):
    hub = hub_mod.Hub(asr=FakeAsr(), tts=QuietTts(), brain=FakeBrain(),
                      modes=ModeStore(tmp_path / "mode.json"),
                      asr_config=ports.AsrConfig(model="test"),
                      settings=Settings(tmp_path / "settings.json"))
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    status, body, _ = await call("GET", "/api/project/s-1/files")
    assert status == 404 and "shortcuts" in body["error"], body

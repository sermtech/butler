"""Recipes and tasks: the routes. docs/automations.md section 5, phase 1.

Every refusal is asserted by its CONSEQUENCE as well as its status -- the
list unchanged, nothing on disk -- because a route that answered 400 and
wrote anyway passes every status check (test/AGENTS.md, test_shortcuts'
rule). AC-448's D-18 half: the expand route goes through `library.expand`,
proved with a spy, and no other file in daemon/ or web/ carries a
placeholder substitution.
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import butlerd                         # noqa: E402
from daemon import library as lib                  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _hub(tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text('{"providers": {}}',
                                            encoding="utf-8")
    monkeypatch.setenv("BUTLER_STATE", str(tmp_path))
    monkeypatch.setattr(butlerd, "PI_REGISTRY", tmp_path / "registry.json")
    monkeypatch.setenv("BUTLER_ENDPOINTER", "none")
    monkeypatch.delenv("BUTLER_SELF_URL", raising=False)
    return butlerd.build_hub({"adapters": {"asr": "fake", "tts": "fake",
                                           "brain": "fake"}})


@pytest_asyncio.fixture
async def served(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    site = tmp_path / "site"
    site.mkdir()
    hub.shortcuts.upsert(sid="", path=str(site), label="SITE")
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)
    frames = []

    async def spy(**payload):
        frames.append(payload)
    monkeypatch.setattr(hub, "broadcast", spy)
    client = TestClient(TestServer(butlerd.make_app({})))
    await client.start_server()
    rows = {r.label: r.id for r in hub.shortcuts.all()}
    try:
        yield hub, client, rows["SITE"], hub.shortcuts.root_id(), frames
    finally:
        await client.close()


async def _json(r):
    return r.status, await r.json()


@pytest.mark.asyncio
async def test_build_hub_puts_the_library_under_the_records(tmp_path,
                                                            monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    assert hub.library.root == tmp_path / "library"


@pytest.mark.asyncio
async def test_a_recipe_round_trips_and_the_panel_serves_it(served):
    hub, client, site, root, frames = served
    status, r = await _json(await client.post("/api/recipes", json={
        "name": "Weekly review", "project": site,
        "prompt": "Review since {since=last Monday}."}))
    assert status == 200, r
    assert r["placeholders"] == [{"name": "since", "default": "last Monday"}]
    assert frames[-1] == {"type": "library", "what": "recipes",
                          "project": site}
    status, panel = await _json(await client.get(
        f"/api/library?project={site}"))
    assert status == 200
    assert [x["id"] for x in panel["recipes"]["project"]] == [r["id"]]
    status, listed = await _json(await client.get("/api/recipes"))
    assert [x["id"] for x in listed["recipes"]] == [r["id"]]
    # Update in place.
    status, r2 = await _json(await client.post("/api/recipes", json={
        "id": r["id"], "name": "Weekly review", "project": lib.GLOBAL,
        "prompt": "Review."}))
    assert status == 200 and r2["id"] == r["id"] and r2["project"] == "global"
    assert frames[-1]["project"] == ""


@pytest.mark.asyncio
async def test_a_refused_recipe_is_a_400_naming_the_rule_and_writes_nothing(
        served):
    hub, client, site, root, frames = served
    for body, why in [({"name": "", "project": site, "prompt": "p"},
                       "needs a name"),
                      ({"name": "n", "project": "E:\\x", "prompt": "p"},
                       "not a project row"),
                      ({"name": "n", "project": site, "prompt": "a { b"},
                       "character"),
                      ({"name": "n", "project": site}, "all text")]:
        status, doc = await _json(await client.post("/api/recipes",
                                                     json=body))
        assert status == 400 and why in doc["error"], (body, doc)
    assert hub.library.recipes.recipes() == [] and frames == []
    status, doc = await _json(await client.post("/api/recipes",
                                                data=b"not json"))
    assert status == 400


@pytest.mark.asyncio
async def test_unknown_ids_are_404s_naming_them(served):
    hub, client, site, root, frames = served
    status, doc = await _json(await client.post("/api/recipes", json={
        "id": "rcp-deadbeef", "name": "n", "project": site,
        "prompt": "p"}))
    assert status == 404 and "rcp-deadbeef" in doc["error"]
    status, doc = await _json(await client.delete("/api/recipes/rcp-nope"))
    assert status == 404 and "rcp-nope" in doc["error"]
    status, doc = await _json(await client.post(
        "/api/recipes/rcp-nope/expand", json={"values": {}}))
    assert status == 404
    status, doc = await _json(await client.delete("/api/tasks/tsk-nope"))
    assert status == 404 and "tsk-nope" in doc["error"]
    status, doc = await _json(await client.get("/api/library?project=s-x"))
    assert status == 404 and "s-x" in doc["error"]


@pytest.mark.asyncio
async def test_expand_goes_through_THE_ONE_EXPANSION(served, monkeypatch):
    """AC-448, D-18: the route's text is `library.expand`'s, and a missing
    value is a 400 naming it."""
    hub, client, site, root, frames = served
    r = hub.library.recipes.save(None, "Bulletin", site,
                                 "Bulletin for {date}, {tone=warm}.")
    calls = []
    real = lib.expand

    def spy(prompt, values=None):
        calls.append((prompt, values))
        return real(prompt, values)
    monkeypatch.setattr(lib, "expand", spy)
    status, doc = await _json(await client.post(
        f"/api/recipes/{r.id}/expand", json={"values": {"date": "Sun"}}))
    assert status == 200 and doc["text"] == "Bulletin for Sun, warm."
    assert calls == [(r.prompt, {"date": "Sun"})]
    status, doc = await _json(await client.post(
        f"/api/recipes/{r.id}/expand", json={"values": {}}))
    assert status == 400 and "needs a value for date" in doc["error"]


def test_no_second_substitution_anywhere():
    """AC-448: the placeholder grammar is spelled in ONE file. The page asks
    the expand route; nothing else in daemon/ or web/ parses `{name=...}`."""
    marker = re.compile(r"a-z0-9_\]\{1,32\}")
    hits = [p.relative_to(REPO).as_posix()
            for folder in ("daemon", "web")
            for p in (REPO / folder).rglob("*")
            if p.suffix in (".py", ".js", ".ts", ".html")
            and "mocks" not in p.parts
            and marker.search(p.read_text(encoding="utf-8",
                                          errors="replace"))]
    assert hits == ["daemon/library.py"], hits
    js = (REPO / "web" / "library.js")
    if js.exists():
        text = js.read_text(encoding="utf-8")
        assert "/expand" in text, "the page must ask the daemon to expand"


@pytest.mark.asyncio
async def test_delete_a_recipe(served):
    hub, client, site, root, frames = served
    r = hub.library.recipes.save(None, "Gone", site, "p")
    status, doc = await _json(await client.delete(f"/api/recipes/{r.id}"))
    assert status == 200 and doc["id"] == r.id
    assert hub.library.recipes.recipes() == []
    assert frames[-1] == {"type": "library", "what": "recipes",
                          "project": site}


@pytest.mark.asyncio
async def test_tasks_add_edit_done_delete_over_the_wire(served):
    hub, client, site, root, frames = served
    status, t = await _json(await client.post("/api/tasks", json={
        "project": site, "text": "Call Dave"}))
    assert status == 200 and t["source"] == "owner", t
    assert frames[-1] == {"type": "library", "what": "tasks",
                          "project": site}
    status, t2 = await _json(await client.post("/api/tasks", json={
        "id": t["id"], "done": True}))
    assert status == 200 and t2["done"] is True
    status, t3 = await _json(await client.post("/api/tasks", json={
        "id": t["id"], "text": "Call Dave Tuesday", "done": False}))
    assert (t3["text"], t3["done"]) == ("Call Dave Tuesday", False)
    status, listed = await _json(await client.get(
        f"/api/tasks?project={site}"))
    assert [x["id"] for x in listed["tasks"]] == [t["id"]]
    status, doc = await _json(await client.delete(f"/api/tasks/{t['id']}"))
    assert status == 200 and hub.library.tasks.tasks() == []


@pytest.mark.asyncio
async def test_a_root_task_broadcasts_as_global(served):
    hub, client, site, root, frames = served
    await client.post("/api/tasks", json={"project": root, "text": "x"})
    assert frames[-1]["project"] == ""


@pytest.mark.asyncio
async def test_a_refused_task_writes_nothing(served):
    hub, client, site, root, frames = served
    for body, why in [({"project": site, "text": ""}, "needs its text"),
                      ({"project": "global", "text": "x"},
                       "not a project row"),
                      ({"project": site, "text": "x", "source": "butler"},
                       "source")]:
        status, doc = await _json(await client.post("/api/tasks", json=body))
        assert status == 400 and why in doc["error"], (body, doc)
    assert hub.library.tasks.tasks() == [] and frames == []


@pytest.mark.asyncio
async def test_the_routes_do_their_disk_work_off_the_loop(served,
                                                          monkeypatch):
    """D-1: every library route reaches the store through to_thread."""
    hub, client, site, root, frames = served
    hopped = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        hopped.append(getattr(fn, "__name__", repr(fn)))
        return await real(fn, *a, **kw)
    monkeypatch.setattr(butlerd.asyncio, "to_thread", spy)
    await client.get(f"/api/library?project={site}")
    await client.post("/api/tasks", json={"project": site, "text": "x"})
    await client.post("/api/recipes", json={"name": "n", "project": site,
                                            "prompt": "p"})
    assert len(hopped) >= 3, hopped

"""What the library routes SERVE for the simpler page (docs/lantern-ui.md
section 8, Step 6): a task's `agent` and a recipe's `usedBy`.

What each test would catch:

* `agent` is the harness the DAEMON knows the calling conversation ran as
  (its record's `harness`), never a name the caller sent; an owner task and
  a task from the endpoint brain carry "" -- asserted on the served routes
  (`GET /api/tasks`, `GET /api/library`), not on the store alone (D-18).
* `usedBy` names exactly the automations that run THAT recipe (the wanted
  recipe sits between two others, one used by another automation and one
  used by none), on every route that serves a recipe, and follows a delete.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import TestClient, TestServer         # noqa: E402

from test_automations import _project                         # noqa: E402
from test_library_voice import _harness_hub                   # noqa: E402
from daemon import butlerd                                    # noqa: E402
from daemon import library as lib                             # noqa: E402


async def _client(hub, monkeypatch):
    monkeypatch.setattr(butlerd, "build_hub", lambda cfg: hub)

    async def quiet(**_payload):
        pass
    monkeypatch.setattr(hub, "broadcast", quiet)
    client = TestClient(TestServer(butlerd.make_app({})),
                        headers={butlerd.hub_mod.LOCAL_HEADER: "1"})
    await client.start_server()
    return client


def _ran_as(hub, chat_id: str, harness: str) -> None:
    """What a real spawn does to the record (`chats.session_ref`, called by
    the spawn resolver): the conversation now runs as `harness`."""
    chats = hub.brain.host._chats
    chats.session_ref(chats._open[chat_id], harness)


@pytest.mark.asyncio
async def test_an_agent_task_names_the_harness_the_daemon_knows(
        tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    born = await hub.new_conversation(row)
    _ran_as(hub, born["id"], "pi")
    root = hub.shortcuts.root_id()
    client = await _client(hub, monkeypatch)
    try:
        # The caller claims to be claude; the record says pi. pi wins.
        r = await client.post("/api/tasks/agent", json={
            "text": "Order candles", "chat": born["id"], "agent": "claude"})
        assert r.status == 200, await r.text()
        r = await client.post("/api/tasks", json={"project": root,
                                                  "text": "Renew passport"})
        assert r.status == 200, await r.text()
        # The endpoint brain's door: the hub's dispatcher, no harness.
        text, _task = hub.library.call_tool(
            lib.TASK_ADD, {"text": "Call the bank"}, project=root,
            chat="chat-endpoint", turn_key=1)
        assert text.startswith("Added task"), text

        served = (await (await client.get("/api/tasks")).json())["tasks"]
        by_text = {t["text"]: t for t in served}
        assert by_text["Order candles"]["agent"] == "pi"
        assert by_text["Renew passport"]["agent"] == ""
        assert by_text["Call the bank"]["agent"] == ""
        panel = await (await client.get(
            f"/api/library?project={root}")).json()
        listed = {t["text"]: t["agent"]
                  for t in panel["tasks"]["global"]["open"]}
        assert listed == {"Order candles": "pi", "Renew passport": "",
                          "Call the bank": ""}
    finally:
        await client.close()


def test_the_store_keeps_agent_only_on_a_butler_task(tmp_path):
    L = lib.Library(tmp_path / "library", rows=lambda: {"s-root"},
                    root_id=lambda: "s-root")
    t = L.tasks.add("s-root", "x", source="butler", chat="c1", agent="pi")
    assert t["agent"] == "pi"
    with pytest.raises(lib.LibraryRefused, match="agent"):
        L.tasks.add("s-root", "y", agent="pi")       # an owner task
    assert [x["text"] for x in L.tasks.tasks()] == ["x"]


@pytest.mark.asyncio
async def test_a_recipe_serves_the_automations_that_use_it(
        tmp_path, monkeypatch):
    hub = _harness_hub(tmp_path, monkeypatch)
    row = _project(hub, tmp_path)
    other = hub.library.recipes.save(None, "Other", row, "Other work.")
    wanted = hub.library.recipes.save(None, "Weekly review", row,
                                      "Review the week.")
    unused = hub.library.recipes.save(None, "Unused", row, "Nothing.")
    now = hub.scheduler.clock()
    day = {"preset": "daily", "time": "09:00"}
    a1 = hub.automations.save({"recipe": wanted.id, "schedule": day}, now)
    hub.automations.save({"recipe": other.id, "schedule": day}, now)
    a2 = hub.automations.save({"recipe": wanted.id, "schedule": day}, now)
    client = await _client(hub, monkeypatch)
    try:
        def used(rows):
            return {r["name"]: r["usedBy"] for r in rows}
        panel = await (await client.get(f"/api/library?project={row}")).json()
        got = used(panel["recipes"]["project"])
        assert got["Weekly review"] == [a1["id"], a2["id"]]
        assert len(got["Other"]) == 1 and got["Unused"] == []
        listed = used((await (await client.get("/api/recipes")).json())
                      ["recipes"])
        assert listed == got
        r = await client.post("/api/recipes", json={
            "id": wanted.id, "name": "Weekly review", "project": row,
            "prompt": "Review the week again."})
        assert r.status == 200 and (await r.json())["usedBy"] == \
            [a1["id"], a2["id"]]
        # Served fresh: a deleted automation leaves the list at once.
        r = await client.delete(f"/api/automations/{a1['id']}")
        assert r.status == 200
        panel = await (await client.get(f"/api/library?project={row}")).json()
        assert used(panel["recipes"]["project"])["Weekly review"] == \
            [a2["id"]]
        assert unused.id not in {a["recipe"]
                                 for a in hub.automations.automations()}
    finally:
        await client.close()

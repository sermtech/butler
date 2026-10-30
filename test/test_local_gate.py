"""Review fixes RV-1..RV-3 (docs/messaging.md 6.5): the local gate, the
records rule for a tool's file, the owner's confirmation, and wa.py's
timeout.

AC-586 (RV-1, F-1): every `is_local` route ALSO needs `X-Butler-Local: 1`,
and an `Origin`, when present, must be Butler's own. A web page in the
owner's browser could POST to the LOCAL listener (a CORS "simple request"
needs no preflight) and send WhatsApp messages from the owner's number.
AC-587 (RV-2, F-2): a tool's file may never come from the records folder
(the WhatsApp session, the TLS key, notify and trigger secrets), junctions
included. AC-588 (RV-2): a tool never sets `confirm`; a held first send is
asked of the OWNER through the question door, and only their yes sends it.
AC-589 (RV-3, F-14): wa.py's wait scales with the file, and a timeout exits
3 "unknown whether it was sent", never a traceback.

Real sockets (`test_listeners._Three`) and the FAKE sidecar; every refusal
is proved by its CONSEQUENCE (no /send reached the sidecar, no note, no
task, no shutdown). Nothing here touches WhatsApp.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aiohttp
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))
sys.path.insert(0, str(REPO))

from assets import wa_archive                                 # noqa: E402
from conftest import attach_session                           # noqa: E402
from daemon import butlerd                                    # noqa: E402
from daemon import hub as hub_mod                             # noqa: E402
from daemon import whatsapp as wa                             # noqa: E402
from daemon.adapters.harness_pi import MEMORY_EXTENSION       # noqa: E402
from test_whatsapp_owner import _until                        # noqa: E402
from test_whatsapp_send import (_Door, _form, _junction,      # noqa: E402
                                _sends, fake_argv)            # noqa: F401

LOCAL = {hub_mod.LOCAL_HEADER: "1"}
EVIL = {"Origin": "https://evil.example"}

# Every route that calls `hub.is_local`, with a body that WOULD act if the
# gate let it through. `test_every_local_route_is_in_this_table` holds the
# list to the source, so a new local route must be added here.
ROUTES = (
    ("POST", "/api/whatsapp/send", {"recipient": "me", "text": "hi"}),
    ("POST", "/api/whatsapp/read/whatsapp_list_chats", {}),
    ("GET", "/api/whatsapp/status", None),
    ("POST", "/api/memory/search", {"query": "fact"}),
    ("POST", "/api/memory/write", {"title": "t", "text": "a fact"}),
    ("POST", "/api/tasks/agent", {"text": "x", "chat": "c"}),
    ("POST", "/api/library/agent", {"tool": "reminder_add", "chat": "c",
                                    "args": {}}),
    ("POST", "/api/artifacts/show", {"tool": "show_ui", "chat": "c",
                                     "args": {"title": "t", "blocks": [
                                         {"type": "List", "items": ["x"]}]}}),
    ("POST", "/api/guide/agent", {}),
    ("POST", "/api/shutdown", {}),
    ("POST", "/api/phonelink", {}),
)
# The handlers that call `is_local`, by (file, function): the source half.
GATED = {("butlerd.py", "post_shutdown"), ("butlerd.py", "memory_route"),
         ("butlerd.py", "post_task_agent"), ("butlerd.py", "post_library_agent"),
         ("butlerd.py", "post_artifact_show"), ("butlerd.py", "post_guide_agent"),
         ("butlerd.py", "read"), ("butlerd.py", "send"),
         ("butlerd.py", "status"), ("phonelink.py", "get"),
         ("phonelink.py", "post")}


async def _call(http, url, method, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    async with http.request(method, url, **kwargs) as r:
        text = await r.text()
        return r.status, text


def _no_consequence(door):
    assert _sends(door.records) == []
    assert door.hub.memory.notes() == []
    assert door.hub.library.tasks.tasks() == []
    assert not door.three.app[butlerd.SHUTDOWN].is_set()
    assert door.three.cleanups == []


# --------------------------------------------------------------------------- #
# AC-586 (RV-1): a cross-site caller is refused on every local route
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_cross_site_request_is_refused_on_every_local_route(
        tmp_path, monkeypatch, fake_argv):
    """AC-586. On the LOCAL listener -- the one that trusts every peer --
    a request with a foreign Origin and no header, the same WITH the header,
    and one with the header but no Origin from Butler's port... the first
    two are refused 403 and NOTHING happens."""
    async with _Door(tmp_path, monkeypatch) as door:
        async with aiohttp.ClientSession() as bare:       # no default header
            for method, path, body in ROUTES:
                url = door.three.url(hub_mod.LOCAL, path)
                for headers in (EVIL, {**EVIL, **LOCAL}, {},
                                {**LOCAL, "Origin": "null"},
                                {**LOCAL, "Origin": "http://127.0.0.1:1"}):
                    status, text = await _call(bare, url, method, body,
                                               headers)
                    assert status == 403, (path, headers, text)
            await asyncio.sleep(0.3)                # past the shutdown delay
            _no_consequence(door)


@pytest.mark.asyncio
async def test_butlers_own_callers_pass_the_gate(tmp_path, monkeypatch,
                                                 fake_argv):
    """AC-586's other half: the header from a local tool with no Origin
    (pi extension, wa.py, the app), and with Butler's OWN origin (the
    settings page, same-origin), is let through and acts."""
    async with _Door(tmp_path, monkeypatch) as door:
        local_port = door.three.socks[hub_mod.LOCAL].getsockname()[1]
        main_port = door.three.socks[hub_mod.MAIN].getsockname()[1]
        async with aiohttp.ClientSession() as bare:
            url = door.three.url(hub_mod.LOCAL, "/api/memory/write")
            for i, origin in enumerate((None,
                                        f"http://127.0.0.1:{local_port}",
                                        f"https://localhost:{main_port}")):
                headers = dict(LOCAL)
                if origin:
                    headers["Origin"] = origin
                status, text = await _call(
                    bare, url, "POST", {"title": f"t{i}", "text": f"fact {i}"},
                    headers)
                assert status == 200, (origin, text)
            status, text = await _call(
                bare, door.three.url(hub_mod.LOCAL, "/api/whatsapp/send"),
                "POST", {"recipient": "me", "text": "hi"}, LOCAL)
            assert status == 200, text
        assert len(door.hub.memory.notes()) == 3
        assert len(_sends(door.records)) == 1


def test_the_origin_set_is_built_from_the_listening_ports():
    """Butler's own origins: each listener port under each loopback name,
    http and https -- and nothing on a port Butler does not listen on."""
    class _App(dict):
        pass

    class _Req:
        app = _App({hub_mod.LISTENERS: {8140: hub_mod.MAIN,
                                        8141: hub_mod.LOCAL}})
    got = hub_mod.own_origins(_Req())
    assert "http://127.0.0.1:8141" in got and "https://localhost:8140" in got
    assert "http://[::1]:8141" in got
    assert not any(o.endswith(":8142") or "evil" in o for o in got)


def _calls(tree: ast.AST, name: str):
    """(enclosing function, line) of every call to `name` / `x.name`."""
    found = []

    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            here = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
            if isinstance(child, ast.Call):
                f = child.func
                if (isinstance(f, ast.Name) and f.id == name) or (
                        isinstance(f, ast.Attribute) and f.attr == name):
                    found.append(here)
            walk(child, here)
    walk(tree, "<module>")
    return found


def test_every_local_route_is_in_this_table_and_none_bypasses_the_gate():
    """AC-586's source test. The ONE gate is `hub.is_local`: no route
    decides locality any other way (`_is_local` and `listener_of` are
    called only inside hub.py, the header and Origin are read only in
    `is_local`), and every handler that calls `is_local` is one the dynamic
    test above drives."""
    # The one gate (docs/remote-access.md RA-1) reads the listener and the
    # trust core by design; it never reads the local header.
    GATE_READS = {("access.py", "decide"), ("access.py", "_peer"),
                  ("access.py", "pair_url")}
    gated, core, header = set(), [], []
    for path in sorted((REPO / "daemon").rglob("*.py")):
        rel = path.relative_to(REPO / "daemon").as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for where in _calls(tree, "is_local"):
            gated.add((rel, where))
        for fn in ("_is_local", "listener_of"):
            core += [(rel, w) for w in _calls(tree, fn)
                     if rel != "hub.py" and (rel, w) not in GATE_READS]
        if rel != "hub.py" and ("X-Butler-Local" in text
                                or "LOCAL_HEADER" in text):
            header.append(rel)
    assert gated == GATED, gated
    assert core == [], core
    assert header == [], header


# --------------------------------------------------------------------------- #
# AC-587 (RV-2): never a file from the records folder
# --------------------------------------------------------------------------- #
@pytest.fixture
def data_root(tmp_path_factory, monkeypatch):
    """The migrated layout: records at <data>/.butler. A sibling of the
    test's own tmp_path (which `_hub` makes BUTLER_STATE)."""
    root = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("BUTLER_DATA", str(root))
    secrets = {
        "whatsapp/session/creds.json": b'{"noiseKey": "x"}',
        "ssl/key.pem": b"-----BEGIN PRIVATE KEY-----",
        "notify/phone.json": b'{"token": "123:abc"}',
        "triggers/mail.json": b'{"password": "hunter2"}',
    }
    for rel, data in secrets.items():
        f = root / ".butler" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    (root / "report.txt").write_text("fine", encoding="utf-8")
    return root, secrets


@pytest.mark.asyncio
async def test_a_tools_file_from_the_records_is_refused_at_the_door(
        tmp_path, monkeypatch, fake_argv, data_root):
    """AC-587 through the loopback door (the pi extension's path): each
    secret, by its real path and through a JUNCTION inside the data root
    that leads into the records, is refused naming the rule; nothing
    reaches the sidecar. A file beside them in the data root goes."""
    root, secrets = data_root
    link = root / "innocent"
    _junction(link, root / ".butler")
    try:
        async with _Door(tmp_path, monkeypatch) as door:
            for rel, data in secrets.items():
                for source in (root / ".butler" / rel, link / rel):
                    status, body = await door.post(data=_form(
                        {"recipient": "me", "source": str(source)},
                        [(Path(rel).name, data)]))
                    assert status == 403, (source, body)
                    assert body["reason"] == "file_in_records", body
                    assert wa.RECORDS_RULE in body["error"]
            assert _sends(door.records) == []
            status, body = await door.post(data=_form(
                {"recipient": "me", "source": str(root / "report.txt")},
                [("report.txt", b"fine")]))
            assert status == 200, body
            assert len(_sends(door.records)) == 1
    finally:
        os.rmdir(link)                      # the junction, not its target


@pytest.mark.asyncio
async def test_the_in_process_tool_refuses_the_records_too(
        tmp_path, monkeypatch, fake_argv, data_root):
    """AC-587 on the endpoint brain's path, with the daemon's own
    `records_dirs` (so a BUTLER_STATE that moved the records elsewhere
    still leaves <data>/.butler refused)."""
    root, secrets = data_root
    link = root / "innocent"
    _junction(link, root / ".butler" / "whatsapp")
    try:
        async with _Door(tmp_path, monkeypatch) as door:
            where = butlerd.paths_mod.resolve()
            assert where.records != root / ".butler"   # BUTLER_STATE moved
            agent = wa.AgentTools(door.hub.settings, root / "none.db", root,
                                  forbidden=wa.records_dirs(where), ask=None)
            for rel in list(secrets) + ["innocent/session/creds.json"]:
                path = rel if rel.startswith("innocent") \
                    else f".butler/{rel}"
                said = await agent.call("whatsapp_send", {
                    "recipient": "me", "file": path}, "c", 1)
                assert said.startswith("NOT SENT.") and \
                    wa.RECORDS_RULE in said, (path, said)
            assert _sends(door.records) == []
    finally:
        os.rmdir(link)


def test_file_inside_cannot_be_called_without_the_records():
    """The rule cannot be skipped by passing nothing to refuse."""
    with pytest.raises(ValueError):
        wa.file_inside(__file__, [REPO], ())


# --------------------------------------------------------------------------- #
# AC-588 (RV-2): the owner confirms, never the model
# --------------------------------------------------------------------------- #
CHAT, TURN = "chat-wa", 700


class _Sessions:
    """`session_brain()` for a hub on the endpoint brain: the conversation's
    project (a tool's file is judged against it) and the transcript note
    an answered question leaves."""

    def __init__(self, project: Path):
        self.project, self.notes = project, []

    def session_chat(self, chat, limit=None):
        return {"id": chat, "project": str(self.project)}             if chat == CHAT else None

    def record_note(self, chat, text):
        self.notes.append((chat, text))


def _turn_in(hub, chat=CHAT, turn=TURN):
    """A page session with a turn running in `chat` -- what `_begin_turn`
    registers (`turn_by_chat` / `chat_by_turn`)."""
    s = attach_session(hub, name="page")
    s.turn_by_chat[chat] = turn
    s.chat_by_turn[turn] = chat
    return s


def _questions(s):
    return [json.loads(m) for m in s.ws.sent if isinstance(m, str)
            and json.loads(m).get("type") == "question"]


async def _answer(door, qid, **answer):
    async with door.three.http.post(
            door.three.url(hub_mod.LOCAL, "/api/answer"),
            json={"qid": qid, **answer}) as r:
        return r.status, await r.json()


async def _held_send(door, body=None, data=None):
    """A tool's send to a person never written to, run beside the test,
    plus the question it raised."""
    task = asyncio.create_task(door.post(body, data=data))
    await _until(lambda: door.hub.questions_for(CHAT) or task.done(),
                 what="the owner's question")
    assert not task.done(), task.result()
    (q,) = door.hub.questions_for(CHAT)
    return task, q


@pytest.mark.asyncio
async def test_a_held_tool_send_asks_the_owner_and_yes_sends_it(
        tmp_path, monkeypatch, fake_argv):
    """AC-588. The tool's own `confirm: true` is DROPPED; the guard's 409
    becomes a question in the turn's chat, on every device; the owner's yes
    re-sends it confirmed."""
    async with _Door(tmp_path, monkeypatch) as door:
        s = _turn_in(door.hub)
        task, q = await _held_send(door, {"recipient": "Bruno",
                                          "text": "lunch at 1?",
                                          "chat": CHAT, "confirm": True})
        assert q["method"] == "confirm" and q["chat"] == CHAT
        assert q["message"] == 'Send "lunch at 1?" to Somebody?'
        assert [f["qid"] for f in _questions(s)] == [q["qid"]]
        (held,) = _sends(door.records)
        assert held["body"].get("confirm") is not True     # the tool's, gone
        status, verdict = await _answer(door, q["qid"], confirmed=True)
        assert status == 200, verdict
        status, body = await asyncio.wait_for(task, 10)
        assert status == 200 and body["text"] == "Sent to Somebody.", body
        held, sent = _sends(door.records)
        assert sent["body"]["confirm"] is True
        assert door.hub.questions_for(CHAT) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["no", "closed"])
async def test_a_no_or_a_closed_question_sends_nothing(
        tmp_path, tmp_path_factory, monkeypatch, fake_argv, how):
    """AC-588: the owner's no, or the question closing unanswered (the
    turn ended), is a refusal the model is told -- and the sidecar saw only
    the held, unconfirmed attempt. A file's question names the file."""
    project = tmp_path_factory.mktemp("proj")
    (project / "note.txt").write_bytes(b"hello")
    sessions = _Sessions(project)
    async with _Door(tmp_path, monkeypatch) as door:
        door.hub.session_brain = lambda: sessions
        _turn_in(door.hub)
        task, q = await _held_send(door, data=_form(
            {"recipient": "Bruno", "chat": CHAT, "confirm": "true",
             "source": str(project / "note.txt")},
            [("note.txt", b"hello")]))
        assert q["message"] == "Send note.txt to Somebody?"
        if how == "no":
            await _answer(door, q["qid"], confirmed=False)
            reason, words = "owner_declined", "said no"
        else:
            await door.hub.close_questions_for_turn(TURN, "cancelled")
            reason, words = "owner_not_answered", "did not answer"
        status, body = await asyncio.wait_for(task, 10)
        assert status == 403 and body["reason"] == reason, body
        assert body["text"].startswith("NOT SENT.") and words in body["text"]
        (held,) = _sends(door.records)
        assert held["body"]["fields"].get("confirm") != "true"
        assert sessions.notes == ([(CHAT, "Butler asked: Send note.txt to "
                                          "Somebody? -- you answered no.")]
                                  if how == "no" else [])


@pytest.mark.asyncio
async def test_no_turn_to_ask_in_is_a_refusal_not_a_send(
        tmp_path, monkeypatch, fake_argv):
    async with _Door(tmp_path, monkeypatch) as door:
        status, body = await door.post({"recipient": "Bruno", "text": "x",
                                        "chat": "chat-idle", "confirm": True})
        assert status == 409 and body["reason"] == "no_turn_to_ask", body
        assert body["text"].startswith("NOT SENT.")
        assert len(_sends(door.records)) == 1


@pytest.mark.asyncio
async def test_a_human_at_a_shell_keeps_confirm(tmp_path, monkeypatch,
                                                fake_argv):
    """tools/wa.py names no chat: its `--confirm` is a human's and is
    forwarded as given, with no question raised."""
    async with _Door(tmp_path, monkeypatch) as door:
        status, body = await door.post({"recipient": "Bruno", "text": "x",
                                        "confirm": True})
        assert status == 200, body
        assert door.hub._questions == {}


@pytest.mark.asyncio
async def test_the_in_process_tool_asks_the_owner_in_its_turn(
        tmp_path, monkeypatch, fake_argv):
    """AC-588 on the endpoint brain's path: the turn's chat and id reach
    `Hub.ask_owner`; a `confirm` argument is ignored."""
    async with _Door(tmp_path, monkeypatch) as door:
        _turn_in(door.hub)
        agent = wa.AgentTools(door.hub.settings, tmp_path / "none.db",
                              tmp_path, forbidden=(tmp_path / ".butler",),
                              ask=door.hub.ask_owner)
        task = asyncio.create_task(agent.call("whatsapp_send", {
            "recipient": "Bruno", "text": "hi", "confirm": True}, CHAT, TURN))
        await _until(lambda: door.hub.questions_for(CHAT) or task.done(),
                     what="the owner's question")
        (q,) = door.hub.questions_for(CHAT)
        await _answer(door, q["qid"], confirmed=True)
        assert await asyncio.wait_for(task, 10) == "Sent to Somebody."
        held, sent = _sends(door.records)
        assert held["body"].get("confirm") is not True
        assert sent["body"]["confirm"] is True


def test_no_tool_schema_offers_confirm_and_the_extension_never_sends_it():
    """AC-588's source half: neither the endpoint brain's schema nor the
    pi extension's has `confirm`, and the extension's send bodies name the
    chat (what makes the door treat it as a tool) and never confirm."""
    for tool in wa.SEND_TOOLS:
        assert "confirm" not in tool["function"]["parameters"]["properties"]
    ts = MEMORY_EXTENSION.read_text(encoding="utf-8")
    send = ts[ts.index('name: "whatsapp_send"'):ts.index(
        'name: "memory_search"')]
    assert not re.search(r"\bconfirm\s*:", send), "confirm in the tool"
    assert "{ recipient: params?.recipient, text: params?.text, chat }" in send
    assert re.search(r"\n\s+chat,\n\s+source: real,", send)
    assert '"x-butler-local": "1"' in ts and ts.count("...LOCAL_HEADER") == 2


def test_the_desktop_app_sends_the_local_header():
    rs = (REPO / "app" / "src-tauri" / "src" / "daemon.rs").read_text(
        encoding="utf-8")
    assert 'const LOCAL_HEADER: &str = "X-Butler-Local: 1\\r\\n";' in rs
    assert rs.count("{LOCAL_HEADER}") == 2


# --------------------------------------------------------------------------- #
# AC-589 (RV-3): wa.py waits in proportion and says "unknown" on a timeout
# --------------------------------------------------------------------------- #
WA = REPO / "tools" / "wa.py"


class _SlowDoor:
    """A stand-in for the door that records headers and answers late."""

    def __init__(self, delay_s: float):
        door = self
        self.got: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                door.got.append(dict(self.headers))
                time.sleep(delay_s)
                raw = b'{"ok": true}'
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _wa(code: str, url: str, db: Path):
    """Run wa.py in a SUBPROCESS (it rewraps stdout at import) with a
    snippet that may shorten its timeout first."""
    prog = (f"import sys; sys.path.insert(0, {str(WA.parent)!r}); "
            f"import wa; {code}")
    return subprocess.run([sys.executable, "-X", "utf8", "-c", prog],
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, BUTLER_URL=url), timeout=60)


def test_the_wait_scales_with_the_file_and_is_capped():
    out = _wa("print(wa.timeout_for(0), wa.timeout_for(100 * 1024 * 1024),"
              " wa.timeout_for(10 ** 10))", "http://127.0.0.1:1",
              REPO).stdout.split()
    assert [float(x) for x in out] == [30.0, 130.0, 630.0]
    assert wa.SEND_TIMEOUT_S + 30 == 630.0


def test_a_timeout_exits_3_unknown_never_a_traceback(tmp_path):
    db = wa_archive.build(tmp_path / "whatsapp.db", int(time.time()))
    door = _SlowDoor(delay_s=3.0)
    try:
        out = _wa("wa.TIMEOUT_BASE_S = 0.5; sys.argv = ['wa.py', '--db', "
                  f"{str(db)!r}, 'send', 'Bruno', 'hello']; wa.main()",
                  door.url, db)
    finally:
        door.close()
    assert out.returncode == 3, (out.stdout, out.stderr)
    assert "unknown whether it was sent; check before retrying" in out.stderr
    assert "Traceback" not in out.stderr
    (headers,) = door.got
    assert headers.get("X-Butler-Local") == "1"


def test_the_skill_documents_exit_3():
    text = (REPO / "tools" / "wa-SKILL.md").read_text(encoding="utf-8")
    assert "3 = UNKNOWN whether it was sent" in text

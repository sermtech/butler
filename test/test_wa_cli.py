"""`tools/wa.py`, the port of /whatsapp-skill's CLI (docs/messaging.md MS-9).

AC-510: the eight subcommands, the `--since` grammar and the exit codes (0
ok, 2 needs confirmation or ambiguous, 1 everything else) match the
original; READS work with Butler stopped (`BUTLER_URL` points at a port
nothing listens on); SENDS go only through the daemon's door.

Every run is a SUBPROCESS: the CLI rewraps stdout at import, as the
original does, which must not happen inside pytest. The send tests stand up
a stand-in for the daemon's door that records what it was sent.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "test"))

from assets import wa_archive                                 # noqa: E402
from conftest import free_port                                # noqa: E402

WA = REPO / "tools" / "wa.py"
NOW = int(time.time())
DEAD = f"http://127.0.0.1:{free_port()}"


def run(args, *, db=None, url=DEAD, env=None, stdin=None):
    full = dict(os.environ, BUTLER_URL=url, **(env or {}))
    argv = [sys.executable, "-X", "utf8", str(WA)]
    if db is not None:
        argv += ["--db", str(db)]
    out = subprocess.run(argv + list(args), capture_output=True, text=True,
                         encoding="utf-8", env=full, input=stdin, timeout=60)
    return out.returncode, out.stdout, out.stderr


@pytest.fixture
def db(tmp_path) -> Path:
    return wa_archive.build(tmp_path / "whatsapp.db", NOW)


def test_the_eight_subcommands_are_the_originals():
    code, out, _ = run(["--help"])
    assert code == 0
    listed = out.split("{", 1)[1].split("}", 1)[0].split(",")
    assert listed == ["status", "inbox", "read", "search", "chats",
                      "contacts", "send", "me"]


def test_inbox_today_is_received_messages_only_with_butler_stopped(db):
    code, out, err = run(["inbox", "--json"], db=db)
    assert code == 0, err
    rows = json.loads(out)
    texts = [r["text"] for r in rows]
    assert "dentist at 3pm tomorrow" in texts
    assert "ok see you" not in texts                 # from_me
    assert "old dinner plan" not in texts            # ten days ago
    assert "my status" not in texts                  # status@broadcast
    assert "[image] beach photo" in texts
    assert "[voice note 12s]" in texts
    assert "bring the charger" in texts              # unwrapped
    assert all(r["kind"] for r in rows)              # protocol noise skipped
    code, out, _ = run(["inbox", "--no-groups", "--json"], db=db)
    assert not any(r["group"] for r in json.loads(out))


@pytest.mark.parametrize("since,old_seen", [
    ("90m", False), ("24h", False), ("7d", False), ("11d", True),
    ("yesterday", False),
    ((dt.date.today() - dt.timedelta(days=30)).isoformat(), True),
])
def test_the_since_grammar(db, since, old_seen):
    code, out, err = run(["inbox", "--since", since, "--json"], db=db)
    assert code == 0, err
    texts = [r["text"] for r in json.loads(out)]
    assert ("old dinner plan" in texts) is old_seen
    assert "dentist at 3pm tomorrow" in texts


def test_a_bad_since_exits_1(db):
    code, _, err = run(["inbox", "--since", "lately"], db=db)
    assert code == 1 and "bad --since" in err


def test_read_refuses_to_guess(db):
    code, out, err = run(["read", "bruno"], db=db)
    assert code == 0, err
    lines = out.splitlines()
    assert lines[-1].endswith("Bruno Lima: bring the charger")  # oldest first
    code, _, err = run(["read", "ana"], db=db)
    assert code == 2 and "matches 2 chats" in err   # chat names, not contacts
    code, _, err = run(["read", "nobody-here"], db=db)
    assert code == 1 and "no chat matches" in err


def test_search_chats_and_contacts(db):
    code, out, _ = run(["search", "dinner", "--since", "30d"], db=db)
    assert code == 0 and "old dinner plan" in out and "dinner sunday" in out
    code, out, _ = run(["search", "dinner", "--chat", "family"], db=db)
    assert code == 0 and "old dinner plan" not in out
    code, out, _ = run(["chats", "--groups"], db=db)
    assert code == 0 and "G  Family Group" in out and "Bruno" not in out
    code, out, _ = run(["contacts", "ana"], db=db)
    assert code == 0 and out.count("\n") == 3


def test_the_archive_is_found_through_the_data_root(tmp_path):
    root = tmp_path / "data"
    wa_archive.build(root / ".butler" / "whatsapp" / "whatsapp.db", NOW)
    env = {"BUTLER_DATA": str(root)}
    full = {k: v for k, v in os.environ.items() if k != "BUTLER_STATE"}
    out = subprocess.run(
        [sys.executable, "-X", "utf8", str(WA), "chats"],
        capture_output=True, text=True, encoding="utf-8",
        env={**full, **env, "BUTLER_URL": DEAD}, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "Family Group" in out.stdout


def test_a_missing_archive_exits_1(tmp_path):
    code, _, err = run(["chats"], db=tmp_path / "none.db")
    assert code == 1 and "archive not found" in err


def test_status_and_send_need_butler_and_exit_1_without_it(db):
    for args in (["status"], ["send", "bruno", "hi"], ["me", "hi"]):
        code, _, err = run(args, db=db)
        assert code == 1 and "not reachable" in err, args


# --------------------------------------------------------------------------- #
# Sends go through the door: a stand-in records them
# --------------------------------------------------------------------------- #
class _Door:
    def __init__(self, status: int, answer: dict):
        self.got: list[dict] = []
        door = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                door.got.append({"path": self.path,
                                 "type": self.headers.get("Content-Type"),
                                 "body": self.rfile.read(n)})
                self._reply(status, answer)

            def do_GET(self):
                door.got.append({"path": self.path})
                self._reply(200, {"ok": True, "status": "paired",
                                  "phone": "+15559990000"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("status,code", [(200, 0), (409, 2), (403, 1),
                                         (503, 1), (502, 1)])
def test_send_exit_codes_follow_the_door(db, status, code):
    door = _Door(status, {"ok": status == 200, "reason": "x"})
    try:
        got, out, _ = run(["send", "Bruno", "hello"], db=db, url=door.url)
    finally:
        door.close()
    assert got == code
    assert json.loads(out)["http"] == status
    (req,) = door.got
    assert req["path"] == "/api/whatsapp/send"
    assert json.loads(req["body"]) == {"recipient": "Bruno",
                                       "text": "hello", "confirm": False}


def test_me_goes_to_me_unconfirmed_and_a_file_is_multipart(db, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello file", encoding="utf-8")
    door = _Door(200, {"ok": True})
    try:
        assert run(["me", "-"], db=db, url=door.url, stdin="from stdin")[0] == 0
        assert run(["send", "Bruno", "--file", str(f), "--caption", "c",
                    "--confirm"], db=db, url=door.url)[0] == 0
        assert run(["status"], db=db, url=door.url)[0] == 0
    finally:
        door.close()
    me, upload, status = door.got
    assert json.loads(me["body"]) == {"recipient": "me", "text": "from stdin",
                                      "confirm": False}
    assert upload["type"].startswith("multipart/form-data; boundary=")
    body = upload["body"]
    assert b'name="files"; filename="note.txt"' in body
    assert b"hello file" in body and b'name="confirm"\r\n\r\ntrue' in body
    assert status["path"] == "/api/whatsapp/status"

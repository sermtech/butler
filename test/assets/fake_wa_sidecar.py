"""A stand-in for `daemon/engines/whatsapp/wa-sidecar.mjs` at the HTTP layer.

docs/messaging.md section 12: the daemon side is tested against a fake
sidecar, the way `test_notify.py` fakes the Bot API. It honours the real
process contract (wa-sidecar.mjs header): needs BUTLER_WA_TOKEN and
BUTLER_WA_DIR, binds 127.0.0.1:0, prints the port as the FIRST stdout line,
logs only to stderr, and answers 401 to any request without the token.

What the tests read back, all inside BUTLER_WA_DIR (so a test also proves
WHERE the daemon put the records, AC-501):
  boots.txt       one line per process start
  requests.jsonl  one JSON line per request: method, path, token_ok, body

Knobs: FAKE_WA_DIE_AT_BOOT=1 exits 3 before printing a port (a sidecar that
cannot start); FAKE_WA_STATUS=<name> starts in that pairing status;
FAKE_WA_LINKED=1 makes /status say linked as OWN_JID (with a device part,
as Baileys reports it).

POST /send (phases M4/M5) answers by the sidecar's status contract
(wa-sidecar.mjs header) and records what it was given: a JSON body as is, a
multipart one as `{"fields": {...}, "files": [{name, size, sha256}]}`. Its
"guard" is a script keyed on the recipient, NOT the real guard (that is
pinned by the Node tests): send_enabled false -> 403 send_disabled;
"ambiguous" -> 409 ambiguous_recipient with candidates; "stranger" -> 403
unknown_recipient; "offline" -> 503; "broken" -> 502 send_failed; OWN_JID
-> 200; anyone else -> 409 confirmation_required until a send carries
confirm (JSON true, or the form string "true"), then 200.

Inbound (phases M6/M7): GET /events is a real NDJSON stream ({type:'ready'}
first). A TEST pushes items into every open stream with POST /_push (token
required, body = the item), and the fake answers how many streams took it;
GET /_streams counts the open ones.
GET /media/<id> serves the bytes of `<BUTLER_WA_DIR>/media/<id>` (404 when
absent). POST /reply and POST /presence are recorded and answered 200 (a
reply's messageId is "R<n>"); so is POST /guest-reply (M9, "G<n>").
"""
import hashlib
import json
import os
import queue
import sys
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = os.environ.get("BUTLER_WA_TOKEN")
DIR = os.environ.get("BUTLER_WA_DIR")
if not TOKEN or not DIR:
    print("fake-wa: BUTLER_WA_TOKEN and BUTLER_WA_DIR are required",
          file=sys.stderr, flush=True)
    sys.exit(1)
RECORDS = Path(DIR)
RECORDS.mkdir(parents=True, exist_ok=True)
with open(RECORDS / "boots.txt", "a", encoding="utf-8") as f:
    f.write(f"{os.getpid()}\n")
if os.environ.get("FAKE_WA_DIE_AT_BOOT") == "1":
    print("fake-wa: dying at boot on purpose", file=sys.stderr, flush=True)
    sys.exit(3)

# A 1x1 PNG, enough to be a PNG data URL.
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
       "DUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
WORDS = {"idle": "Not linked", "awaiting_scan": "Scan this code",
         "paired": "Linked", "replaced": "Link open elsewhere",
         "logged_out": "Logged out"}
state = {"status": os.environ.get("FAKE_WA_STATUS", "idle"), "qrCount": 0}
OWN_JID = "15559990000@s.whatsapp.net"
LINKED = os.environ.get("FAKE_WA_LINKED") == "1"


def parse_multipart(ctype: str, raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
    fields, files = {}, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            files.append({"field": name, "name": part.get_filename(),
                          "size": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
        else:
            fields[name] = data.decode("utf-8")
    return {"fields": fields, "files": files}


def send(body: dict):
    """The scripted guard (see the module docstring)."""
    multipart = "fields" in body
    f = body["fields"] if multipart else body
    enabled = f.get("send_enabled")
    if (enabled == "false") if multipart else (enabled is False):
        return 403, {"ok": False, "reason": "send_disabled"}
    to = f.get("recipient")
    confirm = (f.get("confirm") == "true") if multipart else (
        f.get("confirm") is True)
    if to == "ambiguous":
        return 409, {"ok": False, "reason": "ambiguous_recipient",
                     "candidates": [{"jid": "1@s.whatsapp.net", "name": "A"},
                                    {"jid": "2@s.whatsapp.net", "name": "B"}]}
    if to == "stranger":
        return 403, {"ok": False, "reason": "unknown_recipient"}
    if to == "offline":
        return 503, {"ok": False,
                     "error": "WhatsApp is not connected -- link a device "
                              "first"}
    if to == "broken":
        return 502, {"ok": False, "reason": "send_failed", "error": "boom"}
    if to != OWN_JID and not confirm and to not in CONFIRMED:
        return 409, {"ok": False, "reason": "confirmation_required",
                     "jid": to, "name": "Somebody"}
    CONFIRMED.add(to)
    return 200, {"ok": True, "jid": to, "name": "Somebody",
                 "messageId": "MSG1"}


CONFIRMED: set = set()
STREAMS: list = []
STREAMS_LOCK = threading.Lock()
RECORD_LOCK = threading.Lock()
REPLIES = [0]


def view():
    st = state["status"]
    return {"ok": True, "state": dict(state),
            "view": {"headline": WORDS.get(st, st),
                     "detail": f"<b>detail for {st}</b>", "tone": "neutral"},
            "qrDataUrl": PNG if st == "awaiting_scan" else None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("fake-wa: " + fmt % args, file=sys.stderr, flush=True)

    def _record(self, body):
        line = json.dumps({
            "method": self.command, "path": self.path,
            "token_ok": self.headers.get("X-Butler-Token") == TOKEN,
            "body": body}) + "\n"
        # Handler threads overlap (ThreadingHTTPServer) and a Windows append
        # is seek-then-write: unlocked, concurrent lines overwrite each other
        # (docs/messaging.md RV-19).
        with RECORD_LOCK, open(RECORDS / "requests.jsonl", "a",
                               encoding="utf-8") as f:
            f.write(line)

    def _answer(self, status, doc):
        raw = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type") or ""
        if ctype.startswith("multipart/"):
            body = parse_multipart(ctype, raw)
        else:
            body = json.loads(raw) if raw else None
        self._record(body)
        if self.headers.get("X-Butler-Token") != TOKEN:
            return self._answer(401, {"ok": False,
                                      "error": "missing or wrong token"})
        path = self.path.split("?")[0]
        if path == "/pairing":
            if self.command == "POST":
                state["status"] = "awaiting_scan"
                state["qrCount"] += 1
            elif self.command == "DELETE":
                state["status"] = "idle"
                state["qrCount"] = 0
            return self._answer(200, view())
        if path == "/config" and self.command == "POST":
            if not isinstance(body, dict) or not isinstance(
                    body.get("trigger", "x"), str):
                return self._answer(400, {"ok": False, "error": "bad config"})
            return self._answer(200, {"ok": True, "config": body})
        if path == "/status":
            return self._answer(200, {
                "ok": True, "status": state["status"], "linked": LINKED,
                "jid": OWN_JID.replace("@", ":7@") if LINKED else None,
                "phone": "+15559990000" if LINKED else None,
                "contacts": 6, "chats": 6, "messages": 11})
        if path == "/send" and self.command == "POST":
            return self._answer(*send(body))
        if self._inbound(path, body) is not None:
            return None
        return self._answer(404, {"ok": False, "error": "no route"})

    def _events(self):
        """Hold the stream open; write every pushed item as one line."""
        mine: queue.Queue = queue.Queue()
        with STREAMS_LOCK:
            STREAMS.append(mine)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(b'{"type": "ready"}\n')
            self.wfile.flush()
            while True:
                item = mine.get()
                self.wfile.write(json.dumps(item).encode() + b"\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with STREAMS_LOCK:
                STREAMS.remove(mine)

    def _media(self, media_id):
        f = RECORDS / "media" / media_id
        if not f.is_file():
            return self._answer(404, {"ok": False, "reason": "unknown_media",
                                      "error": "unknown or expired media id"})
        raw = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return None

    def _inbound(self, path, body):
        """The M6/M7 routes; None when `path` is not one of them."""
        if path == "/events" and self.command == "GET":
            return self._events() or True
        if path.startswith("/media/") and self.command == "GET":
            return self._media(path[len("/media/"):]) or True
        if path == "/_streams" and self.command == "GET":
            with STREAMS_LOCK:
                return self._answer(200, {"streams": len(STREAMS)}) or True
        if path == "/_push" and self.command == "POST":
            with STREAMS_LOCK:
                streams = list(STREAMS)
            for q in streams:
                q.put(body)
            return self._answer(200, {"ok": True,
                                      "streams": len(streams)}) or True
        if path == "/reply" and self.command == "POST":
            REPLIES[0] += 1
            return self._answer(200, {"ok": True,
                                      "messageId": f"R{REPLIES[0]}"}) or True
        if path == "/guest-reply" and self.command == "POST":
            # M9: recorded and answered; the chat and prefix rules are the
            # real sidecar's, pinned by its node tests (guest.test.mjs).
            REPLIES[0] += 1
            return self._answer(200, {"ok": True,
                                      "messageId": f"G{REPLIES[0]}"}) or True
        if path == "/presence" and self.command == "POST":
            return self._answer(200, {"ok": True}) or True
        return None

    do_GET = do_POST = do_DELETE = _handle


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
print("fake-wa: listening", file=sys.stderr, flush=True)
server.serve_forever()

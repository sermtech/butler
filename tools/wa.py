"""wa.py: read and send the user's WhatsApp through Butler, from any agent or shell.

A port of /whatsapp-skill's wa.py (docs/messaging.md MS-9, AC-510): the same
eight subcommands, the same --since grammar and the same exit codes (0 ok,
2 needs confirmation or ambiguous, 1 everything else), plus 3: the send timed
out and may have gone out (RV-3, AC-589). Stdlib only.

READS come straight from Butler's archive (<records>/whatsapp/whatsapp.db,
found through Butler's data root exactly as daemon/paths.py resolves it:
BUTLER_DATA, else the legacy layout; BUTLER_STATE moves the records),
opened READ-ONLY, so they work with Butler stopped -- but the archive only
grows while Butler holds the linked device.

SENDS go ONLY through Butler's guarded door, POST /api/whatsapp/send on the
daemon's loopback listener (known contacts and groups only, ambiguity
refused, first send to a person needs --confirm, rate limited, audited).
There is no other send path on purpose: a second one would skip every guard.

Run with: python -X utf8 tools/wa.py <command> ...
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer)
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from daemon import paths  # noqa: E402  (stdlib only: the ONE record-path resolver)

# The daemon's LOCAL listener: plain HTTP on 127.0.0.1, the one the send door
# trusts (`scripts/launch.py` starts it on 8141).
BUTLER = os.environ.get("BUTLER_URL", "http://127.0.0.1:8141").rstrip("/")
# Every local route also needs this header (docs/messaging.md 6.5 RV-1,
# AC-586): a web page cannot send it cross-site, this CLI does.
LOCAL_HEADER = {"X-Butler-Local": "1"}
# How long a call waits for the daemon: 30 s, plus 1 s per MB uploaded (a
# 100 MB file goes out in minutes), capped at the daemon's own wait on the
# sidecar (daemon/whatsapp.py SEND_TIMEOUT_S, 600 s) plus a margin (RV-3).
TIMEOUT_BASE_S = 30.0
TIMEOUT_PER_MB_S = 1.0
TIMEOUT_MAX_S = 630.0
# Exit 3: the send may have gone out; never a blind retry (RV-3, AC-589).
UNKNOWN_EXIT = 3
# The user's own chat: `me` sends here. The door resolves it from the linked
# account and treats it as pre-confirmed.
ME = "me"

# Wrappers whose payload is another message; unwrapped before classifying.
# KEEP EQUAL to WRAPPER_KEYS in daemon/engines/whatsapp/whatsapp-store.js.
WRAPPERS = ("associatedChildMessage", "ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
            "viewOnceMessageV2Extension", "documentWithCaptionMessage", "editedMessage")
# Content kinds in the order they are looked for; anything else is protocol noise.
KINDS = {
    "conversation": "text", "extendedTextMessage": "text", "imageMessage": "image",
    "videoMessage": "video", "audioMessage": "audio", "documentMessage": "document",
    "stickerMessage": "sticker", "contactMessage": "contact", "locationMessage": "location",
    "pollCreationMessage": "poll", "pollCreationMessageV3": "poll", "reactionMessage": "reaction",
    "albumMessage": "album",
}


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def db_path(args):
    if args.db:
        return args.db
    try:
        return str(paths.resolve().whatsapp / "whatsapp.db")
    except paths.DataRootError as e:
        die(f"{e}; or pass --db <path to whatsapp.db>")


def connect(args):
    path = db_path(args)
    if not os.path.exists(path):
        die(f"archive not found: {path}")
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def parse_since(value):
    """'today', '24h', '90m', '7d', or an ISO date/time (local). Returns epoch seconds."""
    if value is None:
        return 0
    now = dt.datetime.now()
    if value == "today":
        return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if value == "yesterday":
        y = now - dt.timedelta(days=1)
        return int(y.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    m = re.fullmatch(r"(\d+)([mhd])", value)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n), "d": dt.timedelta(days=n)}[unit]
        return int((now - delta).timestamp())
    try:
        return int(dt.datetime.fromisoformat(value).timestamp())
    except ValueError:
        die(f"bad --since value: {value!r} (use today, yesterday, 24h, 90m, 7d or an ISO date)")


def classify(raw, text):
    """(kind, display) for a stored message; kind None means protocol noise to skip."""
    msg = json.loads(raw) if raw else {}
    for _ in range(3):
        inner = next((msg[w].get("message") for w in WRAPPERS if isinstance(msg.get(w), dict) and msg[w].get("message")), None)
        if not inner:
            break
        msg = inner
    kind_key = next((k for k in KINDS if k in msg), None)
    if not kind_key:
        return None, None
    kind = KINDS[kind_key]
    body = msg[kind_key] if isinstance(msg[kind_key], dict) else {}
    if kind == "text":
        return kind, text or (msg.get("conversation") or body.get("text") or "")
    if kind == "reaction":
        return kind, f"[reacted {body.get('text', '')}]"
    if kind == "album":
        # The counts are usually absent; the album's items arrive as their own messages.
        n = (body.get("expectedImageCount") or 0) + (body.get("expectedVideoCount") or 0)
        return kind, f"[album: {n} items]" if n else "[album]"
    label = kind
    if kind == "audio":
        label = "voice note" if body.get("ptt") else "audio"
        if body.get("seconds"):
            label += f" {body['seconds']}s"
    if kind == "video" and body.get("seconds"):
        label += f" {body['seconds']}s"
    if kind == "document":
        label += f": {body.get('fileName', '')}"
    caption = body.get("caption") or text or ""
    return kind, f"[{label}]" + (f" {caption}" if caption else "")


class Names:
    """jid -> best display name: saved name, then the sender's own push name, then digits."""

    def __init__(self, con):
        self.contacts = {j: (n or no) for j, n, no in con.execute("select jid, name, notify from contacts")}
        self.chats = {j: n for j, n in con.execute("select jid, name from chats") if n}

    def of(self, jid, push=None):
        if not jid:
            return push or "?"
        return self.chats.get(jid) or self.contacts.get(jid) or push or "+" + jid.split("@")[0].split(":")[0]


def resolve_chat(con, names, query):
    """Exact jid, a phone number, or a unique name match. Ambiguity is refused, never guessed."""
    if "@" in query:
        return query
    digits = re.sub(r"[^\d]", "", query)
    if digits and len(digits) >= 7 and digits == query.lstrip("+").replace(" ", "").replace("-", ""):
        return f"{digits}@s.whatsapp.net"
    q = query.lower()
    hits = sorted({j for j, _ in con.execute("select jid, name from chats")
                   if q in (names.of(j) or "").lower()})
    if len(hits) == 1:
        return hits[0]
    if not hits:
        die(f"no chat matches {query!r}")
    listing = "\n".join(f"  {names.of(j)}  ({j})" for j in hits[:20])
    die(f"{query!r} matches {len(hits)} chats -- use one of these jids:\n{listing}", 2)


def fmt_ts(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def emit_rows(rows, names, as_json):
    out = []
    for jid, from_me, sender, push, ts, text, raw in rows:
        kind, display = classify(raw, text)
        if kind is None:
            continue
        who = "me" if from_me else names.of(sender or jid, push)
        out.append({"time": fmt_ts(ts), "chat": names.of(jid), "chatJid": jid, "from": who,
                    "group": jid.endswith("@g.us"), "kind": kind, "text": display})
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    for r in out:
        where = f"{r['chat']} / {r['from']}" if r["group"] else r["from"] if r["from"] != "me" else f"me -> {r['chat']}"
        print(f"{r['time']}  {where}: {r['text']}")
    print(f"({len(out)} messages)", file=sys.stderr)


COLS = "jid, from_me, sender, push_name, ts, text, raw"


def cmd_inbox(con, args):
    names = Names(con)
    sql = f"select {COLS} from messages where from_me = 0 and ts >= ? and jid != 'status@broadcast'"
    params = [parse_since(args.since)]
    if args.no_groups:
        sql += " and jid not like '%@g.us'"
    rows = con.execute(sql + " order by jid, ts", params).fetchall()
    emit_rows(rows, names, args.json)


def cmd_read(con, args):
    names = Names(con)
    jid = resolve_chat(con, names, args.chat)
    rows = con.execute(f"select {COLS} from messages where jid = ? and ts >= ? order by ts desc limit ?",
                       (jid, parse_since(args.since), args.limit)).fetchall()
    emit_rows(list(reversed(rows)), names, args.json)


def cmd_search(con, args):
    names = Names(con)
    sql = f"select {COLS} from messages where text like ? and ts >= ?"
    params = [f"%{args.query}%", parse_since(args.since)]
    if args.chat:
        sql += " and jid = ?"
        params.append(resolve_chat(con, names, args.chat))
    rows = con.execute(sql + " order by ts desc limit ?", params + [args.limit]).fetchall()
    emit_rows(list(reversed(rows)), names, args.json)


def cmd_chats(con, args):
    names = Names(con)
    sql = "select jid, is_group, last_message_at from chats where jid != 'status@broadcast'"
    if args.groups:
        sql += " and is_group = 1"
    rows = con.execute(sql + " order by coalesce(last_message_at, 0) desc limit ?", (args.limit,)).fetchall()
    for jid, is_group, last in rows:
        print(f"{fmt_ts(last) if last else '-':16}  {'G' if is_group else ' '}  {names.of(jid)}  ({jid})")


def cmd_contacts(con, args):
    q = f"%{args.query}%"
    rows = con.execute("select jid, name, notify from contacts where name like ? or notify like ? or jid like ? limit ?",
                       (q, q, q, args.limit)).fetchall()
    for jid, name, notify in rows:
        print(f"{name or notify or '-'}  ({jid})" + (f"  [push: {notify}]" if name and notify and name != notify else ""))


def timeout_for(size: int) -> float:
    """The client's wait for a body of `size` bytes (RV-3)."""
    return min(TIMEOUT_BASE_S + TIMEOUT_PER_MB_S * size / (1024 * 1024), TIMEOUT_MAX_S)


def http(method, path, body=None, raw=None, content_type="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BUTLER + path, data=data, method=method,
                                 headers={"Content-Type": content_type, **LOCAL_HEADER})
    try:
        with urllib.request.urlopen(req, timeout=timeout_for(len(data or b""))) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (TimeoutError, urllib.error.URLError) as e:
        reason = getattr(e, "reason", e)
        if method == "POST" and isinstance(reason, TimeoutError):
            # The request may have reached Butler, which keeps sending after
            # this client stops waiting: a retry could send it twice.
            die(f"Butler did not answer in time; unknown whether it was sent; "
                f"check before retrying", UNKNOWN_EXIT)
        die(f"Butler not reachable at {BUTLER} ({reason}); sending needs it running")


def cmd_status(con, args):
    code, link = http("GET", "/api/whatsapp/status")
    state = link if code == 200 else {}
    counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("contacts", "chats", "messages")}
    newest = con.execute("select max(ts) from messages").fetchone()[0]
    print(json.dumps({"link": state.get("status"), "phone": state.get("phone"), **counts,
                      "newestMessage": fmt_ts(newest) if newest else None}, indent=1))


def multipart(fields, file_field, file_name, data):
    """Stdlib multipart/form-data body. The sidecar decides the file kind from the NAME."""
    boundary = uuid.uuid4().hex
    crlf = b"\r\n"
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    safe = file_name.replace('"', "_")
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{safe}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + data + crlf
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


def cmd_send(con, args):
    text = sys.stdin.read() if args.text == "-" else args.text
    if not text and not args.file:
        die("send needs text or --file")
    if args.file:
        if not os.path.isfile(args.file):
            die(f"file not found: {args.file}")
        with open(args.file, "rb") as fh:
            data = fh.read()
        fields = {"recipient": args.to, "confirm": "true" if args.confirm else "false"}
        if text:
            fields["text"] = text
        if args.caption:
            fields["caption"] = args.caption
        body, ctype = multipart(fields, "files", os.path.basename(args.file), data)
        code, res = http("POST", "/api/whatsapp/send", raw=body, content_type=ctype)
    else:
        code, res = http("POST", "/api/whatsapp/send",
                         {"recipient": args.to, "text": text, "confirm": bool(args.confirm)})
    print(json.dumps({"http": code, **res}, ensure_ascii=False))
    if code == 200:
        return
    sys.exit(2 if code == 409 else UNKNOWN_EXIT if code == 504 else 1)


def cmd_me(con, args):
    args.to = ME
    args.confirm = False
    cmd_send(con, args)


def main():
    p = argparse.ArgumentParser(prog="wa.py", description=__doc__.split("\n")[0])
    p.add_argument("--db", help="path to whatsapp.db (default: <Butler records>/whatsapp/whatsapp.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="link state and archive size")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("inbox", help="messages RECEIVED since a time, grouped by chat")
    s.add_argument("--since", default="today")
    s.add_argument("--no-groups", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_inbox)
    s = sub.add_parser("read", help="recent messages of one chat (name, number or jid)")
    s.add_argument("chat")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--since")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_read)
    s = sub.add_parser("search", help="text search across messages")
    s.add_argument("query")
    s.add_argument("--chat")
    s.add_argument("--since")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("chats", help="chats, most recent first")
    s.add_argument("--groups", action="store_true")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_chats)
    s = sub.add_parser("contacts", help="find contacts by name, push name or number")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_contacts)
    s = sub.add_parser("send", help="send through Butler's guarded door")
    s.add_argument("to", help="contact or group name, phone number or jid")
    s.add_argument("text", nargs="?", help="message text, or - to read stdin (optional with --file)")
    s.add_argument("--file", help="file to upload and send (image, video, audio, PDF, any document)")
    s.add_argument("--caption", help="caption for --file")
    s.add_argument("--confirm", action="store_true", help="required for the first send to a recipient")
    s.set_defaults(fn=cmd_send)
    s = sub.add_parser("me", help="send to the user's own chat")
    s.add_argument("text", nargs="?", help="message text, or - to read stdin (optional with --file)")
    s.add_argument("--file", help="file to upload and send (image, video, audio, PDF, any document)")
    s.add_argument("--caption", help="caption for --file")
    s.set_defaults(fn=cmd_me)

    args = p.parse_args()
    args.fn(connect(args), args)


if __name__ == "__main__":
    main()

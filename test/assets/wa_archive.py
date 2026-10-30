"""A small WhatsApp archive with the sidecar's schema (an earlier prototype's, MS-7).

`build(path, now)` writes `whatsapp.db` the way `whatsapp-store.js` would
have: the same three tables and index, `ts` in epoch SECONDS, `raw` the
message content itself (no wrapper). Shaped for the refuse-to-guess tests
(test/AGENTS.md: the wanted item in the MIDDLE of three):

  three contacts match "ana" (Ana Costa, Ana Silva, Anabela), so a resolver
  that takes the first match picks one of them and is caught; "bruno"
  matches exactly one; "Family Group" is a group with no contact row; one
  contact is known only by its push name; one is a LID row.

Messages: today (relative to `now`) and ten days ago, from both sides, one
group message, an image with a caption, a voice note, a reaction, a
message wrapped in `ephemeralMessage`, and protocol noise that every reader
skips.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE contacts (jid TEXT PRIMARY KEY, name TEXT, notify TEXT,
                       updated_at INTEGER);
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT,
                    is_group INTEGER NOT NULL DEFAULT 0,
                    last_message_at INTEGER, updated_at INTEGER);
CREATE TABLE messages (jid TEXT NOT NULL, id TEXT NOT NULL,
                       from_me INTEGER NOT NULL DEFAULT 0, sender TEXT,
                       push_name TEXT, ts INTEGER NOT NULL DEFAULT 0,
                       text TEXT, raw TEXT, PRIMARY KEY (jid, id));
CREATE INDEX messages_by_chat ON messages (jid, ts DESC);
"""

ANA_COSTA = "15550000002@s.whatsapp.net"
ANA_SILVA = "15550000001@s.whatsapp.net"
ANABELA = "15550000005@s.whatsapp.net"
BRUNO = "15550000003@s.whatsapp.net"
CARLA = "15550000004@s.whatsapp.net"
LID = "99887766554433@lid"
FAMILY = "120363000000000001@g.us"
OWN = "15559990000@s.whatsapp.net"

DAY = 86400


def build(path: Path, now: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO contacts VALUES (?, ?, ?, ?)", [
        (ANA_SILVA, "Ana Silva", "Ana", now),
        (ANA_COSTA, "Ana Costa", None, now),
        (ANABELA, "Anabela", None, now),
        (BRUNO, "Bruno Lima", "Bruno", now),
        (CARLA, None, "Carla", now),
        (LID, None, "Lid Person", now),
    ])
    old = now - 10 * DAY
    con.executemany("INSERT INTO chats VALUES (?, ?, ?, ?, ?)", [
        (BRUNO, None, 0, now - 60, now),
        (ANA_SILVA, None, 0, now - 120, now),
        (ANA_COSTA, None, 0, old - 100, now),
        (FAMILY, "Family Group", 1, now - 30, now),
        (CARLA, None, 0, old, now),
        (OWN, None, 0, now - 300, now),
        ("status@broadcast", None, 0, now - 10, now),
    ])

    def text(t):
        return json.dumps({"conversation": t})

    rows = [
        (BRUNO, "b1", 0, None, "Bruno", old, "old dinner plan", text("old dinner plan")),
        (BRUNO, "b2", 0, None, "Bruno", now - 200, "dentist at 3pm tomorrow", text("dentist at 3pm tomorrow")),
        (BRUNO, "b3", 1, None, None, now - 100, "ok see you", text("ok see you")),
        (BRUNO, "b4", 0, None, "Bruno", now - 60, "bring the charger",
         json.dumps({"ephemeralMessage": {"message": {"extendedTextMessage": {"text": "bring the charger"}}}})),
        (ANA_SILVA, "a1", 0, None, "Ana", now - 150, "beach photo",
         json.dumps({"imageMessage": {"caption": "beach photo", "mimetype": "image/jpeg"}})),
        (ANA_SILVA, "a2", 0, None, "Ana", now - 120, None,
         json.dumps({"audioMessage": {"ptt": True, "seconds": 12}})),
        (ANA_SILVA, "a3", 0, None, "Ana", now - 110, None,
         json.dumps({"protocolMessage": {"type": 0}})),
        (FAMILY, "g1", 0, CARLA, "Carla", now - 30, "dinner sunday?", text("dinner sunday?")),
        (FAMILY, "g2", 0, BRUNO, "Bruno", now - 20, None,
         json.dumps({"reactionMessage": {"text": "+1"}})),
        (CARLA, "c1", 0, None, "Carla", old, "happy birthday", text("happy birthday")),
        ("status@broadcast", "s1", 0, BRUNO, "Bruno", now - 10, "my status", text("my status")),
    ]
    con.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return path

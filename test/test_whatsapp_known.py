"""RV-20 (AC-606, F-3): who counts as a KNOWN WhatsApp contact.

Baileys (rc14) emits `contacts.update {id, notify: pushName}` for EVERY inbound
message (node_modules/@whiskeysockets/baileys/lib/Socket/chats.js:917-924),
group members included, so "the archive has a contact row" meant "ever
wrote". An address-book name arrives from app-state contact actions
(lib/Utils/sync-action-utils.js:23) and from the `name` of a history-sync
conversation (lib/Utils/history.js:58 builds the contact's name from
`displayName || name || username`, so the sidecar re-reads the raw
conversation instead). Known is now: a saved name, or a chat the user
wrote in themselves; a group in the archive is unchanged.

This file holds the PYTHON twin (`Archive.is_known`, used by a notify
channel's `to`); the sidecar's own rule is tested by the node tests
(`store.test.mjs`, `send-guard.test.mjs`, `inbound.test.mjs`). Nothing here
touches WhatsApp or the owner's archive.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "test"))

from daemon import whatsapp as wa                              # noqa: E402
from assets import wa_archive                                 # noqa: E402

NOW = 1_790_000_000
STRANGER = "15557770000@s.whatsapp.net"
WROTE_TO = "15557770001@s.whatsapp.net"
BUTLER_ONLY = "15557770002@s.whatsapp.net"


def _db(tmp_path) -> Path:
    path = wa_archive.build(tmp_path / "whatsapp.db", NOW)
    con = sqlite3.connect(path)
    con.executemany("INSERT INTO contacts VALUES (?, ?, ?, ?)", [
        (STRANGER, None, "Stranger Push", NOW),
        (WROTE_TO, None, None, NOW),
        (BUTLER_ONLY, None, "Guest", NOW),
    ])
    con.executemany(
        "INSERT INTO messages (jid, id, from_me, ts, text) VALUES (?, ?, ?, ?, ?)",
        [(STRANGER, "s1", 0, NOW, "hi"),
         (WROTE_TO, "w1", 1, NOW, None),                     # a voice note
         (BUTLER_ONLY, wa.BUTLER_ID_PREFIX + "0011223344", 1, NOW, "Butler: x"),
         (BUTLER_ONLY, "old1", 1, NOW, "Butler: y" + wa.MARKER)])
    con.commit()
    con.close()
    return path


def test_known_is_a_saved_name_or_the_users_own_message(tmp_path):
    """The wanted answers sit between wrong ones: two push-name-only rows
    (from the fixture and here) and a chat only Butler wrote in are NOT
    known; saved names, a chat the user wrote in and a group are."""
    with wa.Archive(_db(tmp_path)) as a:
        assert a.is_known(wa_archive.BRUNO)          # saved "Bruno Lima"
        assert not a.is_known(wa_archive.CARLA)      # push name "Carla" only
        assert not a.is_known(STRANGER)
        assert a.is_known(WROTE_TO)
        assert not a.is_known(BUTLER_ONLY)
        assert a.is_known(wa_archive.FAMILY)         # a group: unchanged


def test_a_channel_target_by_push_name_is_refused(tmp_path):
    """The caller of `is_known` (D-18): a notify channel's `to`."""
    db = _db(tmp_path)
    assert wa.channel_target("Carla", db) == (
        "", "to is not a saved contact or group")
    assert wa.channel_target("Bruno", db) == (wa_archive.BRUNO, "")


def test_the_python_constants_equal_the_sidecars():
    js = (REPO / "daemon" / "engines" / "whatsapp" /
          "whatsapp-inbound.js").read_text(encoding="utf-8")
    assert re.search(r"BUTLER_ID_PREFIX = '(\w+)'", js).group(1) == \
        wa.BUTLER_ID_PREFIX
    assert re.search(r"MARKER = '\\u(\w{4})'", js).group(1) == \
        f"{ord(wa.MARKER):04x}"

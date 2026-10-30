"""The WhatsApp settings group (docs/messaging.md MS-5, section 6.1 keys).

Defaults are the spec's: off until the user links (MS-5), sending on
(MS-12), reading off (MS-8), questions on (MS-21), root chat and voice plus
text (MS-16, MS-18a), nobody answered (MS-14a), trigger "Butler". The group
declares its pairing route the way a group declares `collection`, so the
page renders it without naming it (AC-41, AC-503).
"""
import pytest

from daemon.settings import (BY_KEY, GROUPS, Settings, SettingsError)

EXPECTED = {
    "whatsapp_enabled": ("bool", False),
    "whatsapp_send": ("bool", True),
    "whatsapp_read": ("bool", False),
    "phone_questions": ("bool", True),
    "phone_chat": ("choice", "root"),
    "phone_reply": ("choice", "voice_text"),
    "whatsapp_answer_chats": ("list", []),
    "whatsapp_answer_all": ("bool", False),
    "whatsapp_group_trigger": ("text", "Butler"),
}


def test_the_group_holds_exactly_the_spec_keys_with_their_defaults():
    keys = {k for k, s in BY_KEY.items() if s.group == "whatsapp"}
    assert keys == set(EXPECTED)
    for key, (kind, default) in EXPECTED.items():
        assert BY_KEY[key].kind == kind, key
        assert BY_KEY[key].default == default, key
        assert not BY_KEY[key].restart, (
            f"{key} is restart-flagged; the owner applies every one of these "
            f"live (whatsapp.py)")
    assert BY_KEY["phone_chat"].choices == ("root", "current")
    assert BY_KEY["phone_reply"].choices == ("voice_text", "mirror", "text")


def test_the_group_declares_its_pairing_route_and_it_is_served(tmp_path):
    assert GROUPS["whatsapp"].pairing == "/api/whatsapp/pairing"
    served = {g["key"]: g for g in Settings(tmp_path / "s.json")
              .describe()["groups"]}
    assert served["whatsapp"]["pairing"] == "/api/whatsapp/pairing"
    # The other pairing groups are the phone link (docs/desktop-app.md
    # DA-15) and paired devices (docs/remote-access.md RA-8).
    assert all(g["pairing"] == "" for k, g in served.items()
               if k not in ("whatsapp", "devices", "access"))


def test_answer_list_entries_must_be_chat_ids(tmp_path):
    prefs = Settings(tmp_path / "s.json")
    ok = prefs.set("whatsapp_answer_chats",
                   [" 15551234567@s.whatsapp.net", "123-456@g.us"])
    assert ok == ["15551234567@s.whatsapp.net", "123-456@g.us"]
    # Not lowercased: an id is an identifier, not a phrase.
    assert prefs.set("whatsapp_answer_chats", ["ABC@lid"]) == ["ABC@lid"]
    assert prefs.set("whatsapp_answer_chats", []) == []
    with pytest.raises(SettingsError, match="chat id"):
        prefs.set("whatsapp_answer_chats", ["alice"])


def test_the_trigger_cannot_be_blank(tmp_path):
    prefs = Settings(tmp_path / "s.json")
    with pytest.raises(SettingsError):
        prefs.set("whatsapp_group_trigger", "   ")
    assert prefs.set("whatsapp_group_trigger", " Jarvis ") == "Jarvis"

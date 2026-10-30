"""WhatsApp: the second `Notifier` (docs/messaging.md MS-13, AC-516).

A channel file of kind `whatsapp` holds ONE field, `to`:

    {"kind": "whatsapp", "label": "My phone", "to": "me"}

`to` is `me` (the user's own chat, the default) or a saved contact or group.
A name or number is RESOLVED WHEN THE CHANNEL IS SAVED, against the archive
read-only, and stored as its chat id; a name matching several chats is
refused with the candidates named (the store's refuse-to-guess rule,
AC-509), and one that is not a saved contact or group is refused. "me" stays
"me" and is resolved per send from the linked account, so relinking does not
leave a channel pointing at an old number.

HOW THE ID GETS STORED. `notify.Channels.save` hands `problem` the very dict
it then writes, so `problem` writes the resolved id into `doc["to"]`. That
coupling is pinned by `test_whatsapp_send.py`, which reads the saved FILE.

ONE DOOR. Every send goes through `daemon.whatsapp.door().send`, the same
door as the loopback route and the agent tools, so the sidecar's guard, its
rate limit and its audit apply (MS-11, MS-13). Saving the channel names the
recipient on purpose, so a channel send carries `confirm: true`: that save
IS the user's confirmation for that one recipient.

INBOUND (docs/messaging.md MS-20..MS-22, AC-525). This kind declares the
optional inbound capability (`ports.Notifier`): `own()` is the user's OWN
chat, `ask` shows a coding agent's question there through the reply door
(own chat only, never the guarded send door), and `start` hooks the
existing `/events` reader (`WhatsApp.questions`) rather than opening a
second stream. WhatsApp has no buttons, so the question is numbered text
and a reply QUOTES it; what a reply means is `notify.Questions`'s.

SECRECY. `to` is SECRET (a chat id is a phone number): never served, and
`notify.py` scrubs it from every failure detail. No failure text here quotes
it; a refusal is the sidecar's reason code.
"""
from __future__ import annotations

from daemon import paths as paths_mod
from daemon import ports
from daemon import whatsapp as whatsapp_mod

_REASON_CAP = 200
REPLY_NUMBER = "Reply with the number, or reply to this message."
REPLY_TEXT = "Reply to this message with your answer."


def _one_line(text) -> str:
    return " ".join(str(text).split())[:_REASON_CAP]


@ports.register(ports.NOTIFIERS, "whatsapp")
class WhatsAppNotifier:
    LABEL = "WhatsApp"
    FIELDS = (
        {"key": "to", "label": "To", "secret": True,
         "help": "me for your own chat, or a saved contact or group by name, "
                 "number or chat id. A name is looked up once, when you "
                 "save, and kept as the chat id."},
    )

    @classmethod
    def problem(cls, doc: dict) -> str:
        """"" when `doc` can build a channel, else the rule it breaks. A name
        or number is resolved here and the chat id written back into `doc`,
        which is what `Channels.save` stores (see the module docstring).
        Blocking: the caller is already off the loop."""
        raw = doc.get("to")
        if raw is not None and not isinstance(raw, str):
            return "to must be text"
        target, problem = whatsapp_mod.channel_target(
            raw, paths_mod.resolve().whatsapp / "whatsapp.db")
        if problem:
            return problem
        doc["to"] = target
        return ""

    INBOUND = True

    @classmethod
    def own(cls, settings):
        """The user's own chat, or None while WhatsApp is off here."""
        if settings is None or not settings.get(whatsapp_mod.ENABLE_KEY):
            return None
        try:
            whatsapp_mod.door()
        except whatsapp_mod.Unavailable:
            return None
        return cls(whatsapp_mod.ME)

    def __init__(self, to: str):
        self._to = str(to).strip() or whatsapp_mod.ME
        self._hooked = None

    async def start(self, on_message) -> None:
        wa = whatsapp_mod.door()
        wa.questions = on_message
        self._hooked = (wa, on_message)

    async def stop(self) -> None:
        hooked, self._hooked = self._hooked, None
        if hooked is not None and hooked[0].questions is hooked[1]:
            hooked[0].questions = None

    async def ask(self, question: ports.Ask,
                  where: str) -> ports.NotifyResult:
        try:
            wa = whatsapp_mod.door()
            jid = await wa.own_jid()
            if jid is None:
                return ports.NotifyResult(False, "WhatsApp is not linked")
            ref = await wa.reply(jid, text=question_text(question, where))
        except whatsapp_mod.Unavailable as e:
            return ports.NotifyResult(False, _one_line(e.reason))
        except whatsapp_mod.ReplyFailed as e:
            return ports.NotifyResult(False, _one_line(e))
        if not ref:
            return ports.NotifyResult(
                False, "the WhatsApp service named no message id, so a "
                       "reply could not be matched to the question")
        return ports.NotifyResult(True, "", ref)

    async def send(self, title: str, text: str) -> ports.NotifyResult:
        body = f"{title}\n\n{text}" if title else text
        try:
            status, answer = await whatsapp_mod.door().send(
                {"recipient": self._to, "text": body, "confirm": True})
        except whatsapp_mod.Unavailable as e:
            return ports.NotifyResult(False, _one_line(e.reason))
        except whatsapp_mod.SendUncertain:
            return ports.NotifyResult(
                False, "the WhatsApp service did not answer; the message "
                       "may have been sent")
        if status == 200 and answer.get("ok"):
            return ports.NotifyResult(True, "")
        why = answer.get("reason") or answer.get("error") or "not sent"
        return ports.NotifyResult(False, f"{status} {_one_line(why)}")


def question_text(question: ports.Ask, where: str) -> str:
    """MS-22: "Butler needs you (<run or chat>): <question>", the numbered
    options of a select or confirm, then how to answer."""
    head = f"Butler needs you ({where}): " if where else "Butler needs you: "
    asked = "\n".join(x for x in (question.title, question.message) if x)
    lines = [head + asked]
    if question.method == "select":
        lines += [f"{i}) {o}" for i, o in enumerate(question.options, 1)]
    elif question.method == "confirm":
        lines += ["1) Yes", "2) No"]
    lines.append(REPLY_TEXT if question.method == "input" else REPLY_NUMBER)
    return "\n".join(lines)

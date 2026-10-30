"""Endpoint mode: any OpenAI-compatible server, streamed. plan.md 4.4, 7.5.

The second real `Brain` (AC-7a): `EndpointBrain` is the router from
`dialogue.py` in front and this file's conversation call behind it. Butler
ships NO default model -- both halves come from the `{conn, model}` pair the
owner chose and the connection that carries the key (4.4b) -- and no model
name appears anywhere in this file.

Two behaviours here are the substance rather than the plumbing.

**A reply is yielded sentence by sentence as it streams.** The first sentence
starts synthesizing while the rest is still being generated, which is what
`Brain.turn`'s async iterator is for and what keeps AC-4 reachable on a long
answer.

**A capped reply is visible, never silent, and never half-spoken (AC-40).**
Whole sentences are spoken; a fragment cut mid-sentence is shown and not
said; a reply capped to nothing at all speaks a fixed line, because silence
is indistinguishable from Butler ignoring the user. Every one of those paths
ends in a `Done`, so the hub's terminal path re-arms (D-1).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from typing import AsyncIterator

from .. import dialogue, llm, memory as memory_mod, models, ports, speech
from .. import schedule
from .. import library as library_mod
from .. import whatsapp as whatsapp_mod
from .. import guide as guide_mod
from ..ports import Done, Intent, Say, TurnContext

# Asked for, not enforced -- the difference matters (D-11). The cap is the
# enforcement; this is a request the model may ignore, and everything
# downstream is written as though it will.
REPLY_SYSTEM = """You are Butler, a voice assistant. Your answer is READ
ALOUD, so write plain spoken sentences: no markdown, no lists, no headings,
no code blocks, no emoji. Be brief -- a few sentences -- and say the useful
part first."""

#: What Butler says when the connection's own model list says the chosen model
#: takes no image (AC-204, words AC-816). It names where to fix it, because
#: "I cannot see that" does not say the fix is choosing another model.
CANNOT_SEE = ("The model this conversation uses cannot look at images. Choose "
              "one that can, under Brain.")
#: And when the endpoint itself refuses the request. The provider's own words
#: are appended, because "it did not work" is the report that sends somebody
#: reading logs.
REFUSED_IMAGE = "The model would not look at that: "
#: How many times one reply may stop to call a tool (AC-439). The LAST round
#: is sent with `tool_choice: "none"`, so a model that keeps calling is made
#: to answer rather than looping until the idle timeout.
MAX_TOOL_ROUNDS = 4


def _data_url(att) -> str:
    """One image as a data URL, which is how an OpenAI-compatible chat call
    carries one.

    Read from DISK rather than kept in memory across the turn: the store owns
    the bytes and this is the only place that needs them again, so a daemon
    holding a microphone does not also hold every photograph anybody attached.
    """
    import base64
    from pathlib import Path
    raw = Path(att.path).read_bytes()
    return (f"data:{att.media_type};base64,"
            + base64.b64encode(raw).decode("ascii"))


class EndpointConverse:
    """The substantive half of endpoint mode: one streamed completion.

    Not registered as a Brain on its own. It answers everything it is given,
    so running it WITHOUT the router would silently drop the routing the
    whole design is built on -- and a registry entry is an invitation to do
    exactly that.
    """

    # Both lines report what happened and offer no advice. They name no model:
    # which model to run is the owner's decision and Butler does not
    # second-guess it out loud (4.4b).
    OUT_OF_ROOM = "I ran out of room to answer."
    NOTHING_CAME_BACK = "I got nothing back."

    def __init__(self, resolver, client: llm.Client | None = None,
                 persona_for=None, memory=None, tasks=None,
                 whatsapp_tools=None, whatsapp_call=None, clock=None,
                 guide=None):
        self._resolver = resolver
        # `butler_help` (docs/butler-help.md BH-2, AC-648): an async
        # callable `(topic) -> text` the daemon builds, reading what is on
        # at the moment of the call. None offers no such tool.
        self._guide = guide
        # The local wall clock `clock_note` reads (AC-491): an AWARE local
        # datetime per call. A test injects one; the daemon's is the machine
        # zone, `schedule.local_zone()` (daemon/AGENTS.md).
        self._clock = clock if clock is not None else (
            lambda: datetime.now(schedule.local_zone()))
        self._client = client if client is not None else llm.Client()
        # The WhatsApp tools (docs/messaging.md MS-8, MS-18b; AC-508,
        # AC-562): `whatsapp_tools()` answers the tool definitions the
        # settings allow NOW (asked per request, so a switch reaches the
        # next turn) and `whatsapp_call(name, args, chat, turn)` runs one in
        # that turn of that conversation, answering the
        # text the model is shown. Both None offer nothing.
        self._wa_tools = whatsapp_tools
        self._wa_call = whatsapp_call
        # The library tools -- `task_add` (docs/automations.md AU-9,
        # AC-454), `reminder_add` and `schedule_propose` (docs/proactive.md
        # PR-20, PR-21): an async callable `(name, args, chat, turn) -> text`
        # the daemon builds (`hub.library_tool`), which decides the PROJECT
        # from the turn's conversation -- never from the model's arguments --
        # and caps a reply at three of each. None offers none of them.
        self._tasks = tasks
        # The memory store whose tools this brain may call
        # (docs/identity-memory.md 12.6, AC-439): the SAME `MemoryStore` the
        # hub writes "remember that" into and the routes serve (D-18). None
        # offers no tools at all, and then every request is byte-identical to
        # the one built before tools existed (AC-444).
        self._memory = memory
        # Who the owner is and who Butler is (docs/identity-memory.md 4.2,
        # AC-372): a callable answering the labelled system blocks for THIS
        # turn, `persona.Persona.blocks` in the daemon. A resolver, never the
        # settings store. None is a brain built without a daemon behind it --
        # the suite's direct constructions -- and adds nothing, which is also
        # exactly what a blank identity adds (AC-371).
        self._persona_for = persona_for

    async def _is_blind(self, target) -> bool:
        """Did the CONNECTION say this model takes no image? (AC-204)

        True only when the provider asserted it. False for "it said it can"
        AND for "it said nothing", which is most servers -- flattening the
        second into a refusal would stop Butler showing an image to a model
        that can perfectly well see one, and the honest answer for silence is
        to try and report what came back (docs/attachments.md 5.4).

        NEVER RAISES. A model list that cannot be read is not an answer about
        vision, and failing the turn because a listing endpoint was down would
        be worse than the wasted request (D-1).
        """
        try:
            entries = await models.fetch_model_entries(
                target.base_url, api_key=target.api_key)
        except Exception:                                 # noqa: BLE001
            return False
        entry = next((e for e in entries if e.get("id") == target.model), None)
        return models.sees_images(entry or {}) is False

    async def turn(self, text: str, ctx: TurnContext) -> AsyncIterator[Intent]:
        # AC-815. Images go to the model that answers; there is no separate
        # vision model.
        pictures = [a for a in (ctx.attachments or ()) if a.kind == "image"]
        target = self._resolver.resolve("reply")
        if pictures:
            blind = await self._is_blind(target)
            if blind:
                # Refused BEFORE the request, because the connection said so
                # (AC-204's first half). Said out loud and never swallowed: a
                # reply that answers as though it had seen the image is the
                # one failure here that produces a confident wrong answer
                # rather than an error.
                yield Say(CANNOT_SEE, tier="conversation")
                yield Done("conversation")
                return
        messages = [{"role": "system", "content": REPLY_SYSTEM}]
        # The owner's identity, each block its OWN labelled system message,
        # AFTER the speaking rules and BEFORE history (AC-372). Nothing at all
        # when blank, so the shipped request is byte-identical (AC-371).
        for block in (self._persona_for() if self._persona_for else ()):
            messages.append({"role": "system", "content": block})
        # What was SPOKEN, never what was generated (TurnContext.history,
        # D-20). Nothing fills this yet -- conversation memory is open
        # question 8.6 -- and honouring it here means wiring it later is one
        # change in the hub rather than two.
        messages += [{"role": role, "content": said}
                     for role, said in ctx.history]
        # The room, LABELLED, in its OWN message (AC-70, docs/ambient.md 4).
        # Its own message rather than folded into the utterance below, because
        # the utterance has to stay byte-for-byte what the owner said -- a
        # merged one files a remark made to somebody else as the request. And
        # nothing at all when nothing was overheard, which is almost every
        # turn: ambient ships off (AC-68) and an empty section would change
        # every request for a feature nobody switched on.
        note = dialogue.overheard_note(ctx)
        if note:
            messages.append({"role": "system", "content": note})
        # What was READ out of a document, labelled, in its own message
        # (AC-200, AC-202). The same split the room gets and for the same
        # reason: the utterance below stays byte-for-byte what the owner said,
        # and a document's text folded into it would be filed as theirs. The
        # PATH is deliberately not sent -- it means nothing to a model on
        # somebody else's machine, and `dialogue.attached_note` carries it for
        # the reader that can open files.
        read = [a for a in (ctx.attachments or ())
                if a.kind == "document" and a.text]
        if read:
            messages.append({"role": "system", "content":
                             dialogue.ATTACHED_HEADING + "\n\n"
                             + "\n\n".join(f"{a.name}:\n{a.text}"
                                           for a in read)})
        # The IMAGES ride on the user message as content parts, which is the
        # one shape an OpenAI-compatible chat call has for them -- so this is
        # the only place `content` is a list rather than a string, and a turn
        # with no picture builds the exact message it always did.
        if pictures:
            messages.append({"role": "user", "content":
                             [{"type": "text", "text": text}]
                             + [{"type": "image_url",
                                 "image_url": {"url": _data_url(a)}}
                                for a in pictures]})
        else:
            messages.append({"role": "user", "content": text})

        buffer = ""
        spoke = 0
        finish = None
        reasoning = False
        tools = ((memory_mod.TOOLS if self._memory is not None else ())
                 + (library_mod.TOOLS if self._tasks is not None else ())
                 + (tuple(self._wa_tools()) if self._wa_tools is not None
                    else ())
                 + (guide_mod.TOOLS if self._guide is not None else ()))
        tools = tools or None
        if self._tasks is not None:
            # The brain's clock, where `reminder_add` is offered (AC-491):
            # its own system message just BEFORE the user's, so it changes
            # nothing earlier in the request and the prefix cache over the
            # persona and the history survives a new minute.
            messages.insert(len(messages) - 1, {
                "role": "system", "content": dialogue.clock_note(
                    self._clock())})
        rounds = 0
        try:
            while True:
                rounds += 1
                last = tools is not None and rounds >= MAX_TOOL_ROUNDS
                stream = self._client.stream(
                    target, messages, turn_id=ctx.turn_id, tools=tools,
                    tool_choice="none" if last else None)
                calls = ()
                said = ""
                async for delta in stream:
                    if delta.finish_reason:
                        finish = delta.finish_reason
                        reasoning = delta.reasoning
                    if delta.tool_calls:
                        calls = delta.tool_calls
                    if not delta.text:
                        continue
                    buffer += delta.text
                    said += delta.text
                    # The shared splitter (daemon/speech.py). It moved out of
                    # this file when harness mode became the second caller:
                    # two copies of a sentence boundary drift the first time
                    # one is fixed (D-12).
                    sentences, buffer = speech.drain_sentences(buffer)
                    for sentence in sentences:
                        spoke += 1
                        yield Say(sentence, tier="conversation")
                if not calls or last:
                    break
                # The model asked for memory (AC-439): run each call OFF the
                # event loop (D-1 -- it is disk and SQLite), answer it in the
                # OpenAI shape, and ask again. What was already said stays
                # said; the next round continues the same reply.
                messages = messages + [{
                    "role": "assistant", "content": said or None,
                    "tool_calls": [{"id": c.id, "type": "function",
                                    "function": {"name": c.name,
                                                 "arguments": c.arguments}}
                                   for c in calls]}]
                for c in calls:
                    result = await self._answer_tool(c, ctx)
                    print(f"[butler] turn {ctx.turn_id}: tool {c.name} -> "
                          f"{result[:120]!r}", flush=True)
                    messages.append({"role": "tool", "tool_call_id": c.id,
                                     "content": result})
                finish = None
        except Exception as e:                            # noqa: BLE001
            # AC-204's SECOND half, and it only exists for a picture turn.
            # Most OpenAI-compatible servers say nothing about modalities, so
            # for most owners the pre-flight passes everything and the report
            # comes from here -- in the endpoint's OWN words, because "it did
            # not work" is the report that sends somebody reading logs. An
            # ordinary turn re-raises exactly as it did before, so nothing
            # about failure handling changed for the case that is not this.
            if not pictures:
                raise
            print(f"[butler] turn {ctx.turn_id}: the endpoint refused an "
                  f"image turn: {e!r}")
            if not spoke:
                yield Say(REFUSED_IMAGE + str(e), tier="conversation")
            yield Done("conversation")
            return

        # `rstrip`, NOT `strip`. The LEADING whitespace on the leftover is the
        # block separator `speech.drain_sentences` carried onto it, and it is
        # what puts the last block of a reply on its own line. Stripping it
        # welded the final list, fence or table onto the paragraph before it
        # with a joining space, and the page rendered that as prose
        # (2026-09-10). No emptiness guard is needed beside it: a buffer that
        # is only whitespace rstrips to "" on its own, so `if tail:` below
        # still sends a silent reply to the loud empty-reply branch. MEASURED
        # rather than reasoned -- an earlier version of this line carried a
        # `if buffer.strip()` guard and a test for it, and the test could not
        # fail because the guard could not matter.
        tail = buffer.rstrip()
        cap = target.max_tokens or 0
        if finish == "length":
            print(f"[butler] turn {ctx.turn_id}: reply cut short: "
                  f"finish_reason=length, cap={cap}, "
                  f"reasoning={'yes' if reasoning else 'no'}, "
                  f"spoke {spoke} sentence(s), "
                  f"fragment {len(tail)} chars")
            if tail:
                # Shown, not said: a half sentence read aloud sounds broken,
                # and hiding it would be Butler editing the answer (AC-40).
                yield Say(tail, tier="conversation", spoken=False)
            if not spoke:
                # Nothing complete arrived, so nothing was said. Silence is
                # indistinguishable from a dead microphone; say what happened.
                yield Say(dialogue.line(self.OUT_OF_ROOM, ctx.language),
                          tier="conversation")
            yield Done("conversation", truncated=True)
            return

        if tail:
            yield Say(tail, tier="conversation")
            spoke += 1
        if not spoke:
            # Not a cap and not an error: a 200 with nothing in it. Loud,
            # because it is the shape of a misconfigured model, and audible,
            # for the same reason as above.
            print(f"[butler] turn {ctx.turn_id}: the reply came back EMPTY "
                  f"(finish_reason={finish}, cap={cap}, "
                  f"reasoning={'yes' if reasoning else 'no'})")
            yield Say(dialogue.line(self.NOTHING_CAME_BACK, ctx.language),
                      tier="conversation")
        yield Done("conversation")

    async def _answer_tool(self, call, ctx: TurnContext) -> str:
        """One call, routed: a WhatsApp tool is async (it may go through the
        send door), everything else is disk and SQLite, run OFF the event
        loop (D-1)."""
        if call.name in whatsapp_mod.TOOL_NAMES and self._wa_call is not None:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError as e:
                return f"Error: the arguments were not valid JSON ({e})."
            # The TURN's conversation and id, so a held send is asked of
            # the owner in this turn (docs/messaging.md 6.5 RV-2, AC-588).
            return await self._wa_call(call.name, args, ctx.chat,
                                       ctx.turn_id)
        if call.name in guide_mod.TOOL_NAMES and self._guide is not None:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError as e:
                return f"Error: the arguments were not valid JSON ({e})."
            topic = args.get("topic") if isinstance(args, dict) else None
            return await self._guide(topic if isinstance(topic, str) else None)
        if call.name in library_mod.TOOL_NAMES and self._tasks is not None:
            # task_add, reminder_add, schedule_propose: handed the TURN's
            # conversation and id, never a project; the daemon's one
            # dispatcher (`hub.library_tool`) decides the rest.
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError as e:
                return f"Error: the arguments were not valid JSON ({e})."
            return await self._tasks(call.name, args, ctx.chat, ctx.turn_id)
        return await asyncio.to_thread(self._run_tool, call, ctx)

    def _run_tool(self, call, ctx: TurnContext) -> str:
        """One memory tool call, answered as text. Arguments that are not a
        JSON object are MODEL OUTPUT and are answered, not raised (plan.md
        3.8); `MemoryStore.call_tool` does the same for a refused write."""
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as e:
            return f"Error: the arguments were not valid JSON ({e})."
        if self._memory is None:
            return f"Error: there is no tool called {call.name!r}."
        return self._memory.call_tool(call.name, args)

    async def abort(self, turn_id: int) -> None:
        await self._client.abort(turn_id)

    async def warm(self) -> None:
        """One token, to pay the load at boot instead of mid-conversation.

        Measured on this machine: 24.6 s cold for the 4B and 108.5 s for the
        9B, against 15-49 ms once resident (plan.md 4.4). A warm failure is
        logged by `butlerd.warmup` and never fatal (D-1).
        """
        target = replace(self._resolver.resolve("reply"), max_tokens=1)
        await self._client.complete(
            target, [{"role": "user", "content": "hello"}], turn_id=0)

    @property
    def info(self) -> dict:
        return {"engine": "endpoint"}


class GuestCall:
    """ONE completion for a WhatsApp GUEST (docs/messaging.md MS-14b, AC-536).

    The guest path's only way to a model, and deliberately not a `Brain`: no
    router, no persona resolver, no memory store, no library or WhatsApp
    tools, no harness. It is `llm.Client.complete`, which has NO tools
    parameter, so the request it sends cannot carry a `tools` key however it
    is called. The caller hands over every message the model sees (SOUL.md
    and the fixed guest rule, that guest's own recent exchange, the text);
    nothing is added here.

    The model is the `reply` role (`endpoint_model`) in EVERY brain mode: a
    harness child would load the owner's pi skills and extensions, which
    this request must never see. `resolve` raises `ConnectionError_` when
    `endpoint_model` names no connection; the caller answers the guest with
    a fixed line and logs why.
    """

    def __init__(self, resolver, client: llm.Client | None = None):
        self._resolver = resolver
        self._client = client if client is not None else llm.Client()
        # Negative ids: the client's abort registry is keyed by turn id, and
        # no hub turn is ever negative.
        self._next = 0

    async def __call__(self, system: tuple, history: list, text: str) -> str:
        target = self._resolver.resolve("reply")
        messages = ([{"role": "system", "content": s} for s in system if s]
                    + [{"role": m["role"], "content": m["content"]}
                       for m in history]
                    + [{"role": "user", "content": text}])
        self._next -= 1
        done = await self._client.complete(target, messages,
                                           turn_id=self._next)
        return done.text


@ports.register(ports.BRAINS, "endpoint")
class EndpointBrain(dialogue.Dialogue):
    """Router in front, an OpenAI-compatible endpoint behind (4.4 mode 2)."""

    ENGINE = "endpoint"
    # Read by butlerd at construction: this adapter needs the one
    # TargetResolver the daemon builds from Settings plus the
    # ConnectionStore. A capability flag rather than a name test, for the
    # same reason resume is (AC-35): the next brain that needs one adds this
    # line and no call site changes.
    NEEDS_TARGETS = True
    # And the persona resolver (docs/identity-memory.md 4.2), by capability
    # for the same reason. Only the CONVERSATION half is handed it: the router
    # is shown neither the identity nor the memory (4.4).
    NEEDS_PERSONA = True
    # And the memory store, for its two tools (docs/identity-memory.md 12.6).
    # Only the CONVERSATION half: the router is shown no tools.
    NEEDS_MEMORY = True
    # And `task_add` (docs/automations.md AU-9), offered exactly where the
    # memory tools are: the conversation half only.
    NEEDS_TASKS = True
    # And the WhatsApp tools (docs/messaging.md MS-8, MS-18b), beside the
    # library's: two callables, never the settings store.
    NEEDS_WHATSAPP = True
    # And `butler_help` (docs/butler-help.md BH-2), where the memory tools
    # are: one callable reading the live state per call.
    NEEDS_GUIDE = True

    def __init__(self, resolver, client: llm.Client | None = None,
                 persona_for=None, memory=None, tasks=None,
                 whatsapp_tools=None, whatsapp_call=None, clock=None,
                 guide=None):
        # ONE client for both halves, so `abort(turn)` closes whichever of
        # them is holding a request -- and there is exactly one place that
        # knows what is in flight.
        shared = client if client is not None else llm.Client()
        super().__init__(router=dialogue.Router(resolver, client=shared),
                         converse=EndpointConverse(resolver, client=shared,
                                                   persona_for=persona_for,
                                                   memory=memory,
                                                   tasks=tasks,
                                                   whatsapp_tools=whatsapp_tools,
                                                   whatsapp_call=whatsapp_call,
                                                   clock=clock,
                                                   guide=guide))

"""The OpenAI chat-completions wire, in one place. plan.md 4.4, 4.4b.

Both of Butler's OWN brains speak it: the router asks for one
schema-constrained verdict (`dialogue.py`) and endpoint mode streams a reply
(`adapters/brain_endpoint.py`). It is not a port -- an engine behind the
`Brain` port may speak anything at all -- and it is not `models.py`, which
reads a catalogue and never generates.

Three rules this module exists to keep honest.

**The cap is on the WIRE or it is nowhere (AC-38).** `max_tokens` comes from
`ResolvedTarget` and is written into the request body. Zero and None both
mean the key is ABSENT: a literal `max_tokens: 0` is a different instruction,
and a server that honoured it would answer nothing at all.

**`finish_reason` is carried back, never swallowed.** "The model finished"
and "the model was cut off at your cap" are different events with different
handling (AC-39, AC-40), and this field is the only thing that tells them
apart.

**An abort must reach the SOCKET (D-4, AC-43).** Cancelling the reader leaves
the server generating a reply nobody will hear, and the next turn queues
behind it -- measured at 7887 ms on the TTS side of the same shape. So calls
are registered by turn and `abort(turn)` closes them.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import AsyncIterator

import aiohttp

from .connections import ResolvedTarget, api_url

# Connect fast or say so: an unreachable connection is an infrastructure
# failure and must not look like a slow model.
CONNECT_TIMEOUT_S = 10.0
# Silence tolerated BETWEEN reads, not a budget for the whole call. A cold
# load on this machine is 24.6 s for the 4B and 108.5 s for the 9B [MEASURED,
# plan.md 4.4], and a swapping connection pays one on the first request after
# an eviction. A total timeout sized for that would also cut off a long reply
# that is streaming perfectly well, which is why this is per-read.
# 600 s since 2026-09-26 (AC-387, docs/turn-feedback.md 3): 180 s left 71 s
# over a 9B cold load, and a local model also PREFILLS a long prompt before
# its first byte. Waiting long costs nothing now that the page shows how long
# a turn has been quiet and offers Stop; cutting a working model off does.
READ_TIMEOUT_S = 600.0


class LlmError(RuntimeError):
    """The endpoint could not be reached or refused the request.

    Deliberately NOT the same thing as a verdict that failed to parse.
    plan.md 3.8's exception to fail-fast covers bad MODEL OUTPUT from a
    healthy server; an absent, dead or refusing server is infrastructure and
    fails loudly (docs/reflex-serving.md section 6 names this exact
    temptation).
    """


class Aborted(RuntimeError):
    """This call was closed by `Client.abort` because its turn was abandoned."""


@dataclass(frozen=True)
class Completion:
    """One whole answer, read at once."""
    text: str
    finish_reason: str | None
    reasoning: bool = False
    """True when the server sent a `reasoning_content` block.

    Worth carrying because of a measured failure: a reasoning model under a
    small cap spends the entire cap thinking and returns EMPTY content, 10
    times out of 10 (plan.md 4.4). "Empty because it was still thinking" and
    "empty because it broke" want the same handling and a different log line.
    """


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for, assembled from its fragments.
    `arguments` is the raw JSON text the model wrote; parsing it is the
    caller's, because a malformed one is model output to answer, not a
    broken server (plan.md 3.8)."""
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Delta:
    """One piece of a streamed answer. The last one carries the finish, and
    the tool calls when the model asked for any (docs/identity-memory.md
    12.6, AC-439)."""
    text: str = ""
    finish_reason: str | None = None
    reasoning: bool = False
    tool_calls: tuple = ()


def _headers(target: ResolvedTarget) -> dict:
    return ({"Authorization": f"Bearer {target.api_key}"}
            if target.api_key else {})


def _body(target: ResolvedTarget, messages: list[dict], *, stream: bool,
          response_format: dict | None = None, tools=None,
          tool_choice: str | None = None) -> dict:
    body: dict = {"messages": messages, "stream": stream}
    # A blank model means "the connection's own default" (plan.md 4.4), so the
    # key is omitted rather than sent empty: `"model": ""` is a name, and a
    # server matching on it answers 404 for a model nobody asked for.
    if target.model:
        body["model"] = target.model
    if target.max_tokens:
        body["max_tokens"] = target.max_tokens
    if not target.reasoning:
        # Off means "skip the reasoning block". A reasoning model streams
        # nothing but thought for 30-68 s and the router returns an EMPTY
        # verdict [MEASURED Ornith-1.0-9B 2026-08-18]; this is the Qwen-family
        # signal to answer at once. Only sent when the owner turned it off, so
        # a target with reasoning left on is a byte-identical request (4.4b) --
        # a cloud connection that would reject the kwarg never sees it.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if response_format:
        body["response_format"] = response_format
    # Only when a caller offers tools, so every request that offers none is
    # byte-identical to the one built before tools existed (AC-444).
    if tools:
        body["tools"] = list(tools)
        if tool_choice:
            body["tool_choice"] = tool_choice
    return body


class Client:
    """One brain's HTTP calls, with barge-in able to reach the socket.

    Held by an adapter, never shared with the daemon core: it knows an engine
    protocol, which is exactly what a port exists to keep out of `hub.py`.
    """

    def __init__(self, connect_timeout_s: float = CONNECT_TIMEOUT_S,
                 read_timeout_s: float = READ_TIMEOUT_S):
        self._timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=connect_timeout_s,
            sock_read=read_timeout_s)
        self._open: dict[int, set] = {}
        # Bounded on purpose. A turn id lands here so a read that fails
        # BECAUSE we closed it can be told from one that failed on its own,
        # and a set that only grew would be a slow leak in a daemon that is
        # meant to run for weeks.
        self._recently_aborted: deque[int] = deque(maxlen=64)

    # -- the two calls ------------------------------------------------------ #
    async def complete(self, target: ResolvedTarget, messages: list[dict], *,
                       turn_id: int,
                       response_format: dict | None = None) -> Completion:
        """Ask once, read the whole answer. Used for the router's verdict."""
        url = api_url(target.base_url, "chat/completions")
        body = _body(target, messages, stream=False,
                     response_format=response_format)
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, json=body,
                                        headers=_headers(target)) as response:
                    self._register(turn_id, response)
                    try:
                        if response.status != 200:
                            text = (await response.text())[:300]
                            raise LlmError(
                                f"{url} answered {response.status}: {text}")
                        payload = await response.json(content_type=None)
                    finally:
                        self._forget(turn_id, response)
        except LlmError:
            raise
        except Exception as e:                            # noqa: BLE001
            raise self._translate(turn_id, url, e) from e
        return _completion_from(payload)

    async def stream(self, target: ResolvedTarget, messages: list[dict], *,
                     turn_id: int, tools=None,
                     tool_choice: str | None = None) -> AsyncIterator[Delta]:
        """Yield the answer as it arrives. Used for a spoken reply.

        Streaming is not an optimisation here: the first sentence starts
        synthesizing while the rest is still being generated, which is the
        whole reason `Brain.turn` yields instead of returning.
        """
        url = api_url(target.base_url, "chat/completions")
        body = _body(target, messages, stream=True, tools=tools,
                     tool_choice=tool_choice)
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, json=body,
                                        headers=_headers(target)) as response:
                    self._register(turn_id, response)
                    try:
                        if response.status != 200:
                            text = (await response.text())[:300]
                            raise LlmError(
                                f"{url} answered {response.status}: {text}")
                        async for delta in _read_sse(response):
                            yield delta
                    finally:
                        self._forget(turn_id, response)
        except LlmError:
            raise
        except Exception as e:                            # noqa: BLE001
            raise self._translate(turn_id, url, e) from e

    # -- barge-in ----------------------------------------------------------- #
    async def abort(self, turn_id: int) -> None:
        """Close whatever this turn has in flight. Unknown turn: a no-op.

        Barge-in races completion by nature, so a raise here would turn an
        ordinary race into a failed abandon -- which is a held microphone
        (D-1).
        """
        self._recently_aborted.append(turn_id)
        for response in self._open.pop(turn_id, set()):
            response.close()

    def _register(self, turn_id: int, response) -> None:
        self._open.setdefault(turn_id, set()).add(response)

    def _forget(self, turn_id: int, response) -> None:
        live = self._open.get(turn_id)
        if live is None:
            return
        live.discard(response)
        if not live:
            self._open.pop(turn_id, None)

    def _translate(self, turn_id: int, url: str, e: Exception) -> Exception:
        """A read that failed because WE closed it is not a server failure."""
        if turn_id in self._recently_aborted:
            return Aborted(f"turn {turn_id} was aborted mid-request")
        return LlmError(f"{url} could not be read: {e!r}")


def _completion_from(payload: dict) -> Completion:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LlmError(f"no choices in the answer: {str(payload)[:200]}")
    first = choices[0] or {}
    message = first.get("message") or {}
    return Completion(text=str(message.get("content") or ""),
                      finish_reason=first.get("finish_reason"),
                      reasoning=bool(message.get("reasoning_content")))


async def _read_sse(response) -> AsyncIterator[Delta]:
    """Server-sent events, one `data:` line at a time.

    Tolerant of the two things every server does differently -- keep-alive
    comments and a trailing `[DONE]` -- and intolerant of malformed JSON,
    which is a broken server rather than a slow one.
    """
    reasoning_seen = False
    # Tool calls arrive in FRAGMENTS keyed by `index`: the id and name once,
    # the arguments a few characters at a time (the OpenAI streaming shape).
    # They are handed over whole, on the finishing delta.
    calls: dict[int, dict] = {}
    handed = False

    def assembled() -> tuple:
        return tuple(ToolCall(c["id"], c["name"], c["arguments"])
                     for _, c in sorted(calls.items()))

    async for raw in response.content:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as e:
            raise LlmError(f"malformed stream frame {data[:120]!r}: {e}")
        choices = payload.get("choices") or []
        if not choices:
            continue
        first = choices[0] or {}
        delta = first.get("delta") or {}
        if delta.get("reasoning_content"):
            reasoning_seen = True
        for frag in delta.get("tool_calls") or ():
            slot = calls.setdefault(int(frag.get("index") or 0),
                                    {"id": "", "name": "", "arguments": ""})
            fn = frag.get("function") or {}
            slot["id"] = frag.get("id") or slot["id"]
            slot["name"] = fn.get("name") or slot["name"]
            slot["arguments"] += fn.get("arguments") or ""
        text = delta.get("content") or ""
        finish = first.get("finish_reason")
        if text or finish:
            handed = handed or bool(finish and calls)
            yield Delta(text=str(text), finish_reason=finish,
                        reasoning=reasoning_seen,
                        tool_calls=assembled() if finish else ())
    if calls and not handed:
        # A server that ended the stream without a finishing frame still
        # asked for the calls; dropping them would answer nothing.
        yield Delta(finish_reason="tool_calls", reasoning=reasoning_seen,
                    tool_calls=assembled())

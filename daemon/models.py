"""What a target actually serves. plan.md 4.4, AC-30.

NOT a port (4.10 caps them at five, and the fifth is `Harness`). This is a
small read-only client:
given the base URL of an OpenAI-compatible server, ask it what models it has.

Why the daemon asks instead of the page holding a list. llama-swap serves 130
models on this machine and its catalogue changes without Butler being told, so
a list written into `daemon/settings.py` is wrong the first time someone edits
a config file somewhere else -- and wrong INVISIBLY: the picker still shows the
old name, the daemon still saves it, and the endpoint answers 404 on the next
thing the owner says. The settings page names no model for the same reason it
names no setting.

FAIL LOUD, AND KEEP THE TWO FAILURES APART. "Nobody answered" and "answered
with an empty catalogue" are different sentences and only one of them is worth
interrupting someone for. Returning `[]` for a dead endpoint would draw an
empty picker and claim everything was fine, so this module raises instead.
"""
from __future__ import annotations

import time

import aiohttp

from .connections import api_url

# Generous, because the target may be busy generating a reply for the turn the
# owner is in. This budget is for a metadata read, not for inference.
DEFAULT_TIMEOUT_S = 10.0
# How long a catalogue is trusted. The panel builds one picker per model
# setting and every ask is a round trip to a server that is also answering
# turns, so this is not an optimisation -- it is what stops opening the
# settings page from competing with the thing you just said.
DEFAULT_TTL_S = 60.0


class ModelsUnavailable(RuntimeError):
    """The target could not be asked, or did not answer like a model server."""


async def fetch_models(base_url: str,
                       timeout_s: float = DEFAULT_TIMEOUT_S,
                       api_key: str | None = None) -> list[str]:
    """Model ids served at `base_url`, in the order the server gave them.

    Order is preserved rather than sorted: a server that puts its default or
    its loaded model first is telling you something, and alphabetising throws
    that away for a tidiness nobody asked for.
    """
    # Both base-URL conventions are handled in ONE place, beside the thing
    # that owns what a base URL is (connections.api_url). Normalising, not
    # falling back: one request, and which path it goes to is decided before
    # it is sent. A fallback would try one then the other, turning a 404 into
    # two round trips and hiding which URL was wrong.
    url = api_url(base_url, "models")
    # The whole reason connections resolve a key (plan.md 4.4b). Without this
    # the key was computed and discarded, and every authenticated provider --
    # which is all three cloud ones -- came back 502 "could not be read".
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    body = (await response.text())[:200]
                    raise ModelsUnavailable(
                        f"{url} answered {response.status}: {body}")
                payload = await response.json(content_type=None)
    except ModelsUnavailable:
        raise
    except Exception as e:                            # noqa: BLE001
        # aiohttp raises a family of these and the distinction between "no
        # route to host" and "malformed JSON" does not change what the owner
        # has to do about it. What matters is that it is not silence.
        raise ModelsUnavailable(f"{url} could not be read: {e!r}") from e

    # Anything can answer on a port. Only an answer SHAPED like /v1/models
    # counts, or a URL pointing at the wrong service quietly becomes "this
    # endpoint has no models".
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ModelsUnavailable(
            f"{url} did not answer like an OpenAI model list: "
            f"{str(payload)[:200]}")
    ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
    if not ids:
        raise ModelsUnavailable(f"{url} listed no usable model ids")
    return ids


# AC-204. Where a provider says what a model can be SHOWN. There is no
# standard for this -- `/v1/models` in the OpenAI shape carries an id and
# little else -- so these are the field names providers that bother have
# chosen, read from their own answers. A model list that says NOTHING is the
# normal case and is why this returns None rather than False.
_MODALITY_KEYS = ("input_modalities", "input_modality", "modalities",
                  "supported_input_modalities")


def sees_images(entry: dict) -> bool | None:
    """Does this `/v1/models` entry say it accepts an image? None = it did not say.

    THREE-VALUED ON PURPOSE (AC-204, docs/attachments.md 5.4). True and False
    are things the provider asserted; None means nobody said, which is most
    servers, and a caller that flattened None to False would refuse to show an
    image to a model that can perfectly well see one. The honest thing for None
    is to TRY and report what came back.

    There is deliberately no table of model NAMES here. A hardcoded list of
    which models can see is exactly the kind of list that was wrong by a factor
    of two and a half when `Tts.info` carried one (AC-42), and this one would
    go stale every time a provider shipped anything.
    """
    if not isinstance(entry, dict):
        return None
    for holder in (entry, entry.get("architecture") or {}):
        if not isinstance(holder, dict):
            continue
        for key in _MODALITY_KEYS:
            said = holder.get(key)
            if isinstance(said, str):
                said = [said]
            if isinstance(said, (list, tuple)) and said:
                return any(str(m).lower().startswith("image") for m in said)
    return None


async def fetch_model_entries(base_url: str,
                              timeout_s: float = DEFAULT_TIMEOUT_S,
                              api_key: str | None = None) -> list[dict]:
    """The `/v1/models` entries WHOLE, for the one caller that needs more than
    an id (AC-204).

    `fetch_models` above answers ids and is what every picker wants; this is
    the same request keeping the metadata, so `sees_images` has something to
    read. Kept as a second function rather than widening the first: 130 model
    dicts are not what a dropdown needs, and the `Catalogue` that memoises the
    id list is the hot path.
    """
    url = api_url(base_url, "models")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    raise ModelsUnavailable(
                        f"{url} answered {response.status}")
                payload = await response.json(content_type=None)
    except ModelsUnavailable:
        raise
    except Exception as e:                            # noqa: BLE001
        raise ModelsUnavailable(f"{url} could not be read: {e!r}") from e
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ModelsUnavailable(
            f"{url} did not answer like an OpenAI model list")
    return [m for m in data if isinstance(m, dict)]


class Catalogue:
    """Model lists by base URL, remembered briefly.

    Deliberately has no invalidation hook. A stale entry costs at most one TTL
    of showing a model that has just appeared; the alternative -- a cache the
    rest of the daemon has to remember to clear -- is a cache that is wrong in
    the direction nobody notices.
    """

    def __init__(self, ttl_s: float = DEFAULT_TTL_S):
        self.ttl_s = ttl_s
        self.fetches = 0            # asserted by the tests; also a real metric
        self._entries: dict[str, tuple[float, list[str]]] = {}

    async def models(self, base_url: str,
                     timeout_s: float = DEFAULT_TIMEOUT_S,
                     api_key: str | None = None) -> list[str]:
        """Cached by base_url ALONE, deliberately, even though the key varies.

        Putting the key in the cache key would mean this long-lived object
        held a secret for the life of the process, and the rule is that a key
        is read at call time and dropped (plan.md 4.4b, AC-37). The cost is
        that two connections sharing one base URL with different keys would
        share a model list; pi gives every provider its own base URL, so that
        configuration does not arise here, and correctness of a CATALOGUE is
        worth less than not retaining a secret.
        """
        hit = self._entries.get(base_url)
        if hit is not None and (time.monotonic() - hit[0]) < self.ttl_s:
            return hit[1]
        # A failure is NOT cached: the endpoint being down is the state most
        # likely to change in the next few seconds, and caching it would make
        # the page keep saying so after the owner had fixed it.
        found = await fetch_models(base_url, timeout_s=timeout_s,
                                   api_key=api_key)
        self.fetches += 1
        self._entries[base_url] = (time.monotonic(), found)
        return found

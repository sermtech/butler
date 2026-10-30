"""What models a target actually serves. AC-30, plan.md 4.4.

The settings page must not carry a list of model names. llama-swap alone
serves 130 on this machine and the set changes without Butler being told, so a
hardcoded list is wrong the first time it is edited and the drift is invisible:
you pick a model, the daemon saves it, and the endpoint answers 404 on the next
thing you say. So the daemon asks the target and the page renders the answer.

The failure that matters most here is the QUIET one. An endpoint that is down
must not read as an endpoint with no models -- those are different sentences,
and only one of them is worth waking someone up for.
"""
import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import test_utils, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daemon import models                                      # noqa: E402

SERVED = ["bartowski/Ornith-1.0-9B-Q5_K_L", "LiquidAI/LFM2-2.6B",
          "bartowski/Qwen3.5-4B-Q8_0"]


@pytest_asyncio.fixture
async def stub_endpoint():
    """An OpenAI-compatible /v1/models, and nothing else.

    Deliberately not llama-swap: a test that needs the owner's model server
    running is a test that fails for reasons that have nothing to do with the
    code (D-13). Built on aiohttp's own TestServer rather than the
    `aiohttp_server` fixture, so the suite does not gain a plugin dependency
    for four tests.
    """
    async def list_models(_request):
        return web.json_response(
            {"object": "list",
             "data": [{"id": m, "object": "model"} for m in SERVED]})

    app = web.Application()
    app.router.add_get("/v1/models", list_models)
    server = test_utils.TestServer(app)
    await server.start_server()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_the_models_are_read_from_the_endpoint(stub_endpoint):
    assert await models.fetch_models(stub_endpoint) == SERVED


@pytest.mark.asyncio
async def test_an_endpoint_that_is_down_raises_rather_than_returning_nothing():
    """The quiet failure this file exists for.

    Returning [] would render an empty picker, which says "this endpoint has
    no models". The truth is "nobody answered", and the two need different
    reactions from whoever is looking at the page (fail fast, no fallback).
    """
    with pytest.raises(models.ModelsUnavailable):
        # Port 1 is reserved and nothing listens there.
        await models.fetch_models("http://127.0.0.1:1", timeout_s=2.0)


@pytest.mark.asyncio
async def test_a_malformed_answer_is_not_mistaken_for_an_empty_catalogue():
    """Anything can answer on a port. Only /v1/models shaped like /v1/models
    counts, or a misconfigured URL silently becomes "no models here"."""
    async def wrong_shape(_request):
        return web.json_response({"models": ["nope"]})

    app = web.Application()
    app.router.add_get("/v1/models", wrong_shape)
    async with test_utils.TestServer(app) as server:
        with pytest.raises(models.ModelsUnavailable):
            await models.fetch_models(f"http://127.0.0.1:{server.port}")


@pytest.mark.asyncio
async def test_the_catalogue_is_cached_so_the_page_cannot_hammer_it(
        stub_endpoint):
    """The panel asks once per picker and the schema has several of them.

    Every ask is a round trip to a model server that is also serving replies,
    so a cached answer is not an optimisation -- it is what stops opening
    settings from competing with the turn you are in.
    """
    cache = models.Catalogue(ttl_s=60.0)
    first = await cache.models(stub_endpoint)
    second = await cache.models(stub_endpoint)
    assert first == second == SERVED
    assert cache.fetches == 1, f"asked the endpoint {cache.fetches} times"


@pytest.mark.asyncio
async def test_a_stale_cache_is_refreshed(stub_endpoint):
    cache = models.Catalogue(ttl_s=0.0)
    await cache.models(stub_endpoint)
    await cache.models(stub_endpoint)
    assert cache.fetches == 2


# --------------------------------------------------------------------------- #
# over HTTP -- the caller, not just the helper (D-18)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_api_serves_what_a_connection_actually_has(
        isolated_daemon, stub_endpoint):
    """Point a CONNECTION at a known endpoint and read the route.

    Was `target=endpoint` against the single `endpoint_url` setting, which is
    retired: everything remote is a connection now, because two ways to name
    a server is the drift connections replaced (plan.md 4.4b).

    Runs against the ISOLATED daemon, not the session one. Adding a
    connection writes `state/connections.json`, and the session daemon owns
    the OWNER's state directory.
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/connections", json={
                "id": "stub", "label": "stub",
                "base_url": stub_endpoint,
                "auth": {"from": "none"}}) as r:
            # 400 on a re-run: the module-scoped daemon keeps the
            # connection from the first test in this file that added it.
            assert r.status in (200, 400), await r.text()
        async with http.get(base + "/api/models?connection=stub") as r:
            assert r.status == 200, await r.text()
            body = await r.json()

    assert body["models"] == SERVED, (
        f"the route must serve what the connection has, got {body}")


@pytest.mark.asyncio
async def test_a_dead_connection_is_a_loud_answer_not_an_empty_list(
        isolated_daemon):
    """A connection nobody answers gets a 502 carrying what went wrong.

    An empty 200 would draw an empty picker and say nothing was wrong --
    "nobody answered" and "answered with no models" are different sentences
    and only one is worth interrupting someone for (daemon/models.py).
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        # llama-swap in the fixture registry points at port 9, which nothing
        # listens on: a real dead endpoint rather than a mocked failure.
        async with http.get(base + "/api/models?connection=llama-swap") as r:
            assert r.status == 502, (
                f"a dead connection answered {r.status}, not a loud 502")
            body = await r.json()
    assert body.get("error"), f"the 502 carried no reason: {body}"


@pytest.mark.asyncio
async def test_a_local_target_never_touches_the_network(isolated_daemon):
    """A local target must not go through the fetch path at all.

    The isolated daemon's registry points every connection at something
    unreachable, so if `tts_voices` ever fell through to the catalogue it
    would fail here -- and the voice picker would break whenever the model
    endpoint was down, two unrelated things failing together.
    """
    base, _state, _registry = isolated_daemon
    from daemon.adapters.tts_fake import FAKE_VOICES

    async with aiohttp.ClientSession() as http:
        async with http.get(base + "/api/models?target=tts_voices") as r:
            assert r.status == 200, await r.text()
            body = await r.json()
    # NAMES, not entries: the target serves what the picker renders, and
    # `voice_ids` is the one place that knows an entry carries a language
    # beside its id (AC-155).
    assert body["models"] == [v["id"] for v in FAKE_VOICES], body


@pytest.mark.asyncio
async def test_the_harness_model_list_comes_from_the_harness_itself(
        isolated_daemon):
    """AC-30 for `harness_model`, whose list no connection can serve.

    A harness model lives in the HARNESS's own registry -- pi reads its
    models.json and prints a table -- so this target asks the configured
    adapter rather than an endpoint, and the daemon never learns the lister's
    vocabulary (AC-41). Pointed at the fake harness so the WIRING is tested on
    every machine: the real lister needs pi installed, and a picker that never
    receives its list is the silent half of AC-7b.
    """
    base, _state, _registry = isolated_daemon
    from daemon.adapters.harness_fake import FAKE_MODELS

    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/settings",
                             json={"harness": "fake"}) as r:
            assert r.status == 200, await r.text()
        try:
            async with http.get(base + "/api/models?target=harness_models") as r:
                assert r.status == 200, await r.text()
                body = await r.json()
        finally:
            await http.post(base + "/api/settings", json={"harness": "pi"})

    assert body["models"] == list(FAKE_MODELS), body


@pytest.mark.asyncio
async def test_an_unknown_harness_is_a_loud_502_not_an_empty_picker(
        isolated_daemon):
    """The two failures models.py keeps apart, one seam further out.

    A picker drawn empty says nothing was wrong; a 502 carrying the reason is
    what puts a red note in the control the owner is looking at.
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/settings",
                             json={"harness": "no-such-harness"}) as r:
            assert r.status == 200, await r.text()
        try:
            async with http.get(base + "/api/models?target=harness_models") as r:
                assert r.status == 502, await r.text()
                body = await r.json()
        finally:
            await http.post(base + "/api/settings", json={"harness": "pi"})
    assert "no-such-harness" in body["error"], body


@pytest.mark.asyncio
async def test_a_harness_that_cannot_list_its_models_is_not_a_bare_500(
        tmp_path, monkeypatch):
    """The WORLD's failure at this target, which had no path of its own.

    `PiHarness.list_models` raises `RuntimeError` for every real-world failure
    it has -- pi missing, the 60 s timeout spent, an empty registry, a table
    whose shape changed -- and the handler catches only `LocalListUnavailable`.
    So the one failure most likely to actually happen escaped as a bare 500
    with no `error` key at all, and the picker showed "HTTP 500" instead of the
    reason. models.py's rule, one seam out: "nobody answered" and "answered
    with nothing" are different sentences, and the owner can only act on the
    first if they are told which it was.

    The translation is what is asserted here; that a `LocalListUnavailable`
    becomes a 502 carrying its message is already proved over the wire by
    `test_an_unknown_harness_is_a_loud_502_not_an_empty_picker`.
    """
    from types import SimpleNamespace

    from daemon import butlerd, ports
    from daemon.settings import Settings

    class Unreachable:
        @classmethod
        async def list_models(cls):
            raise RuntimeError("pi did not list its models within 60s")

    monkeypatch.setitem(ports.HARNESSES, "unreachable", Unreachable)
    settings = Settings(tmp_path / "settings.json")
    settings.set("harness", "unreachable")
    hub = SimpleNamespace(settings=settings)

    with pytest.raises(butlerd.LocalListUnavailable) as raised:
        await butlerd._harness_models(hub)

    assert "60s" in str(raised.value), (
        f"the reason the harness gave was thrown away, so the picker can only "
        f"say that something went wrong: {raised.value}")


@pytest.mark.asyncio
async def test_an_unknown_target_is_refused_by_name(daemon):
    async with aiohttp.ClientSession() as http:
        async with http.get(f"{daemon}/api/models?target=nonsense") as r:
            assert r.status == 400
            body = await r.json()
    assert "tts_voices" in body["error"], (
        f"the refusal must name the targets that DO exist: {body}")





@pytest.mark.asyncio
async def test_a_base_url_that_already_names_v1_is_not_doubled(stub_endpoint):
    """pi stores every provider's baseUrl WITH the version, OpenAI-style.

    `fetch_models` appended /v1/models unconditionally, so every imported
    connection asked for /v1/v1/models and got a 404 -- which is to say the
    whole feature was broken for its main source. No test caught it because
    every test built its base URL the one way that happened to work; a real
    browser against a real registry found it in seconds.
    """
    from daemon.models import fetch_models

    without_version = await fetch_models(stub_endpoint)
    with_version = await fetch_models(stub_endpoint + "/v1")

    assert without_version == SERVED
    assert with_version == SERVED, (
        "a base URL that already names /v1 must not have another one "
        "appended")


@pytest_asyncio.fixture
async def path_recording_endpoint():
    """Answers /v1/models under ANY prefix and records what was asked for."""
    asked = []

    async def any_models(request):
        asked.append(request.path)
        return web.json_response(
            {"object": "list", "data": [{"id": m} for m in SERVED]})

    app = web.Application()
    app.router.add_get("/{tail:.*}models", any_models)
    server = test_utils.TestServer(app)
    await server.start_server()
    try:
        yield f"http://127.0.0.1:{server.port}", asked
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_versioned_base_that_is_not_v1_is_not_doubled(
        path_recording_endpoint):
    """Versions are not all spelled "v1".

    pi's registry holds `https://generativelanguage.googleapis.com/v1beta`
    for google and `https://api.kimi.com/coding/v1` for kimi. A first fix
    tested `endswith("/v1")` and would have built ".../v1beta/v1/models" --
    the same 404 as before, for the provider nobody had checked. The rule is
    whether the URL carries a PATH at all, not how it is spelled.

    Asserted on the path the SERVER was actually asked for. Recomputing the
    rule in the test would only prove the test agrees with itself.
    """
    from daemon.models import fetch_models

    base, asked = path_recording_endpoint
    for suffix, expected in (("", "/v1/models"),
                             ("/", "/v1/models"),
                             ("/v1", "/v1/models"),
                             ("/v1beta", "/v1beta/models"),
                             ("/coding/v1", "/coding/v1/models")):
        asked.clear()
        assert await fetch_models(base + suffix) == SERVED, suffix
        assert asked == [expected], (
            f"base {base + suffix!r} asked for {asked}, expected "
            f"[{expected!r}] -- a doubled or missing version segment is a "
            f"404 against every real provider")


def test_an_unwarmed_voice_list_is_a_loud_failure_not_an_empty_picker():
    """AC-108. "Could not be asked" and "has no voices" are different facts.

    This gap ARRIVED with AC-108 and did not exist before it. While the
    adapter hardcoded eleven names, `info["voices"]` could not be empty, so
    the lambda's `or []` was unreachable. Now the list is the ENGINE's,
    fetched in `warm()` and cached, so it IS empty in exactly two situations
    -- the sidecar has not been warmed, or it is dead -- and both of them
    answered 200 with `[]`. On the page that renders as a picker offering
    nothing, which reads as "this engine has no voices" rather than as
    "nobody could be asked", and it is the same empty-list-as-though-all-were
    -well defect `LocalListUnavailable` was written for one seam out.

    Asserted on the LISTER rather than over the wire because the 502
    translation is already proved there by
    `test_an_unknown_harness_is_a_loud_502_not_an_empty_picker`; what is new
    here is which side of the line an empty list falls on.
    """
    from types import SimpleNamespace

    from daemon import butlerd

    cold = SimpleNamespace(tts=SimpleNamespace(info={"voices": []}))
    with pytest.raises(butlerd.LocalListUnavailable) as raised:
        butlerd.LOCAL_TARGETS["tts_voices"](cold)
    assert "warming up" in str(raised.value) or "not answering" in str(
        raised.value), (
        f"the reason is what the owner can act on -- a bare failure sends "
        f"them to the wrong place: {raised.value}")

    warm = SimpleNamespace(tts=SimpleNamespace(
        info={"voices": [{"id": "bm_george", "language": "en-gb"}]}))
    assert butlerd.LOCAL_TARGETS["tts_voices"](warm) == ["bm_george"], (
        "a warmed adapter must still answer normally; a guard that refuses "
        "every call is not a guard")

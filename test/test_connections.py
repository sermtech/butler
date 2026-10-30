"""Connections carry pointers to secrets, never secrets. AC-34, AC-37.

The two load-bearing tests here are NEGATIVE, and they were written before
the feature (plan.md section 5 step 7 piece 1 says so explicitly): no key
value in any HTTP response, and no key value anywhere under `state/`. The
registry those pointers aim at holds the owner's real plaintext keys for
three cloud providers, and this daemon is served over a tailnet.

This file boots its OWN daemon rather than using the session fixture, which
is a deliberate exception to conftest's "there is no per-test daemon" rule.
The session daemon runs against the owner's real `state/` directory and their
real pi registry. A test that writes a connection carrying a key pointer into
that directory has already done the damage a cleanup cannot undo, and a test
that reads the real registry puts real secrets in a test process. Isolation
here is the point of the test, not a convenience.
"""
import json
from pathlib import Path

import aiohttp
import pytest

from conftest import ENV_KEY, ENV_VAR, PI_KEY  # noqa: F401




@pytest.mark.asyncio
async def test_no_response_ever_carries_a_key(isolated_daemon):
    """AC-34's load-bearing negative, asserted against a real HTTP body.

    The fixture registry holds a key for every provider, so a handler that
    serialised a connection wholesale -- or an error path that echoed the
    provider object into a message -- would put it on the wire. Checked as a
    substring of the raw body rather than by inspecting parsed fields,
    because a leak through an unexpected key is exactly the leak nobody
    thought to look for.
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        for path in ("/api/connections",
                     "/api/models?connection=llama-swap",
                     "/api/models?connection=does-not-exist",
                     "/api/settings"):
            async with http.get(base + path) as r:
                body = await r.text()
            assert PI_KEY not in body, (
                f"{path} leaked the pi registry key into its response "
                f"({r.status}): {body[:400]}")
            assert ENV_KEY not in body, (
                f"{path} leaked an environment key into its response: "
                f"{body[:400]}")


@pytest.mark.asyncio
async def test_the_connection_list_names_where_the_key_lives_not_what_it_is(
        isolated_daemon):
    """The page needs to show that a connection IS authenticated, and which
    pointer it uses, without ever being told the value."""
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.get(base + "/api/connections") as r:
            assert r.status == 200
            body = await r.json()

    by_id = {c["id"]: c for c in body["connections"]}
    assert "llama-swap" in by_id, f"pi providers were not imported: {body}"
    assert by_id["llama-swap"]["auth_from"] == "pi"
    assert by_id["llama-swap"]["origin"] == "pi"
    assert "auth" not in by_id["llama-swap"], (
        "the raw auth pointer object is not the page's business; it carries "
        "a provider name that only the daemon needs")


@pytest.mark.asyncio
async def test_nothing_under_state_holds_a_key_value(isolated_daemon):
    """AC-37's load-bearing negative.

    Adding a connection whose key comes from the environment must persist the
    VARIABLE NAME and nothing else. Checked by reading every file under the
    daemon's state directory after the save, because "we did not mean to
    write it" is not evidence.
    """
    base, state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/connections", json={
                "id": "my-box", "label": "My server",
                "base_url": "http://10.0.0.5:8080/v1",
                "auth": {"from": "env", "var": ENV_VAR}}) as r:
            assert r.status == 200, await r.text()

    written = [p for p in Path(state).rglob("*") if p.is_file()]
    assert written, "the save wrote nothing at all"
    offenders = []
    for path in written:
        text = path.read_text(encoding="utf-8", errors="replace")
        if ENV_KEY in text or PI_KEY in text:
            offenders.append(path.name)
    assert offenders == [], (
        f"a key VALUE was written to disk in {offenders}. Butler stores "
        f"pointers; the value is read in memory at call time and dropped.")

    saved = json.loads((Path(state) / "connections.json").read_text("utf-8"))
    assert saved["my-box"]["auth"] == {"from": "env", "var": ENV_VAR}, (
        f"the pointer itself must survive the round trip: {saved}")


@pytest.mark.asyncio
async def test_a_manual_id_may_not_shadow_a_pi_provider(isolated_daemon):
    """Two connections answering to one name means the model the owner
    picked is not the model that answers."""
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/connections", json={
                "id": "kimi", "label": "impostor",
                "base_url": "http://127.0.0.1:1/v1",
                "auth": {"from": "none"}}) as r:
            assert r.status == 400, (
                f"shadowing a pi provider was accepted ({r.status})")
            assert "kimi" in (await r.text())


def test_an_unset_environment_variable_fails_loudly_and_names_it(tmp_path):
    """AC-37's other half, at the unit level because the message is the point.

    An unauthenticated request would come back 401 from somewhere further
    away, while the owner is waiting for Butler to speak, naming nothing that
    helps. This raises before anything is sent and says which connection and
    which variable.
    """
    from daemon.connections import Connection, ConnectionError_, ConnectionStore

    store = ConnectionStore(tmp_path / "connections.json",
                            pi_registry=tmp_path / "absent.json")
    store.add_manual(Connection(
        id="cloudy", label="Cloudy", base_url="https://example.invalid/v1",
        auth={"from": "env", "var": "BUTLER_TEST_DEFINITELY_UNSET"}))

    with pytest.raises(ConnectionError_) as excinfo:
        store.resolve("cloudy")
    message = str(excinfo.value)
    assert "cloudy" in message and "BUTLER_TEST_DEFINITELY_UNSET" in message, (
        f"the refusal must name the connection and the variable: {message}")


def test_reloading_pi_leaves_manual_connections_alone(tmp_path):
    """A reload re-imports one half of the store and must not touch the
    other. The owner's own entries are the ones nothing else can recreate."""
    from daemon.connections import Connection, ConnectionStore

    registry = tmp_path / "pi.json"
    registry.write_text(json.dumps({"providers": {
        "alpha": {"baseUrl": "http://alpha.invalid/v1", "apiKey": PI_KEY}}}),
        encoding="utf-8")
    store = ConnectionStore(tmp_path / "connections.json",
                            pi_registry=registry)
    store.add_manual(Connection(id="mine", label="Mine",
                                base_url="http://mine.invalid/v1"))

    registry.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    store.reload_pi()

    ids = {c.id for c in store.all()}
    assert "mine" in ids, "a reload deleted a manual connection"
    assert store.get("alpha").missing is True, (
        "a provider that vanished must be marked missing, not dropped -- a "
        "setting pointing at it has to fail with a sentence that names it")


@pytest.mark.asyncio
async def test_a_non_object_auth_is_a_named_400_not_a_crash(isolated_daemon):
    """This route is reachable from the tailnet with no auth of its own.

    `auth.get("from")` on a string raised AttributeError, which aiohttp turns
    into a 500 -- an unhandled crash where every other handler in the file
    answers 400 naming what was allowed.
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/connections", json={
                "id": "malformed", "base_url": "http://127.0.0.1:1/v1",
                "auth": "oops"}) as r:
            assert r.status == 400, (
                f"a non-object auth answered {r.status}, not a named 400")
            assert "object" in (await r.text()).lower()


@pytest.mark.asyncio
async def test_a_body_carrying_a_key_value_is_refused(isolated_daemon):
    """Refused, never ignored. A caller that sent a key believes it was
    stored; dropping it silently means the connection fails later for a
    reason nobody connects to this request (AC-37)."""
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/connections", json={
                "id": "leaky", "base_url": "http://127.0.0.1:1/v1",
                "auth": {"from": "env", "var": "X", "key": ENV_KEY}}) as r:
            assert r.status == 400, f"a key value was accepted ({r.status})"
            body = await r.text()
    assert ENV_KEY not in body, "the refusal echoed the key back"


def test_a_hand_edited_connections_file_fails_loudly(tmp_path):
    """`state/connections.json` is a file the owner can edit.

    An `auth` that is not an object used to survive loading and blow up later
    inside `.get()`, taking the WHOLE connection list down -- `public()` runs
    over every entry, so one malformed line hid all the good ones.
    """
    from daemon.connections import ConnectionError_, ConnectionStore

    path = tmp_path / "connections.json"
    path.write_text(json.dumps(
        {"broken": {"label": "B", "base_url": "http://b.invalid/v1",
                    "auth": "not-an-object"}}), encoding="utf-8")

    with pytest.raises(ConnectionError_) as excinfo:
        ConnectionStore(path, pi_registry=tmp_path / "absent.json")
    assert "broken" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_manual_connection_can_be_removed_again(isolated_daemon):
    """A panel that adds and never removes is a trap.

    The only way out would be hand-editing a file the owner was never told
    about. Asserted through the same route the page uses, and confirmed by
    the connection disappearing from the list rather than by a 200.
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/connections", json={
                "id": "temp", "label": "Temp",
                "base_url": "http://127.0.0.1:1/v1",
                "auth": {"from": "none"}}) as r:
            assert r.status == 200, await r.text()
        async with http.delete(base + "/api/connections/temp") as r:
            assert r.status == 200, await r.text()
        async with http.get(base + "/api/connections") as r:
            ids = {c["id"] for c in (await r.json())["connections"]}
    assert "temp" not in ids, f"the connection survived its deletion: {ids}"


@pytest.mark.asyncio
async def test_a_pi_provider_cannot_be_deleted_through_butler(isolated_daemon):
    """Refused out loud, not silently ignored.

    pi's registry is not Butler's to edit, and the import would put the entry
    straight back on the next reload -- so a quiet success would be a lie
    that undoes itself.
    """
    base, _state, _registry = isolated_daemon
    async with aiohttp.ClientSession() as http:
        async with http.delete(base + "/api/connections/llama-swap") as r:
            assert r.status == 400, (
                f"deleting a pi provider answered {r.status}")
            assert "pi" in (await r.text()).lower()
        async with http.get(base + "/api/connections") as r:
            ids = {c["id"] for c in (await r.json())["connections"]}
    assert "llama-swap" in ids, "a pi provider was removed anyway"


# --------------------------------------------------------------------------- #
# The resolver: a {conn, model} pair plus a cap becomes ONE call's target.
# plan.md 4.4b, docs/architecture-seams.md 3.4, AC-38.
# --------------------------------------------------------------------------- #
def _store_and_settings(tmp_path, **values):
    from daemon.connections import Connection, ConnectionStore
    from daemon.settings import Settings

    store = ConnectionStore(tmp_path / "connections.json",
                            pi_registry=tmp_path / "absent.json")
    store.add_manual(Connection(id="mine", label="Mine",
                                base_url="http://mine.invalid/v1"))
    prefs = Settings(tmp_path / "settings.json")
    for key, value in values.items():
        prefs.set(key, value)
    return store, prefs


def test_a_pair_and_a_cap_become_one_target(tmp_path):
    from daemon.connections import SettingsResolver

    store, prefs = _store_and_settings(
        tmp_path, router_model={"conn": "mine", "model": "small-one"},
        router_max_tokens=200)
    target = SettingsResolver(store, prefs).resolve("router")

    assert target.base_url == "http://mine.invalid/v1"
    assert target.model == "small-one"
    assert target.max_tokens == 200


def test_reasoning_off_reaches_the_target_per_role(tmp_path):
    """The setting is per role, like the cap. Off resolves to a target that
    will send the no-think kwarg; the default leaves it True so the request is
    unchanged (2026-08-18 reasoning measurement)."""
    from daemon.connections import SettingsResolver

    store, prefs = _store_and_settings(
        tmp_path, router_model={"conn": "mine", "model": "small-one"},
        endpoint_model={"conn": "mine", "model": "big-one"},
        router_reasoning=False)
    resolver = SettingsResolver(store, prefs)
    assert resolver.resolve("router").reasoning is False
    assert resolver.resolve("reply").reasoning is True


def test_a_zero_cap_is_no_cap_at_all(tmp_path):
    """AC-38. 0 must reach the request as an ABSENT field, so it has to stop
    being a number here rather than being special-cased at the wire."""
    from daemon.connections import SettingsResolver

    store, prefs = _store_and_settings(
        tmp_path, endpoint_model={"conn": "mine", "model": "big-one"},
        reply_max_tokens=0)
    assert SettingsResolver(store, prefs).resolve("reply").max_tokens is None


def test_a_cap_change_takes_effect_on_the_next_turn(tmp_path):
    """AC-38's last clause. Everything outside the engines group is read PER
    TURN (4.6a), and a resolver that read once at construction would make the
    setting a restart-flagged one without saying so."""
    from daemon.connections import SettingsResolver

    store, prefs = _store_and_settings(
        tmp_path, router_model={"conn": "mine", "model": "small-one"})
    resolver = SettingsResolver(store, prefs)
    assert resolver.resolve("router").max_tokens is None

    prefs.set("router_max_tokens", 512)
    assert resolver.resolve("router").max_tokens == 512


def test_a_role_nobody_serves_raises(tmp_path):
    """The seam names two roles. A typo must not resolve to something
    plausible -- it would send the router's traffic to the big model."""
    from daemon.connections import SettingsResolver

    store, prefs = _store_and_settings(tmp_path)
    with pytest.raises(KeyError):
        SettingsResolver(store, prefs).resolve("reflex")


def test_a_model_with_no_connection_says_which_setting_is_empty(tmp_path):
    """Butler ships no default model and no default connection (4.4), so an
    unconfigured brain is the NORMAL first state. It has to fail with a
    sentence that names the setting rather than a KeyError from two layers
    down, because this is what a fresh install hits on its first utterance.
    """
    from daemon.connections import ConnectionError_, SettingsResolver

    store, prefs = _store_and_settings(tmp_path)
    with pytest.raises(ConnectionError_) as excinfo:
        SettingsResolver(store, prefs).resolve("router")
    assert "router_model" in str(excinfo.value), str(excinfo.value)


def test_a_pair_pointing_at_a_connection_that_is_gone_fails_by_name(tmp_path):
    """Never a silent substitution onto some other connection (4.4b)."""
    from daemon.connections import SettingsResolver

    store, prefs = _store_and_settings(
        tmp_path, router_model={"conn": "deleted", "model": "small-one"})
    with pytest.raises(KeyError) as excinfo:
        SettingsResolver(store, prefs).resolve("router")
    assert "deleted" in str(excinfo.value)

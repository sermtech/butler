"""Where Butler may ask for a completion, and where the keys are NOT.

plan.md 4.4b. A CONNECTION is the primitive: a base URL plus an auth
POINTER. It replaces a single `endpoint_url` setting, which could only ever
reach unauthenticated local servers because it had nowhere to carry a key.

Three rules govern this file, and all three are about the same thing.

**A pointer, never a value.** `auth` names WHERE a secret lives -- a provider
in pi's registry, or an environment variable -- and the key is read into a
`ResolvedTarget` at call time and dropped. Butler writes no key to its own
disk and no HTTP response carries one (AC-34, AC-37). The real registry at
`C:\\Users\\user\\.pi\\agent\\models.json` holds the owner's plaintext keys for
kimi, openrouter and google, and this page is served over a tailnet.

**pi's registry supplies CONNECTIONS and KEYS, never the model list.** Its
`models[]` array is a DECLARATION, and a declaration goes stale silently --
the picker keeps showing a name, the daemon keeps saving it, and the endpoint
answers 404 on the next thing the owner says. Model lists come live from each
connection's own `/v1/models`, which is what `daemon/models.py` already does.

**Read at boot and on explicit reload. Nothing watches the filesystem.** A
provider that has vanished since boot is marked missing rather than deleted,
and a model setting still pointing at a missing connection fails loudly at
use rather than silently resolving onto some other connection.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# The owner's pi installation. Overridable so tests can point at a fixture
# registry holding a deliberately fake key -- reading the real one in a test
# would put real secrets in a test process (test/AGENTS.md).
DEFAULT_PI_REGISTRY = Path.home() / ".pi" / "agent" / "models.json"


class ConnectionError_(RuntimeError):
    """A connection could not be used, and the message says which and why.

    Deliberately loud. The alternative -- issuing the request without the key
    -- produces a 401 from somewhere further away, at a moment when the owner
    is waiting for Butler to speak, and names nothing that would help.
    """


@dataclass(frozen=True)
class Connection:
    """One place Butler may ask for a completion.

    `auth` is a pointer: {"from": "pi", "provider": X}, {"from": "env",
    "var": X}, or {"from": "none"} for a local server that wants no key.
    """

    id: str
    label: str
    base_url: str
    auth: dict = field(default_factory=lambda: {"from": "none"})
    origin: str = "manual"          # "pi" (imported) | "manual" (owner's own)
    missing: bool = False           # was in pi's registry at boot, gone now

    def __post_init__(self):
        """Validate here so a HAND-EDITED file fails like a bad request does.

        The POST handler checks its body, but `state/connections.json` is a
        file the owner can edit, and an `auth` that is not an object used to
        blow up later inside `.get()` -- taking the WHOLE connection list down
        with it, because `public()` runs over every entry.
        """
        if not isinstance(self.auth, dict):
            raise ConnectionError_(
                f"connection {self.id!r} has a non-object auth "
                f"{self.auth!r}; it must be a pointer such as "
                f"{{'from': 'env', 'var': 'NAME'}}")

    def public(self) -> dict:
        """What may cross the wire. Note what is NOT here: anything resolved.

        This method exists so the leak is a deliberate act rather than an
        oversight -- a handler that serialised the dataclass wholesale would
        still be safe today, and would start leaking the moment someone added
        a resolved field to it.
        """
        return {"id": self.id, "label": self.label, "base_url": self.base_url,
                "auth_from": self.auth.get("from", "none"),
                "origin": self.origin, "missing": self.missing}


@dataclass(frozen=True)
class ResolvedTarget:
    """Everything ONE completion call needs, resolved at call time.

    The key VALUE exists only in this in-memory instance: read from the
    pointer, used for one request, never written and never serialised. It has
    no `public()` on purpose -- there is no correct way to put this on a wire.
    """

    base_url: str
    model: str                      # "" means the connection's own default
    api_key: str | None
    max_tokens: int | None = None   # None means send no cap at all (4.4b)
    # True leaves the model's own default and sends NOTHING; False sends the
    # Qwen-family no-think signal. Default True so an untouched target is a
    # byte-identical request -- a reasoning control that ships On is a kwarg a
    # cloud connection never asked for.
    reasoning: bool = True


def api_url(base_url: str, path: str) -> str:
    """A connection's base URL joined to an API path, both conventions honoured.

    Two are real and both arrive here. OpenAI's own base INCLUDES the version
    -- "https://api.openai.com/v1" -- and that is what pi's registry stores
    for every provider; this repo's retired `endpoint_url` used the other, a
    bare "http://host:8077".

    The test is whether the URL HAS A PATH, not whether it ends in the literal
    "/v1". A first version checked the suffix and would have built
    ".../v1beta/v1/models" for google, whose registry entry ends /v1beta --
    and ".../coding/v1" is kimi's. Versions are not all spelled v1, and a base
    carrying any path at all is already complete.

    One rule, one home: the model list and both completion calls join their
    URLs the same way, so a connection that answers a picker also answers a
    turn. They were two copies for a day, and the copy in `models.py` was the
    one that shipped `/v1/v1/models` to every pi-imported connection.
    """
    root = base_url.rstrip("/")
    has_path = urllib.parse.urlsplit(root).path not in ("", "/")
    return f"{root}/{path}" if has_path else f"{root}/v1/{path}"


def read_pi_providers(path: Path) -> dict:
    """`{name: {baseUrl, apiKey, ...}}` from pi's registry, or {} if absent.

    An absent registry is NORMAL -- pi need not be installed for Butler to
    run -- and is different from a corrupt one, which raises. Returning {}
    for both would make a typo in the path look like "pi has no providers".
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ConnectionError_(f"pi registry at {path} is unreadable: {e}")
    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise ConnectionError_(
            f"pi registry at {path} has no providers object")
    return providers


class ConnectionStore:
    """The connections Butler knows, and the only thing that reads a key.

    Two sources, merged at load: the owner's own file, and pi's registry
    imported as `origin: "pi"`. A reload re-imports the pi half and leaves
    manual entries strictly alone.
    """

    def __init__(self, path: Path, pi_registry: Path | None = None):
        self.path = Path(path)
        self.pi_registry = Path(
            pi_registry if pi_registry is not None else DEFAULT_PI_REGISTRY)
        self._manual: dict[str, Connection] = {}
        self._pi: dict[str, Connection] = {}
        self.load()

    # -- loading -------------------------------------------------------------
    def load(self) -> None:
        self._manual = self._read_manual()
        self.reload_pi()

    def _read_manual(self) -> dict[str, Connection]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        out: dict[str, Connection] = {}
        for cid, entry in (raw or {}).items():
            out[cid] = Connection(
                id=cid, label=entry.get("label") or cid,
                base_url=entry.get("base_url", ""),
                auth=entry.get("auth") or {"from": "none"},
                origin="manual")
        return out

    def reload_pi(self) -> None:
        """Re-import pi's providers. Manual entries are never touched.

        A provider that has disappeared is marked `missing` rather than
        dropped, so a model setting pointing at it fails with a sentence that
        names the connection instead of an unexplained KeyError.
        """
        providers = read_pi_providers(self.pi_registry)
        fresh: dict[str, Connection] = {}
        for name, entry in providers.items():
            base = (entry or {}).get("baseUrl") or ""
            fresh[name] = Connection(
                id=name, label=f"{name} (pi)", base_url=base,
                auth={"from": "pi", "provider": name}, origin="pi")
        for name, was in self._pi.items():
            if name not in fresh:
                fresh[name] = Connection(
                    id=was.id, label=was.label, base_url=was.base_url,
                    auth=was.auth, origin="pi", missing=True)
        self._pi = fresh

    def save(self) -> None:
        """Write the MANUAL half only.

        pi's providers are imported, not owned: writing them here would make
        a second copy of a list that is maintained somewhere else, which is
        the staleness this design exists to avoid. And no `auth` value is
        ever written, because `auth` holds only pointers to begin with.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {c.id: {"label": c.label, "base_url": c.base_url,
                          "auth": c.auth}
                   for c in self._manual.values()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- reading -------------------------------------------------------------
    def all(self) -> list[Connection]:
        """Manual first: they are the ones the owner typed."""
        return list(self._manual.values()) + list(self._pi.values())

    def get(self, conn_id: str) -> Connection:
        for conn in self.all():
            if conn.id == conn_id:
                return conn
        raise KeyError(
            f"no connection named {conn_id!r}; "
            f"known: {sorted(c.id for c in self.all())}")

    def add_manual(self, conn: Connection) -> None:
        """An id already used by a pi provider is refused rather than
        shadowing it: two connections answering to one name means the model
        the owner picked is not the model that answers (4.4b)."""
        if conn.id in self._pi:
            raise ConnectionError_(
                f"{conn.id!r} is already a pi provider; choose another id "
                f"rather than shadowing it")
        self._manual[conn.id] = conn
        self.save()

    def remove_manual(self, conn_id: str) -> None:
        """Forget one of the owner's own. pi's imports are not ours to delete.

        A panel that can add and never remove is a trap: the only way out
        would be hand-editing a JSON file the owner was never told about.
        Refusing on a pi entry rather than silently doing nothing, because
        "it did not work and did not say so" is the worse failure -- the
        import would put it straight back on the next reload anyway.
        """
        if conn_id in self._pi:
            raise ConnectionError_(
                f"{conn_id!r} comes from pi's registry, not from Butler; "
                f"remove it there, or it returns on the next reload")
        if conn_id not in self._manual:
            raise KeyError(f"no connection named {conn_id!r}")
        del self._manual[conn_id]
        self.save()

    # -- the only place a secret is read -------------------------------------
    def resolve(self, conn_id: str, model: str = "",
                max_tokens: int | None = None,
                reasoning: bool = True) -> ResolvedTarget:
        conn = self.get(conn_id)
        if conn.missing:
            raise ConnectionError_(
                f"connection {conn.id!r} is no longer in pi's registry; "
                f"reload, or point this setting somewhere that exists")
        if not conn.base_url:
            raise ConnectionError_(f"connection {conn.id!r} has no base URL")
        return ResolvedTarget(base_url=conn.base_url, model=model,
                              api_key=self._key_for(conn),
                              max_tokens=max_tokens, reasoning=reasoning)

    def _key_for(self, conn: Connection) -> str | None:
        source = conn.auth.get("from", "none")
        if source == "none":
            return None
        if source == "env":
            var = conn.auth.get("var") or ""
            value = os.environ.get(var)
            if not value:
                raise ConnectionError_(
                    f"connection {conn.id!r} wants its key from environment "
                    f"variable {var!r}, which is not set. Set it and restart, "
                    f"or the request would go out unauthenticated and fail "
                    f"somewhere less obvious.")
            return value
        if source == "pi":
            name = conn.auth.get("provider") or conn.id
            provider = read_pi_providers(self.pi_registry).get(name)
            if provider is None:
                raise ConnectionError_(
                    f"connection {conn.id!r} reads its key from pi provider "
                    f"{name!r}, which is not in the registry any more")
            return provider.get("apiKey") or None
        raise ConnectionError_(
            f"connection {conn.id!r} has an unknown auth source "
            f"{source!r}; known: none, env, pi")


class TargetResolver(Protocol):
    """What Butler's own brain adapters are written against.

    One method, deliberately: an adapter that could ask this object anything
    else would be reading the settings store through a keyhole, and the point
    of the seam is that it cannot. `SettingsResolver` below is the daemon's
    implementation; `test/conftest.py` has the other one.
    """

    def resolve(self, role: str) -> ResolvedTarget:
        """role is "router" or "reply". An unknown role raises KeyError."""
        ...


class SettingsResolver:
    """The seam between the owner's preferences and one completion call.

    docs/architecture-seams.md 3.4. Butler's own brain adapters take one of
    these at CONSTRUCTION and call `resolve(role)` per turn, which is what
    keeps three existing rules true at once: settings outside the engines
    group are read per turn (4.6a), an adapter never imports the settings or
    connections modules' state, and no key is held anywhere (AC-37).

    Exactly two roles, and they are not interchangeable: the router's cap is
    sized to survive a reasoning block and the reply's is sized to bound
    spend (docs/architecture-seams.md 4.1).
    """

    # Images go to `reply` (AC-815): the model that answers has the eyes.
    ROLES = {"router": ("router_model", "router_max_tokens", "router_reasoning"),
             "reply": ("endpoint_model", "reply_max_tokens", "reply_reasoning")}

    def __init__(self, store: ConnectionStore, settings):
        self._store = store
        self._settings = settings

    def resolve(self, role: str) -> ResolvedTarget:
        """Everything ONE call needs. Unknown role raises KeyError."""
        if role not in self.ROLES:
            raise KeyError(f"no such brain role {role!r}; "
                           f"known: {sorted(self.ROLES)}")
        model_key, cap_key, reasoning_key = self.ROLES[role]
        pair = self._settings.get(model_key) or {}
        conn_id = str(pair.get("conn") or "").strip()
        if not conn_id:
            # The normal state of a fresh install: Butler ships no default
            # model and no default connection (4.4), so this fires on the
            # first thing said and has to name the setting to fix rather than
            # arriving as a KeyError from two layers down.
            raise ConnectionError_(
                f"{model_key} names no connection yet -- choose one on the "
                f"settings page, under Brain")
        cap = self._settings.get(cap_key) or 0
        # Only an explicit False disables thinking; the default and anything
        # falsy-but-unset leaves the model alone, so the request is unchanged
        # until the owner turns it off (the same wire-or-nowhere rule as the
        # cap, AC-38).
        reasoning = self._settings.get(reasoning_key) is not False
        # 0 means "send no cap at all" and it stops being a number HERE, so
        # nothing downstream has to remember the special case (AC-38).
        return self._store.resolve(conn_id, model=str(pair.get("model") or ""),
                                   max_tokens=int(cap) or None,
                                   reasoning=reasoning)

# Remote access: who may call Butler (BUILT; live rollout open)

Written 2026-10-29 against HEAD `f9fc689`; approved and built the same
day (`daemon/access.py`, `test/test_access_gate.py`, `test/test_lan_access.py`).
AC-720..AC-732 are promoted to `plan.md`; AC-733..AC-749 stay reserved.
Spec-local decision ids are RA-n. Provenance tags as `docs/proactive.md`. The
spec was reviewed adversarially before the build and the build was attacked
after it [REVIEW]; section 8 holds what the attack found.

## 0. The owner's decisions [OWNER]

- 2026-10-29: considering a public release; a safety review found that
  anyone who can reach port 8140 controls Butler. Owner chose "option 2 and
  3": keep working over Tailscale through `tailscale serve` with an identity
  check, plus a pairing token for every other remote client.
- 2026-10-29, on port 8140's default: **"Both, LAN opt-in per machine."**
  A fresh install listens on localhost only; the owner's three machines
  turn LAN access on, so their phones may use either the ts.net address
  (no pairing) or the old address (paired once).

## 1. What is true today [READ 2026-10-29]

- Main listener 8140 binds `0.0.0.0` (`daemon/butlerd.py:4470`), TLS when
  `ssl/` holds a cert, plain HTTP otherwise (`butlerd.py:4366`). The
  sockets are bound in `main` BEFORE settings load.
- LOCAL 8141 and PROXIED 8142 bind 127.0.0.1, plain HTTP; a request's
  listener is read off the accepting socket (`hub.listener_of`, DA-6).
- About 75 routes are registered. 13 call `hub.is_local`; the rest,
  including `/api/mode`, `/api/settings`, `/api/notify`, `/api/shortcuts`,
  `/api/project/{id}/raw|upload`, `/api/terminal`, `/api/restart`, have NO
  gate. `/ws` refuses only a foreign `Origin`; a client sending none passes
  (`hub.py:155`). Route inventory: the review of 2026-10-29 [REVIEW].
- Verified exploit chain [REVIEW]: a LAN neighbour POSTs `/api/mode`
  `{"mode":"trusted"}` (`butlerd.py:3022`, `authorized=True` unchecked),
  opens `/ws` with no Origin and types a command an agent runs.
- `tailscale serve` adds `Tailscale-User-Login` to proxied requests, strips
  any client-supplied copy, omits it for tagged devices and Funnel, and
  DOES add it for users a device was shared with [READ:
  tailscale.com/kb/1312/serve, 2026-10-29]. Presence of the header is
  therefore not ownership; only an allowlisted login is. It also keeps the
  phone's ts.net `Host` (`test/test_ws_origin.py:99`).
- Desk has other `tailscale serve` entries;
  nothing forwards to 8142 yet [MEASURED 2026-10-29].
- The settings loader drops unknown keys from the file and the API refuses
  them (`daemon/settings.py:1602`, `:1832`).

## 2. The shape

One function decides, for every request, before any handler runs:

```
Gate.decide(request) -> OWNER | DEVICE(id) | raises Refused(status, reason)
```

- **RA-1 One gate.** An aiohttp middleware (`access.gate`) calls it for
  every route, static file and `/ws`. A handler never decides trust
  itself; `hub.is_local` stays as a SECOND, narrower check on the agent
  routes (it answers "a child process on this machine", not "the owner").
  A gate refusal carries the header `X-Butler-Gate: refused`; a handler's
  own 401/403 never does, so tests can tell the two apart.
- **RA-2 By listener, never by a header the caller controls.**

| Listener | OWNER when | DEVICE when | otherwise |
|----------|-----------|-------------|-----------|
| LOCAL 8141 | always (bound to 127.0.0.1) | -- | -- |
| MAIN 8140 | peer is loopback | valid device cookie | 401 |
| PROXIED 8142 | `Tailscale-User-Login` in the owner list | valid device cookie | 401 |

  A DEVICE may use everything EXCEPT the owner-only actions (RA-11): it
  cannot mint pair codes, list or remove devices, or change `lan_access`
  or `tailscale_owners`. That stops a stolen cookie from entrenching itself
  THROUGH THE GATE. It does not undo what the device did while paired: a
  device can run coding agents, create a webhook trigger (its token keeps
  working), add a notification channel. Removing a device ends its own
  requests and sockets, nothing more (section 8, H2).
- **RA-3 Same-origin on every request.**
  - `Origin`, when present, must be one of the listener's own origins
    (today's `ws_origin_ok` rule, applied to HTTP too).
  - `Sec-Fetch-Site`, when present, must be `same-origin` or `none`. This
    catches the cross-site GET that carries no `Origin` (an `<img>` or a
    no-cors fetch from another tab) [from memory: Fetch Metadata; verify
    in step 6].
  - On LOCAL and on a plain-HTTP MAIN, `Host` must be an IP literal or
    `localhost`, with no userinfo (DNS rebinding). On PROXIED, `Host` must
    end in `.ts.net` (only Tailscale answers DNS for that suffix, so no page
    can rebind one to 127.0.0.1) and Origin, when present, equals
    `https://` + Host; two `Tailscale-User-Login` headers are refused.
- **RA-4 No proxy in front of a loopback-trusted listener.** MAIN and
  LOCAL refuse any request carrying `Tailscale-User-*`, `X-Forwarded-*`,
  `Forwarded`, `X-Real-IP` or `True-Client-IP`. Without this, an old `tailscale serve` entry, a portal
  or an SSH tunnel aimed at 8140/8141 makes every one of its clients
  OWNER (loopback peer). Phone link only ever forwards to PROXIED.
- **RA-5 Exempt routes, a closed list.** `POST /api/pair` (pairing),
  `POST /api/hooks/{name}` (keeps its own `X-Butler-Token`,
  `triggers.py:869`), `GET /manifest.json` and `GET /icon.svg` (fetched by
  browsers without cookies; no data in them). RA-3 and RA-4 still apply
  to them. A source test holds the list. A refused `GET /` gets the pair
  page AS its 401 body, so there is no exempt page route.
- **RA-6 Owner list.** Setting `tailscale_owners` (comma-separated
  logins). Blank means "this machine's own login", from
  `tailscale status --json` (`Self.UserID` -> `User[id].LoginName`), read
  on demand and cached 60 s (tailscaled may start after Butler). If the
  machine is a tagged node, or the login cannot be read, blank resolves to
  NOBODY: a request that needs the owner list gets 503 naming the setting;
  cookie devices and exempt routes are unaffected. Never allow-all.
- **RA-7 LAN is opt-in.** Setting `lan_access` (bool, default false,
  `restart: True`). `butlerd.main` reads it from the settings file BEFORE
  binding. False: MAIN binds 127.0.0.1. True: MAIN binds 0.0.0.0 and
  REQUIRES TLS; with no cert the daemon refuses to boot and names
  `scripts/make-cert.py`. `--host` defaults to unset; when given it still
  wins. Because AC-715 restarts the daemon by itself after a restart-flagged
  change, `POST /api/settings` REFUSES `lan_access: true` when no
  certificate exists (`butlerd.cert_paths`), rather than restarting into a
  daemon that will not boot.

## 3. Pairing

- **RA-8 Codes come only from an OWNER.** Settings, Access (a
  `Group.pairing` block over `/api/devices`: GET lists, POST mints, DELETE
  `?device=<id>` removes) shows "Pair a device": a 6-digit code and, when
  an address for the new device is known (the page's own non-loopback
  address, or the LAN address with `lan_access` on), a QR of
  `<address>/#pair=NNNNNN`. The
  code rides in the URL FRAGMENT, which browsers never send, so it never
  reaches a server log. A code lives 5 minutes and works once. Wrong
  guesses: 5 per peer address, 20 in total, then the code burns and the
  owner mints another (a neighbour can spend a code, not guess it: 20
  tries in 10^6).
- **RA-9 The exchange.** `POST /api/pair {code, name}` returns a 256-bit
  random token in the cookie `butler_device` (`HttpOnly; Secure;
  SameSite=Strict; Path=/`, no expiry). The daemon stores only
  `sha256(token)`, name, created and last-seen times in
  `paths.records / "devices.json"` (data root, never the repo).
- **RA-10 Unpaired browsers see one page.** A refused `GET /` (Accept
  `text/html`) answers 401 with a self-contained pair page: a code box,
  prefilled from its own `#pair=` fragment if present. It loads no other
  asset. Every other refusal is 401 JSON `{error, pair: "/"}`.
- **RA-11 Owner-only actions.** Every method of `/api/devices` (minting
  codes, the list, removing), the standing-channel routes (H2, section 8),
  `/api/settings/reset` (a device's reset
  buttons answer 403: accepted cost), and the settings `lan_access` and
  `tailscale_owners`. Removing a device deletes its
  hash; its next request is refused and its open `/ws` closes within 1 s.
- `/ws` carries the cookie automatically (same origin), so the page's
  WebSocket code does not change.
- **Accepted costs.** Browsers send a cookie to every port on the same
  host, so other HTTPS services on the same name or IP (on the desk: 8787
  and 3001 behind ts.net) receive `butler_device`. An iPhone home-screen
  app keeps its own cookie jar, so it pairs once more inside the app (the
  pair page works there; type the code) [from memory].

## 4. Criteria (AC-720..AC-749 reserved)

Harness rule for all of them: requests go through the REAL middleware on
a full `make_app` (settings, channels, triggers and WhatsApp present, so
conditional routes exist), on REAL sockets (`test_listeners._Three`): a
foreign peer is MAIN bound to this machine's LAN address. Patching
`Gate.decide` or `_is_local` does not count.

- **AC-720 (one gate).** For every route in `app.router` plus static
  `/app.js` and a `/models/` sample, a non-loopback MAIN request with no
  cookie answers with `X-Butler-Gate: refused`, except the RA-5 list.
- **AC-721 (loopback and LOCAL stay owner).** The same enumeration on LOCAL
  and on MAIN from 127.0.0.1 never carries `X-Butler-Gate`. The desk
  agent, pi extension, `tools/wa.py` and the desktop app keep working.
- **AC-722 (/ws).** An upgrade with no Origin from a non-loopback peer and
  no cookie is refused before `Hub.handle` runs.
- **AC-723 (same-origin).** On every listener, a POST with
  `Origin: https://evil.example`, and a GET with
  `Sec-Fetch-Site: cross-site`, are gate-refused. On LOCAL and plain-HTTP
  MAIN, `Host: evil.example` is refused. On PROXIED, Host
  `desk.tail1234.ts.net` with the matching Origin is accepted
  (`test_ws_origin.py` stays green).
- **AC-724 (no proxy on loopback listeners).** A loopback request on MAIN
  or LOCAL carrying `Tailscale-User-Login` or `X-Forwarded-For` is
  gate-refused.
- **AC-725 (Tailscale owner).** On PROXIED, a listed login is OWNER;
  another login, or none (tagged device), is refused without a cookie.
- **AC-726 (owner list default).** Blank `tailscale_owners` resolves to
  the self login; a tagged self or unreadable status resolves to nobody
  (503 naming the setting) while a cookie device and `/api/hooks` still
  work on PROXIED.
- **AC-727 (pair code).** An OWNER-minted code pairs once; reused, expired,
  or past 5 per-peer / 20 total failures it is refused. A DEVICE or an
  unknown caller cannot mint one.
- **AC-728 (cookie).** The pair response sets `butler_device` with
  HttpOnly, Secure, SameSite=Strict; `devices.json` holds no raw token.
- **AC-729 (owner-only and revoke).** A DEVICE gets 403 on RA-11 actions.
  Removing a device makes its next HTTP request refused and closes its
  `/ws`.
- **AC-730 (LAN default).** `butlerd.main` with no `--host`: `lan_access`
  false binds MAIN to 127.0.0.1, true to 0.0.0.0, true with no cert exits
  non-zero naming `make-cert.py`. Asserted on the socket `main` binds.
- **AC-731 (exempt list).** A source test asserts the exempt set is
  exactly RA-5; `/api/hooks/{name}` still requires its token.
- **AC-732 (settings and guide).** `lan_access`, `tailscale_owners`, the
  pair block and the device list appear on the settings page from the
  schema (AC-41); `daemon/guide_features.py` names pairing (BH-3).

Gherkin for the journeys that matter most:

```
Scenario: a LAN neighbour cannot take over (AC-720, AC-722)
  Given lan_access is true and no device is paired
  When 10.0.0.50 POSTs /api/mode {"mode":"trusted"}
  Then the answer is 401 with X-Butler-Gate and the mode is unchanged
  When 10.0.0.50 opens /ws with no Origin
  Then the upgrade is refused

Scenario: the owner's phone over Tailscale needs nothing (AC-725)
  Given tailscale serve forwards https://desk.ts.net:<port> to 8142
  And tailscale_owners is blank and the desk's login is owner@example.com
  When the phone signed in as owner@example.com opens the page
  Then it loads and /ws connects with no pairing step

Scenario: a phone on the LAN pairs once (AC-727, AC-728)
  Given lan_access is true
  When the owner opens Pair a device on the desk and the phone scans the QR
  Then the phone gets a butler_device cookie and the page loads
  And the same code on another phone is refused

Scenario: a stolen cookie cannot entrench itself (AC-729)
  Given a paired DEVICE
  When it asks for a pair code or edits tailscale_owners
  Then the answer is 403
```

## 5. Build plan

Each step is red-green under `test/AGENTS.md`'s mutation discipline
(break the gate, watch the named test fail, restore). Scratch daemons only
(`hotkeys_enabled: false`, own `BUTLER_STATE`, free port, killed by PID);
never 8140-8142 on a live machine.

1. **Harness, gate, same-origin, proxy refusal (AC-720..724, 731).**
   `daemon/access.py`: `caller`, the middleware, the exempt set; wired in
   `make_app`. The faked-peer full-app fixture and the enumerating test
   come first. [opus: security, wrong is silent]
2. **Tailscale owner (AC-725, 726).** Reuse `phonelink._tailscale` for the
   status read; no second copy. [opus]
3. **Pairing and owner-only actions (AC-727..729).** Codes, exchange,
   `devices.json` via `daemon/paths.py`, pair page, revoke and `/ws`
   close. [opus for the token path; sonnet for the pair page HTML against
   the approved tests]
4. **LAN setting (AC-730).** Schema rows, `main` reads the setting before
   binding, `--host` default unset, TLS requirement; `scripts/launch.py`
   prints the real bind address. [sonnet: bounded, tests exist]
5. **Settings page and guide (AC-732).** Pair block and device list
   through the generic `Group.pairing` render. [sonnet]
6. **Attack review.** An opus review subagent with a shell attacks a
   scratch daemon (LAN peer, foreign Origin, cross-site GET from a real
   browser, forged Tailscale header on MAIN, reused and brute-forced code,
   revoked cookie) and reads pytest's exit code.
7. **Docs.** This spec's status, `plan.md` ACs, `docs/desktop-app.md`
   sections 3 and 9, `docs/macos.md`, root `AGENTS.md` safety block,
   `README.md`.

## 6. Rollout to the live machines (owner present)

After this change every device that reaches 8140 from another address
(LAN IP, Tailscale `100.x` IP, `.local` name) needs pairing once, or moves
to the ts.net address. Remote webhook senders lose MAIN when `lan_access`
is off; ts.net stays open to them. Per machine, desk first, then laptop,
then Mac mini:

1. `tailscale serve status` on that machine: remove or report any entry
   forwarding to 8140 or 8141 (RA-4 would now refuse it anyway).
2. Sync the code with the daemon STOPPED, add `"lan_access": true` to its
   `settings.json` by hand (the old daemon drops unknown keys, so this
   cannot be done before the sync), then start through the documented path
   and prove a new PID and start time.
3. Phone link on a free HTTPS port (desk: 443, 8443, 10000, 10443 taken).
4. Each phone: open the ts.net address, or pair on the old one.
5. Mac mini: launchd does not restart a daemon that refuses to boot, so
   stay on SSH until the PID is proven. Ana's phone needs her Tailscale
   login in that Mac's `tailscale_owners`, or one pairing done with her.
   Ask before touching it.

## 7. Not in this spec

- Restricting project roots (`/api/shortcuts` accepts `C:\`). Finding 3 of
  the 2026-10-29 review; after this spec only the owner can reach it, so it
  becomes a guard against the owner's own agents, specced separately.
- Prompt injection through messages an agent reads.
- Retiring the self-signed listener (`desktop-app.md` O-2).
- Scrubbing personal data from the repo before publishing.

## 8. Built 2026-10-29, and what the attack found [REVIEW]

- Built: steps 1-5 and 7 of section 5. 21 + 5 + 3 mutations (D-13) each
  failed their aimed test and left the rest green. The fast tier of the
  suite runs green after the change (16 older tests now present an owner's
  login or a paired device through `_Three.who`, so each still asks its
  question: such a caller is not this machine).
- Attack review (opus, scratch daemons, real Chrome for cross-site):
  - H1 FIXED: DNS rebinding reached PROXIED as the owner (Host was not
    checked there, and a same-origin page may set `Tailscale-User-Login`).
    PROXIED now accepts only a `.ts.net` Host (RA-3).
  - H2 DECIDED 2026-10-29 (owner: "owner"): the routes that make a
    standing channel are owner-only (`access.OWNER_ONLY`: triggers, notify
    channels, connections, automations, WhatsApp pairing; pinned by
    `test_a_device_cannot_create_a_standing_channel`). What a device does
    through an agent while paired is still not undone. Was: removing a
    device does not remove what it created. Verified: a device made a webhook trigger, was removed, and
    the hook's token still worked. A device can also run agents. Options:
    make the routes that create standing channels (triggers, notify,
    connections, WhatsApp pairing, automations, shortcuts) owner-only, or
    accept that a paired device is trusted like the owner (today's text).
  - Low, fixed: a list body on `/api/settings` was a 500; `X-Real-IP` and
    `True-Client-IP` passed RA-4; a Host with userinfo passed; a duplicated
    login header used the first copy.
  - Held: every route, odd path, method and static file from a LAN peer;
    forged headers in any case; Origin and fetch-metadata variants in a real
    browser; pairing brute force, reuse and a 4-way redemption race (one
    winner); owner-only bypass attempts including the `/ws` protocol.
- Live rollout (section 6): DESK done 2026-10-29 (`lan_access: true`,
  `phone_link_port` 8444, `tailscale serve` 8444 -> 8142; LAN requests
  unpaired answer 401, ts.net as this PC's login answers 200; settings
  backup `settings.json.bak-2026-10-29`). LAPTOP done 2026-10-29
  (`lan_access: true`, serve 443 -> 8142, LAN unpaired 401, WhatsApp still
  linked). MAC MINI done 2026-10-29 (`lan_access: true`, serve 443 -> 8142
  set by hand over SSH, `tailscale_owners` set EXPLICITLY to the same login
  a blank would mean: from launchd the app's CLI answers "The Tailscale GUI
  failed to start", so Butler cannot read its own login there).
  All three machines and the phones share one Tailscale account; the owner
  ruled 2026-10-29 to leave that as is, so any device on it is OWNER on all.

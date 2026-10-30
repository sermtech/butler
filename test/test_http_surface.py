"""The HTTP surface a person actually touches. plan.md 4.9, AC-8.

Small, but this is the file that catches "the daemon is fine, the page just
does not load". The 403-on-root bug these tests pin down was invisible to
every protocol test, because those connect straight to /ws and never ask
for the page a human opens.
"""
import aiohttp
import pytest


@pytest.mark.asyncio
async def test_root_serves_the_satellite_page(daemon):
    """GET / must return the page, not 403.

    add_static answers 403 for the directory itself, so without an explicit
    route the only URL anyone types serves an error. Nothing else in the
    suite would notice.
    """
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/") as r:
            assert r.status == 200, f"GET / returned {r.status}"
            body = await r.text()
    assert "<html" in body.lower()


@pytest.mark.asyncio
async def test_wasm_is_served_with_the_right_mime(daemon):
    """onnxruntime-web refuses to instantiate a .wasm served as anything but
    application/wasm, and on iOS that is the difference between a working
    wake word and a blank HUD. Load-bearing, and easy to break by changing
    how static files are served."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/ort18/ort-wasm-simd.wasm") as r:
            if r.status == 404:
                pytest.skip("ort18 wasm not fetched in this checkout")
            assert r.status == 200
            assert r.headers.get("Content-Type") == "application/wasm", (
                f"wasm served as {r.headers.get('Content-Type')!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/app.js", "/api/settings"])
async def test_everything_served_tells_the_browser_to_revalidate(daemon, path):
    """A stale page is indistinguishable from a missing feature.

    With no Cache-Control header a browser applies heuristic caching and can
    reuse app.js from memory without asking. The page then runs an older
    build than the daemon serves -- and the symptom is not an error, it is
    controls quietly absent, which reads as "that was never built".

    no-cache is revalidate-before-use, not no-store: the ETag still answers
    304, so this costs a conditional request rather than a re-download.
    """
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}{path}") as r:
            assert r.status == 200, path
            cache = (r.headers.get("Cache-Control") or "").lower()
    assert "no-cache" in cache or "no-store" in cache, (
        f"{path} was served without a revalidation header: {cache!r}")


@pytest.mark.asyncio
async def test_mode_endpoint_reports_the_server_side_mode(daemon):
    """AC-8: the mode is server-side and readable over HTTP, so two devices
    cannot diverge the way four localStorage copies did (D-2)."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/mode") as r:
            assert r.status == 200
            body = await r.json()
    assert body.get("mode"), f"no mode in {body}"


@pytest.mark.asyncio
async def test_health_reports_the_protocol_version(daemon):
    """A satellite that cannot tell which protocol it is talking to has to
    guess, and guessing is how a version skew becomes a silent failure."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/health") as r:
            assert r.status == 200
            body = await r.json()
    assert body.get("proto") == 1
    assert body.get("ok") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "?project=", "?project=s-nobody",
                                   "?project=s-nobody&q=x"])
async def test_scoping_the_chat_list_is_404_in_endpoint_mode(daemon, query):
    """The scope does not open a door past `NO_CONVERSATION`.

    Every route in this family 404s when the daemon runs no harness, and the
    new query parameter is checked AFTER that -- so a scoped request in
    endpoint mode answers the same thing an unscoped one does. A 400 here
    would tell a satellite this daemon holds conversations and merely
    disagrees about which project, which is a different fact.
    """
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/chats{query}") as r:
            assert r.status == 404, f"{query} answered {r.status}"
            body = await r.json()
    assert "no conversation" in body.get("error", "").lower(), body


@pytest.mark.asyncio
async def test_turn_status_is_404_in_endpoint_mode(daemon):
    """`GET /api/chats/{id}/status` (AC-236) is in the same family as every
    other session route: a daemon holding no conversation cannot report a
    turn's status for one, and it says so rather than answering an empty 200
    a page would read as "this conversation exists and is idle"."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/chats/chat-nope/status") as r:
            assert r.status == 404, f"answered {r.status}"
            body = await r.json()
    assert "no conversation" in body.get("error", "").lower(), body


@pytest.mark.asyncio
async def test_every_project_row_carries_a_conversation_count(daemon):
    """`GET /api/shortcuts` rows gain `chats` (AC-229's rail half).

    Asserted against the SESSION daemon, which runs in endpoint mode on a
    scratch state directory -- so this is also the honest check that the count
    is 0 and the pane is served anyway, rather than a route that needs a
    conversation store to answer at all.
    """
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{daemon}/api/shortcuts") as r:
            assert r.status == 200
            body = await r.json()
    assert body["rows"], ("the boot migration mints a ROOT row, so an empty "
                          f"pane means that stopped happening: {body}")
    assert all(row.get("chats") == 0 for row in body["rows"]), (
        f"a daemon holding no conversations did not count 0: {body['rows']}")


def test_client_bundle_has_no_localstorage():
    """AC-8's grep gate, as a test rather than a promise.

    An earlier prototype ended up with one laptop holding four diverging localStorage
    copies of a setting because the app answered on four origins. The mock
    is excluded: it is a design artifact, not shipped client code.
    """
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    offenders = []
    for path in list(web.glob("*.js")) + list(web.glob("*.html")):
        if path.name == "mock.html":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "localStorage" in text or "sessionStorage" in text:
            offenders.append(path.name)
    assert offenders == [], (
        f"client storage is banned; found it in {offenders}")


def test_the_shipped_client_is_ascii():
    """No unicode in code (the project rule), enforced over ALL of `web/`.

    It was enforced over three files and not the fourth. `web/AGENTS.md`
    runs `LC_ALL=C grep -c "[^ -~]" web/settings.js web/index.html` by hand
    and `test_visualiser_contract.py` checks `visualiser.js` as raw bytes --
    so `app.js`, the largest file in the directory, was the one nobody
    watched. It carried four: two curly quotes in the microphone's own label
    and two middle dots. Found by a probe agent on 2026-09-10, not by any
    gate, which is the whole argument for this test existing.

    Checked as BYTES rather than by decoding, because a file that is not
    valid UTF-8 must fail here rather than raise somewhere less obvious.
    `mock.html` and `mocks/` are design artifacts and are excluded, the same
    way the storage gate above excludes them.

    CONTROL BYTES COUNT TOO, and that half was added 2026-09-10 after a
    NUL reached `index.html` inside a CSS escape a shell layer had eaten,
    arriving as a literal 0x00 followed by "B7". It passed BOTH gates:
    this test looked only above 0x7F, and the hand-run
    `LC_ALL=C grep -c "[^ -~]"` in `web/AGENTS.md` answers 0 on a file
    grep has decided is BINARY, which is exactly what one NUL makes it.
    So the composer rendered a replacement glyph in its own microcopy
    with every gate green. Tab, newline and carriage return are the only
    control bytes a source file here has any business holding.
    """
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    allowed_control = {0x09, 0x0A, 0x0D}
    offenders = {}
    for path in sorted(list(web.glob("*.js")) + list(web.glob("*.html"))):
        if path.name == "mock.html":
            continue
        raw = path.read_bytes()
        bad = [i for i, b in enumerate(raw)
               if b > 0x7F or (b < 0x20 and b not in allowed_control)]
        if bad:
            line = raw[:bad[0]].count(b"\n") + 1
            offenders[path.name] = (f"{len(bad)} byte(s), first "
                                    f"0x{raw[bad[0]]:02X} at line {line}")
    assert offenders == {}, (
        f"non-ASCII in shipped client code: {offenders}. Use an HTML entity "
        f"in markup and a plain ASCII character in a JS string -- a JS "
        f"string reaches the DOM through textContent, where an entity "
        f"renders literally.")


def _web():
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "web"


def _hud_ids(app_js: str) -> set:
    """Every id `app.js` writes a diagnostic into.

    Read OFF the caller rather than written down here, which is
    `test_visualiser_contract.py`'s rule for the `viz.*` call sites and the
    same defect underneath (D-18): a list restated in a test is a list that
    can agree with itself while disagreeing with the page.
    """
    import re
    return set(re.findall(r'\bhud\(\s*"([^"]+)"', app_js))


def test_every_hud_readout_has_a_slot_to_land_in():
    """AC-161's demotion half, as a gate rather than a hope.

    `hud(id, value)` looks the element up and RETURNS QUIETLY when it is
    missing -- deliberately, because an ES module that throws takes the whole
    page down and the symptom reads as "that was never built" (D-22). The
    cost of that kindness is that a dropped slot is invisible: the panel
    still opens, the other readouts still update, and the diagnostic is
    discarded with one `console.warn` nobody is watching.

    That is exactly the edit this gate exists for. The diagnostics fold was
    MOVED out of the conversation column and into the sidebar on 2026-09-10,
    and AC-161's stated failure mode is "one that drops a HUD readout without
    saying so". A probe that opens the fold and reads it cannot catch this --
    a missing span makes the panel look fine.

    `modeLabel` is in the set and lives in the sidebar foot rather than in
    the fold; the gate asks where `app.js` writes, not where the markup keeps
    it, which is what lets the fold move again without touching this test.
    """
    import re
    app_js = (_web() / "app.js").read_text(encoding="utf-8", errors="replace")
    html = (_web() / "index.html").read_text(encoding="utf-8", errors="replace")
    ids = _hud_ids(app_js)
    assert ids, ("no `hud(\"...\")` call sites found in app.js at all, so this "
                 "gate is vacuous -- the readouts or the helper were renamed")
    present = set(re.findall(r'\bid="([^"]+)"', html))
    missing = sorted(ids - present)
    assert missing == [], (
        f"app.js writes diagnostics into {missing}, which index.html has no "
        f"element for. `hud()` swallows that silently, so the readout is "
        f"discarded and the panel still looks healthy (AC-161).")

# End-to-end user-journey audit (AUDIT, 2026-09-26, against 80effe1)

An audit, not a spec. Nothing here is an accepted criterion and no code was
changed. Driven with `playwright-cli` (headless Chromium, desktop 1400x900 and
an emulated phone 390x844 with touch and a coarse pointer) against a SCRATCH
daemon on 127.0.0.1:8798 (`BUTLER_STATE=TMP/e2e-journeys-state`, a copy of
`state/` with `hotkeys_enabled: false`, `BUTLER_ASR=fake`, `BUTLER_TTS=fake`,
real pi brain, `harness_tools` read_only). Killed by launcher PID each time
(13892, then 44144, with `/T`). Port 8140 was never touched. HEAD moved to
d14f6de during the audit (CLAUDE.md only), so the audited code is 80effe1.

Evidence: screenshots in `TMP/e2e-journeys-out/`, the daemon console in
`TMP/e2e-journeys.log`, and `GET /api/chats` / `GET /api/session/current`
read on 8798 after each step. [MEASURED] means driven and observed; [READ]
means read from source; [OPINION] is a judgement.

## 0. Priority: can a chat be deleted without the two-press confirm?

**Verdict: no single press deletes, and nothing deletes on load, reload or
restore -- but the confirm has no dwell time, so any input that produces two
activations in a row deletes instantly.** [MEASURED]

| Path | Result | Evidence |
|---|---|---|
| One click on the trash icon | Arms ("delete?"), disarms after 4 s, chat kept | d1-armed.png; API still lists zzdel8 |
| Double-click on the icon | DELETED, no pause | log `chat ...zzdel8 deleted from the page` |
| Touch: one tap | Arms then disarms, kept | phone session, API |
| Touch: two taps 120 ms apart | DELETED | log line for zzdel2 |
| Held Enter on a focused icon (CDP autoRepeat keydowns, 35 ms apart) | DELETED | log line for zzdel5 |
| Arm row A's icon, then click row B's icon | B armed, A disarmed, nothing deleted | eval: `zzdel7:- zzdel6:1:delete?` |
| Click the row body next to the icon | Opens only; row and icon abut, no overlap (row 81-339 px, icon 339-371 px) | elementFromPoint sweep |
| Enter / Space on a focused ROW | Opens the chat only | API: zzdel4 still present |
| Page load, reload, reconnect | No DELETE request issued | `requests` list: only DELETEs follow my clicks |

Mechanism [READ]: `chatDropButton` (web/app.js:2167-2198) arms on the first
click and commits on any second click on the same element within
`DROP_ARM_MS` (4000 ms, app.js:2152), with no minimum interval. A repaint
replaces the element, so a repaint disarms (safe). The only caller of
`DELETE /api/chats/{id}` is `commitChatDelete` (app.js:2215-2221), and the
only daemon writer of the "deleted from the page" line is `ChatStore.delete`
(daemon/chats.py:662-714), reached only from that route.

The owner's log (`butler.log` lines 68-71: open X, delete X, open Y, delete Y)
therefore records two real DELETE requests. The log has no timestamps and no
access log, so whether they were a fast double-click or deliberate cannot be
recovered; a double-click on the icon reproduces the first delete exactly.
After deleting the CURRENT chat the page issues no `session/open` of its own
(d8), so the "page opened Y" line was a separate click.

## 1. Spec (user stories, Given/When/Then, abbreviated)

S1 First arrival. Given a fresh load, the tree marks the chat the daemon holds
and the header names its project.
S2 New chat. When "+ New chat" (tree) or the strip pencil is pressed, a chat is
born in that project (tree) or the current chat's project (strip), and shown.
S3 Typed turn. When I send, the reply appears; while it runs the row and
project show running; afterwards the row and header carry the title.
S4 Switching. When I open another chat (same or other project) the
transcript, header and the agent's cwd follow it; a background turn is shown
as running on its own row, and the chat on screen shows its own state.
S5 Tree. A project press only expands/collapses; the set survives reload.
S6 Delete. Two deliberate presses delete; deleting the current chat shows a
fresh chat in the same project; a row deleted elsewhere is handled quietly.
S7 Rewind. Copy-then-truncate; prompt back in the composer; never auto-sent.
S8 Search. Title and content, across projects, with project names; Escape
clears.
S9 Projects. Add, rename, remove a TMP project; removed-project chats appear
struck through; the root cannot be removed.
S10 Busy confirm. "+ New chat" on a project with a running agent asks; Cancel
changes nothing; Confirm stops the agent and births a new chat.
S11 "go to X" typed. Navigates to X's newest chat by the name shown in the
tree.
S12 Settings round trip. A change persists in the daemon and across reload.
S13 Look popover changes theme and closes.
S14 Keyboard-only. Every control reachable, focus stays put after actions.
S15 Phone 390 px. Drawer opens, a tap opens a chat and closes the drawer,
chips show the project.
S16 Reload mid-turn. The running turn is still shown as running and its reply
arrives.
S17 Daemon restart. The page says the link is lost, reconnects, restores.
S18 Stop. Stop acts on the conversation on screen only.
S19 Ghost chats. No zero-message "(nothing was said)" rows are left behind.

## 2. Plan (as run)

Delete probes on fabricated rows (`zzdel1..8`) -> add TMP projects
e2ealpha/e2ebravo -> new chat + pwd turn in each -> switch mid-turn ->
busy confirm (stale and fresh) -> rewind -> search -> rename/remove/root
remove -> typed "go to" x3 -> settings -> look -> collapse + reload ->
keyboard -> phone -> reload mid-turn -> kill/relaunch daemon -> Stop from
another chat. Seven typed messages: four model turns, three reflex refusals or
navigations, plus one final owl turn for S18 (eight total).

## 3. Results

| # | Criterion | Result | Evidence |
|---|---|---|---|
| S1 | Tree marks daemon's current chat; header names project | PASS | j01; DOM live id == API current |
| S2a | "+ New chat" in tree births in that project | PASS | j02; API 556410 e2ealpha |
| S2b | Strip New chat (phone) births in current project | PASS | p04; API d3ec1b e2ealpha |
| S3a | Typed turn answers; cwd is the project | PASS | j03-turn-done (e2e-proj-a), j03-bravo-done (e2e-proj-b) |
| S3b | Own running turn shows on row / project dot | FAIL | j03-turn-running: no `.rowstatus`, no dot while API said running |
| S3c | Header title updates after first turn | FAIL | j03-turn-done: "current conversation" until reload |
| S3d | Reply text renders intact | FAIL | "Listing current directory.E:/PROJECTS/..." -- two segments joined without a space, in the daemon record too |
| S4a | Opening another chat shows its transcript | PASS | j04 |
| S4b | Agent cwd follows the chat across projects | PASS | turn 3 in alpha after bravo printed e2e-proj-a |
| S4c | An idle chat on screen shows idle while another runs | FAIL | j05: bravo (done) shows "thinking", Stop button, "Working on it 0:06"; alpha row unmarked |
| S5 | Expand/collapse persists, moves nothing | PASS | j15; `expanded_projects` matches; current unchanged |
| S6a | One press arms, times out | PASS | d1 |
| S6b | Delete current rotates in same project | PASS | d8 (butler), p05 (e2ealpha -> 4bd954) |
| S6c | Confirm resists double-click / double-tap / held Enter | FAIL | section 0 |
| S6d | Row deleted on another device | PARTIAL | j16: error banner with raw id `no conversation 'chat-...-zzdel2'`, dead row stays |
| S7 | Rewind | PASS | j06: copy fc8513 (2 turns), 556410 cut to 1, prompt in composer, not sent |
| S8a | Content search | PASS | j08-search-content: "lighthouse" -> fc8513 with snippet, project, age |
| S8b | Cross-project results | PASS | j08-search-cross: OWNER and butler rows |
| S8c | Escape clears; no-match state | PASS | tree restored; j08-search-empty |
| S9a | Add project | PASS | j07-added; API rows |
| S9b | Rename | PASS | API label e2echarlie (screenshot lost to a browser kill) |
| S9c | Removed project's chat shown struck through | PARTIAL | j10-removed: row vanished; only after reload j10-orphan-after-reload |
| S9d | Root cannot be removed | PARTIAL | j10-root-remove: refused with a message, but "remove" is offered |
| S10a | Confirm on "+ New chat" while own turn runs | FAIL | j09-busy-confirm: no dialog, chat 1937e7 born while API busy=True |
| S10b | Cancel changes nothing | PARTIAL | API unchanged after Cancel; the dialog itself was not captured on that attempt |
| S10c | Confirm stops and births | PASS | j09-confirm-accepted; but it asked about an agent the daemon already reported done (j09-confirm-2) |
| S10d | New chat shows only its own transcript | FAIL | j09-after-alpha-finished: empty 1937e7 (API 0 msgs) displays alpha's whole story |
| S11a | "go to project e2ealpha" (tree label) | FAIL | j11-goto-typed: "I do not have a project called e2ealpha" |
| S11b | "go to project e2e-proj-a" (folder name, hyphens) | FAIL | j11-goto-dirname: refused |
| S11c | "go to project e2e proj a" | PASS | j11-goto-spaced: opened 556410; DOM == API |
| S12 | Settings round trip | PASS | j12: tts_speed 1.1 in API and after reload; reverted to 1.3 |
| S13 | Look popover | PASS | j14-look-popover2, j14-light-theme; reverted to dark. First press after closing settings opened nothing |
| S14 | Keyboard-only | PARTIAL | Tab order sensible; focus falls to BODY after opening a chat and after a delete |
| S15 | Phone drawer journeys | PASS | p02, p03, p04-phone-chip-owner |
| S16 | Reload mid-turn | FAIL | j13: "no reply came back for this turn." while API running; pill "standing by"; row frozen "running - 0:06"; footer clock never stops (still "Working on it - 1:06" in j09-confirm-accepted on a new empty chat) |
| S17 | Daemon restart | PASS | j18-daemon-down ("link lost", "Disconnected"), j18-daemon-back (reconnected, current 4bd954 restored) |
| S18 | Stop acts on the chat on screen | FAIL | j19: Stop pressed while viewing idle zzdel1 killed alpha's owl turn (API 556410 idle, no assistant message) |
| S19 | No ghost "(nothing was said)" rows | FAIL, FIXED 2026-09-26 | section 4 |
| - | Attachments, voice/wake, tablet, screen reader | NOT RUN | |

**Totals: 21 PASS, 12 FAIL, 5 PARTIAL, 1 group NOT RUN.**

## 4. Ghost chats (owner report) -- reproduction only

**FIXED 2026-09-26** ("fix(chats): a conversation with no messages is never
written"). The reap on `rotate` was a patch on one exit; the rule now lives in
`ChatStore._write`: a record with no messages is never on disk, so a newborn
exists only in memory until its first message and leaving it by ANY path
leaves nothing behind. `listing()` adds the current newborn from memory, boot
removes legacy empty records loudly (`_purge_empty`), and the page's
`openChat` re-fetches the list instead of repainting the cached ghost row.
Re-driven on a scratch daemon: New chat then open another, delete-current,
reload and daemon restart left zero "(nothing was said)" rows and zero empty
files. Tests: `test/test_no_empty_chat_on_disk.py`. The reproduction below is
kept as the record of the defect; its line numbers are pre-fix.

[MEASURED + READ] `ChatStore.rotate` writes the fresh record at once
(daemon/chats.py:795) and reaps the OUTGOING chat only if it is empty
(chats.py:796). `ChatStore.open` (chats.py:366-394) only moves the pointer and
reaps nothing. So an empty chat is discarded when you leave it by New chat,
and left on disk forever when you leave it by opening another chat.

- New chat, then open another chat: GHOST. bad867 (new, then Enter on zzdel4),
  01210d (confirm-born, then opened 1937e7), d3ec1b (phone New chat, then
  opened 1937e7) all stayed as "(nothing was said)" rows (API `turns 0`).
- Delete the CURRENT chat: the rotation mints an empty current chat (9dd9b0,
  4bd954). It is reaped if the next act is New chat (9dd9b0 vanished) and
  becomes a ghost if the next act is opening another chat (same path as above).
  The owner's log shows exactly that: delete X, then "page opened" another.
- New chat, then another New chat: no ghost (four API births left one file).
- Daemon restart: did not mint on its own; the empty current 4bd954 survived
  and was restored as current (j18-daemon-back). The owner's own state held
  two empty chats minted 4.6 s apart (636671, 645745) shortly before the copy;
  their cause was not established.

## 5. Findings

### UI

- HIGH, S10d: an in-flight reply streams into a DIFFERENT, newly born chat's
  transcript (j09-after-alpha-finished). DOM disagrees with the daemon.
- HIGH, S16: reload mid-turn draws "no reply came back for this turn."
  (`closeItem`, app.js:1621-1628) for a turn the daemon is still running, drops
  the Stop control, and leaves a footer clock that never stops.
- MEDIUM, S4c: turn state is page-global; an idle chat shows "thinking", Stop
  and "Working on it" while another chat runs (j05).
- MEDIUM, S3b/S9c/S3c: the tree and header refresh only at turn END
  (`loadChats`/`loadShortcuts`, app.js:2741-2742) and not after a project
  remove: no running mark on your own turn, removed-project chats vanish until
  reload, header keeps "current conversation".
- MEDIUM, S3d: reply segments around a tool call are concatenated with no
  separator ("directory.E:/..."); present in the daemon record, so upstream of
  the page (harness text assembly) [inferred, source line not located].
- LOW: phone header "e2ealpha" is overlapped by the "standing by" pill (p03);
  "conversation deleted" / "new conversation started" notes linger in the empty
  state for minutes (j07-added); two rewind siblings share one title (j06);
  "no match" and "nothing matched" both shown (j08-search-empty).

### UX

- HIGH, S6c: delete confirm has no dwell; double-click, double-tap or a held
  Enter deletes permanently (app.js:2181-2182). [MEASURED]
- HIGH, S18: Stop sends `{type:"stop", turn: currentTurn}` from page-global
  state (`stopTurn`, app.js:4424-4431) and killed a background agent from an
  unrelated chat, against the parallel-agents ruling that Stop acts on the
  conversation on screen.
- HIGH, S10a: the busy confirm reads `row.busy` from the page's cached
  shortcuts (`gateNewChat`, app.js:5534-5536), refreshed only at turn end, so
  it is skipped exactly when you just started the turn, and shown for an agent
  that already finished after a reload (j09-confirm-2).
- MEDIUM, S11: "go to X" matches only the folder name with hyphens read as
  spaces (`project_name`, daemon/settings.py:1585-1594) while the heard name is
  not normalized the same way (`match_project`, settings.py:1631-1635). The
  tree label is never matched, so a relabelled project ("classes" ->
  CRS101 on the owner's own list) is unreachable by the name on screen, and a
  typed hyphenated name never matches.
- MEDIUM, S19: ghost empty chats (section 4). FIXED 2026-09-26.
- LOW, S9d: root "remove" is offered and then refused; hide or disable it.
- LOW, S6d: opening a row deleted elsewhere shows a raw-id error and keeps the
  row; the delete path already treats 404 as benign and refreshes.
- LOW, S14: focus is lost to BODY after opening a chat or deleting a row.
- LOW [OPINION]: on a phone every row carries a full-opacity trash icon (p02),
  eleven destructive targets 3 px apart; "Wave placement" is shown while the
  voice effect is not the lattice (j14); the `harness_cwd` help text reads as
  where every agent starts, though every chat now carries its project.

### Test infrastructure

- Copying `state/` into a scratch dir (as `TMP/launch_review.py` does) keeps
  each chat's absolute `sessionRef` pointing into the OWNER's `state/chats/`,
  and `ChatStore.session_ref` reuses a stored ref (chats.py:628). A scratch turn
  or rewind in a copied chat would write or rename the owner's pi session file.
  This audit repointed the refs before any turn (`TMP/e2e-fix.py`).
  FIXED 2026-09-26: the record now stores only the minted FILE NAME and the
  store joins it onto its own root at use; legacy absolute refs are re-rooted
  at boot (`test/test_session_ref_rooted.py`, `daemon/AGENTS.md`).
- Both playwright browser sessions were closed externally twice mid-run (no
  browsers listed afterwards); one screenshot (j10-renamed) was lost.

## 6. Not tested

Attachments and camera, any spoken path (wake word, dictation, barge-in),
real TTS playback, a tablet, a screen reader, `harness_tools` full (forbidden),
the per-project cap with two projects busy at once, and live multi-device sync
beyond the stale-delete case.

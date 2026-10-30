# Rich chat -- showing files, charts and tables in the conversation

Owner's ask, 2026-10-08, after seeing an earlier prototype's `show_ui` (an earlier prototype's commit
`792c9fae`, "the agent can show tables, charts and figures"):

> the agent should be able to SHOW in the Butler chat images, charts, text,
> code, CSV, audio and video; every other file type appears as a download link.

Criteria AC-613..AC-640 are RESERVED here. They stay a proposal until the owner
accepts this spec and promotes them into `plan.md`.

**Status: BUILT 2026-10-08** (R1-R5; R0's mock was skipped: the owner said
"go", and the build was judged from screenshots of the real page at 1440 and
390 px against a scratch daemon). The owner's rulings are in section 0;
section 11 is the contract the daemon and the page are built against.
Real-world checks still open are in `docs/backlog.md`.

---

## 0. Owner rulings (2026-10-08)

> "1. reverse. 2. allow images and everything and ALSO on explorer. and allow
> drag to upload to chat or explorer as well. 3. thats fine. 4. ok. go."

- Item 1 REVERSED as proposed (RC-8).
- Item 2 WIDENED: the file explorer previews every kind too (FE-4 is
  overturned, not kept), and a file DRAGGED onto the chat attaches it, onto
  the explorer uploads it into the project folder (RC-12, RC-13).
- Item 3 accepted. O-1 and O-2 accepted at their defaults.

What was asked:

1. **Reverse AC-94's "NEVER img", narrowly.** Today the page may never create
   an `img` (`web/app.js:990-992`, `web/AGENTS.md:217-218`), because an image
   would be an outbound request authored by whatever a coding agent read on
   disk. Proposed rule (RC-7): `img`, `audio` and `video` may exist ONLY with a
   `src` the page builds itself from Butler's own artifact route. A URL from
   model text still never becomes an element source.
2. **Extend FE-4 ("text preview only", `docs/file-explorer.md:293`) for the
   chat only.** (Ruled wider: the explorer too.)
3. **Accept the read surface (section 8).** Artifacts are served to the tailnet
   the way `/api/chats` already is (R2, `docs/file-explorer.md:250`), so a phone
   can see them.
4. **Open questions O-1 and O-2** (section 10): defaults are chosen; the owner
   may overrule.

---

## 1. Why an earlier prototype's code cannot be ported [READ]

An earlier prototype's `show_ui` takes `{title, source}` where `source` is OpenUI Lang over
nine components, rendered by a React chunk built with a bundler (commit
`792c9fae`, `package.json` changed). Butler's page has no build step, no npm
and no CDN (`web/AGENTS.md:20-21`). What ports is the RULE, not the library:
the server refuses any layout the renderer would not draw as written, and says
why, so the model can fix it and retry.

---

## 2. What exists today [READ]

- **Markdown renderer.** Closed element set built with `createElement` and
  `textContent`, never `innerHTML` (`web/app.js:984-1000`, `web/AGENTS.md:
  214-218`). Links via `mdLink` (`web/app.js:1069`), http/https only.
- **The answer is rebuilt on every piece.** `paintAnswer` (`web/app.js:1891`)
  rebuilds `.answered` whole; anything placed inside it is lost. `markStopped`
  (`web/app.js:1764`) already solves this by placing its note AFTER
  `.answered`. `historyItem` is at `web/app.js:1677`, `loadSession` at
  `web/app.js:1950`.
- **SVG without markup.** The icon helper builds SVG with `createElementNS`
  (`web/app.js:1332-1335`). Charts reuse that idiom.
- **Byte sniffing.** `daemon/attachments.sniff` (`daemon/attachments.py:128`)
  decides kind from magic bytes (`_MAGIC`, line 58: png, jpeg, gif, pdf;
  webp at line 138), then a text-extension table (`_TEXT_EXTS`, line 73).
  It knows no audio or video.
- **Containment.** `whatsapp.records_dirs` and `whatsapp.file_inside`
  (`daemon/whatsapp.py:1308`, `:1324`) decide whether a file an agent names
  may leave the machine: inside a project or the data root, never the records,
  the TLS folder or `<data>/.butler`. `files.SECRETS` (`daemon/files.py:56`)
  is the explorer's denylist. `files.MAX_PREVIEW` is 256 KiB
  (`daemon/files.py:44`).
- **The message record.** `ChatStore.append` (`daemon/chats.py:1162`) writes
  `{id, role, content, timestamp}` plus `stopped` only when true (lines
  1199-1203). `ChatStore.delete` is at `daemon/chats.py:819`.
- **Records paths.** Every record path is a `Paths` property
  (`daemon/paths.py`, e.g. `library` at line 60, `whatsapp` at line 115).
- **How agent tools reach the daemon.** The pi extension
  (`daemon/adapters/pi_memory.ts`) POSTs to the daemon with `LOCAL_HEADER`
  (`readTool`, line 208). The endpoint brain takes its tools by capability
  flag (`NEEDS_MEMORY`, `NEEDS_WHATSAPP`, `daemon/adapters/brain_endpoint.py:
  446-459`). pi's `--tools` gates extension tools too, so every tool must be
  named there (`daemon/butlerd.py:1812-1815`) or it is silently dropped.
  `test/test_whatsapp_read.py:273-282` is the parity test that pins the
  extension's declarations to the daemon's.
- **Guest turns** get no tools of any kind (`docs/messaging.md:293`).
- **Owner attachments** are pending on the session and consumed by the next
  turn (`docs/attachments.md` section 1); the page drops its chips after
  sending and the record keeps no field for them (`web/app.js:3733-3770`).
- **Probe.** `test/manual/probe_ux.py:534` asserts `#history img` count is 0.

---

## 3. Decisions

- **RC-1. Two display-only tools.** `show_file(path, caption?)` shows one file
  the agent made or found. `show_ui(title, blocks)` shows a small layout from a
  CLOSED component set (RC-9). Neither changes anything on disk outside the
  artifact store, and neither returns file content to the model; the answer is
  a short line ("shown: chart.png, image, 412 KB") or the refusal reason.
  *Losing option:* one tool with a `kind` switch -- two tools keep each schema
  small enough for a small model to fill correctly.
- **RC-2. Offered where a conversation record exists, never to guests.** The
  pi extension (a POST naming its `--butler-chat`); both names join the
  `--tools` list at `daemon/butlerd.py:1814`. A parity test pins the
  extension to the daemon (the `test_whatsapp_read.py` shape). [DECIDED
  2026-10-08, at build] NOT the endpoint brain: endpoint mode keeps no
  conversation record (`GET /api/session/current` answers 404 there,
  `daemon/hub.py:2334` `session_brain`), so a snapshot would have no message
  to belong to and would vanish on reload, which is what RC-3 exists to
  prevent.
- **RC-3. Snapshot, not reference.** The daemon COPIES the file into
  `<records>/artifacts/<chat>/<id>.<ext>` (a new `Paths.artifacts` property).
  A reload, a phone, or a later edit or delete of the original still shows
  what was shown. The folder is removed with its chat in `ChatStore.delete`.
  *Losing option:* serve the original path -- it changes or vanishes under the
  record, and the route would have to re-check containment on every read.
- **RC-4. One optional field on the assistant message.** `artifacts: [{id,
  kind, media, name, size, caption?, truncated?}]`, written only when present
  so every other message stays byte-identical (the `stopped` precedent). Served
  unchanged by `GET /api/chats/{cid}` and `GET /api/session/current`. A live
  turn gets a new daemon-to-satellite frame `{type:"artifact", turn, chat,
  artifact}`; `plan.md` 4.3's frame list gains it in the same commit.
- **RC-5. Text never depends on an artifact.** The spoken reply, WhatsApp and
  Telegram text stand alone; an artifact is never read aloud and never
  referenced as "see above" by Butler's own wording.
- **RC-6. The kind comes from the BYTES.** `sniff` is extended with audio (mp3:
  `ID3` or frame sync; ogg: `OggS`; wav: `RIFF....WAVE`; m4a/aac: `ftyp` with
  an audio brand) and video (mp4: `ftyp` with a video brand; webm: EBML
  `1A 45 DF A3`). Text kinds need valid UTF-8 plus the extension table, split
  into markdown, CSV/TSV, JSON and code. Anything else is a `file` (download
  link). A name that disagrees with its bytes loses.
- **RC-7. What each kind becomes on the page.**

  | kind | shown as | cap |
  |---|---|---|
  | image (png, jpeg, webp, gif) | inline `img`, click opens full size | 20 MB |
  | svg, html | CODE text, never rendered (script-capable) | 256 KiB preview |
  | markdown, plain text | the existing markdown renderer | 256 KiB preview |
  | code, json | `pre > code` | 256 KiB preview |
  | csv, tsv | a table, header row, first 200 rows, "N more rows" | 256 KiB preview |
  | audio (mp3, ogg, wav, m4a) | native `audio controls` -- SUPERSEDED, see below | 100 MB |
  | video (mp4, webm) | native `video controls` | 100 MB |
  | anything else (pdf, office, zip, ...) | download link: name, size, type | 100 MB |

  A text kind past 256 KiB shows the preview plus "download full"
  (`truncated: true`). Over a cap, `show_file` is refused with the number.
  PDF and Office rendering are out of scope (owner, 2026-10-08).
  SUPERSEDED for audio 2026-10-27 by `docs/lantern-ui.md` CU-16 (Step 6c):
  a styled player in `artifacts.js` (play button, seek slider, time; the
  duration from the element's own metadata; no waveform) over an `audio`
  element with no `controls`. Every artifact is drawn as a card with a head
  line (name, type and size; an image adds full size and download links).
  Video keeps native controls.
- **RC-8. The page builds every source.** `img`, `audio` and `video` are
  created only by `web/artifacts.js`, with `src` set to
  `/api/artifacts/<chat>/<id>` composed from the record's ids. No string from
  model text is ever an element source. The artifacts sit in a container
  AFTER `.answered` in the bot turn (the `markStopped` rule), so a streamed
  piece never erases them.
- **RC-9. `show_ui` is JSON over a closed set, validated in Python.**
  Components: `Card(title, blocks)`, `Text(markdown)`, `Stat(label, value,
  unit?, delta?)`, `Table(columns, rows)`, `List(items)`, `BarChart(title,
  labels, series)`, `LineChart(title, labels, series)`. Bounds: depth 3, 50
  blocks, 200 table rows, 12 series, 500 points, 64 KiB of JSON. An unknown
  component, a missing field, a wrong type or a bound crossed is refused with
  the path and reason ("blocks[2].series[0].values: expected numbers"). The
  validated layout is stored as a `ui` artifact (the JSON itself), so it
  replays like a file.
- **RC-10. Charts are hand-drawn SVG.** `createElementNS`, theme tokens for
  colours, axis labels via `textContent`. No chart library, no `innerHTML`.
- **RC-12. The explorer previews every kind.** `GET /api/project/{id}/file`
  gains `kind` and `media` (the RC-6 classifier); a new `GET /api/project/{id}/raw`
  serves the bytes under the explorer's own rules (`daemon/files.py` row id,
  lexical, resolve, SKIP, SECRETS) with RC-4's headers. The page draws the
  same renderer the chat uses.
- **RC-13. Drag to upload.** A file dropped on the conversation goes through
  the existing attachment route (`POST /api/attach`, AC-197), exactly as the
  upload button does. A file dropped on the explorer is written into the
  folder it was dropped on by a new `POST /api/project/{id}/upload`: the name is one
  component under `files.clean_relative`'s rules, never a SKIP or SECRETS
  name, never over an existing file (409), at most 100 MB read in chunks with
  the read ABORTED past the cap (413). This is a tailnet-open WRITE into a
  project folder (R2, the owner's item 3): stated in section 8.
- **RC-11. One containment guard.** `records_dirs` and `file_inside` move from
  `daemon/whatsapp.py` to a shared module (proposed `daemon/containment.py`);
  the WhatsApp send and `show_file` call the same function, plus a refusal for
  `files.SECRETS`. *Losing option:* a second copy for artifacts -- two guards
  drift, and the WhatsApp one already paid for its review (RV-2, AC-587).

---

## 4. Serving

`GET /api/artifacts/{chat}/{id}`, and `?download=1`:

- The chat and artifact ids are KEYS looked up in the record, never path
  components (`ChatStore.delete`'s rule): a traversal is unrepresentable.
- `Content-Type` is the sniffed media type; text kinds are served as
  `text/plain; charset=utf-8` whatever their extension. Always
  `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`.
- `Content-Disposition: inline` for shown kinds, `attachment` for `file`
  kinds and for every `?download=1`. SVG and HTML are always `attachment`.
- HTTP Range (206) so a video seeks and iOS plays it.
- 404 for an unknown chat or id; never a hint about which half was unknown.

---

## 5. Criteria (proposed, AC-613..AC-640)

Daemon:

- **AC-613** `show_file` on a file inside a project copies it to
  `<records>/artifacts/<chat>/<id>.<ext>` and the assistant message gains an
  `artifacts` entry with the sniffed kind.
- **AC-614** `show_file` on a path in the records folder, the TLS folder,
  `<data>/.butler`, outside every project and the data root, or matching
  `files.SECRETS`, is refused naming the rule; nothing is copied.
- **AC-615** The WhatsApp send and `show_file` call the SAME containment
  function (a caller assertion, D-18).
- **AC-616** A link or junction from inside a project into the records is
  refused (resolved paths compared).
- **AC-617** `sniff` names mp3, ogg, wav, m4a, mp4 and webm from bytes; a
  `.png` name on mp4 bytes is a video.
- **AC-618** Text kinds require valid UTF-8; a `.csv` name on binary bytes is a
  `file`.
- **AC-619** A file over its kind's cap is refused with the cap in the message.
- **AC-620** A text kind over 256 KiB is stored whole and marked `truncated`.
- **AC-621** Editing or deleting the original after `show_file` does not
  change what the artifact route serves.
- **AC-622** Deleting a chat deletes its artifact folder.
- **AC-623** A message without artifacts is byte-identical to today's record.
- **AC-624** A live turn emits one `artifact` frame per artifact, carrying the
  turn and chat ids; `plan.md` 4.3 lists the frame.
- **AC-625** `show_ui` refuses an unknown component, a wrong type and each
  bound in RC-9, each with a path-and-reason message; a valid layout is
  stored as a `ui` artifact.
- **AC-626** Both tools are offered to pi and the endpoint brain, named in
  `--tools`, and pinned by a parity test; a guest turn is offered neither.
- **AC-627** The spoken text of a turn with artifacts contains no artifact
  content.

Route:

- **AC-628** The route serves the sniffed type with `nosniff` and
  `Content-Security-Policy: sandbox`; text kinds are `text/plain`.
- **AC-629** SVG, HTML, `file` kinds and `?download=1` are served as
  `attachment`.
- **AC-630** A Range request answers 206 with the right bytes.
- **AC-631** An unknown chat id, an unknown artifact id, and an id containing
  `..` or a slash all answer 404 and read no file.

Page:

- **AC-632** `img`, `audio` and `video` in `#history` exist only with a `src`
  starting `/api/artifacts/`; a reply whose text contains `![x](http://...)`
  or a raw `<img>` produces none.
- **AC-633** Artifacts render after `.answered` and survive further streamed
  pieces and a stop.
- **AC-634** A reload (`loadSession`) draws the same artifacts as the live turn.
- **AC-635** CSV renders as a table capped at 200 rows with an "N more rows"
  line and a download link.
- **AC-636** SVG and HTML artifacts render as code text.
- **AC-637** A `file` kind renders as a link with name, size and type, and the
  link carries `download`.
- **AC-638** `BarChart` and `LineChart` render as SVG built with
  `createElementNS`; `web/` contains no `innerHTML` (existing grep gate).
- **AC-639** `Stat`, `Card`, `Table`, `List` and `Text` render from the closed
  set only; an unknown component in a stored record draws a plain "cannot show
  this" note and never throws.
- **AC-640** Every new string in `web/` and the spec is ASCII
  (`test_the_shipped_client_is_ascii`).

---

## 6. Scenarios

```gherkin
Feature: the agent shows things in the chat

  Scenario: an image the agent made
    Given a project chat whose agent wrote "out/chart.png"
    When the agent calls show_file("out/chart.png")
    Then the bot turn shows the picture under the answer text
    And reloading the page shows the same picture
    And the spoken reply does not describe the file's bytes

  Scenario: the original is deleted afterwards
    Given the agent showed "out/chart.png"
    When the file is deleted from the project
    Then the chat still shows the picture

  Scenario: a secret is refused
    When the agent calls show_file(".env")
    Then the tool answers with the refusal rule
    And nothing is copied and no artifact frame is sent

  Scenario: the records folder through a junction
    Given a junction inside the project pointing at Butler's records
    When the agent calls show_file on a file through it
    Then the tool refuses it

  Scenario: a CSV
    Given a 5,000-row CSV
    When the agent shows it
    Then the chat shows the header and 200 rows, "4,800 more rows" and a download link

  Scenario: an SVG is not rendered
    When the agent shows "logo.svg"
    Then the chat shows its source as code
    And the route serves it as an attachment

  Scenario: a video on the phone
    Given the owner's phone on the tailnet
    When the agent shows "demo.mp4"
    Then the phone plays it with native controls and can seek

  Scenario: a PDF is a link
    When the agent shows "report.pdf"
    Then the chat shows "report.pdf, 1.2 MB, PDF" as a download link

  Scenario: a model writes an image URL in its text
    When a reply contains "![x](https://example.com/a.png)"
    Then no img element exists in the history

  Scenario: an invalid layout
    When the agent calls show_ui with a BarChart whose values are strings
    Then the tool answers "blocks[0].series[0].values: expected numbers"
    And nothing is shown

  Scenario: a guest on WhatsApp
    Given a guest reply turn
    Then neither show_file nor show_ui is offered
```

---

## 7. Build plan

Red-green per phase; each phase is one commit or a short series.

| phase | builds | gate |
|---|---|---|
| R0 | look mock `web/mocks/rich-chat-r0.html` (captured payloads, every kind, light and dark) | owner approves the look |
| R1 | `daemon/containment.py` (moved guard, WhatsApp re-pointed); `sniff` extended; `Paths.artifacts`; artifact store; the route with headers and Range | AC-613..AC-623, AC-628..AC-631 |
| R2 | `show_file` in pi and the endpoint brain; `artifacts` field; `artifact` frame; `--tools`; parity test | AC-624, AC-626, AC-627; full suite |
| R3 | `web/artifacts.js` (the `library.js` fetch pattern): image, text, markdown, code, CSV, audio, video, link; `web/AGENTS.md` and `probe_ux.py` changed in the same commit | AC-632..AC-637, AC-640; full suite |
| R4 | `show_ui` validation; SVG charts, stat tiles, cards | AC-625, AC-638, AC-639 |
| R5 | O-1 phone delivery; O-2 owner attachments in the transcript | criteria written when the owner rules |

Mutations (D-13) required before R1 and R3 close: break the containment
guard (drop the records check) and watch AC-614/AC-616 fail; let
`artifacts.js` take a `src` from the record's `name` field and watch AC-632
fail.

---

## 8. Security

- **Read surface.** Anyone on the tailnet who can reach `/api/chats` today can
  read a chat's artifacts. That is R2's accepted cost, widened from text to
  files the agent chose to show. Owner rules on it (section 0 item 3).
- **No outbound request from the page.** Every element source is Butler's own
  route; a model-authored URL never becomes one (RC-8, AC-632).
- **No active content.** SVG and HTML are never rendered; the route sends
  `nosniff`, `CSP: sandbox` and `attachment` for them.
- **What may be shown** is decided once, by the shared guard (RC-11), against
  resolved paths; the secrets denylist is incomplete by construction
  (`daemon/files.py:54-55` says so), so the guard's root rule carries the
  weight.
- **Upload is a write.** `POST /api/project/{id}/upload` lets any tailnet caller
  add (never replace) a file in a project folder. Accepted by the owner
  (2026-10-08, item 3); a file it writes is inert until something runs it.
- **Disk.** Snapshots cost space (caps in RC-7); they are deleted with their
  chat. No global quota is proposed; this is an accepted cost to name.

---

## 9. What does NOT change

- The markdown renderer's element set gains nothing; artifacts render in their
  own container.
- No build step, no library, no browser storage.

---

## 10. Open questions (defaults chosen)

- **O-1.** On a phone-origin turn (WhatsApp or Telegram), should `show_file`
  ALSO send the file there (image, audio and video inline, anything else as a
  document)? Default: YES, through the existing reply path, with the same caps
  as that channel.
- **O-2.** Should the owner's OWN attachments, sent from the page, also show in
  their turn (thumbnail or link)? Today they vanish after sending. Default:
  YES, the same renderer and the same artifact store.

---

## 11. Contract (what the daemon serves and the page draws)

Artifact entry, the same in the record, the frame and the route answers:

```
{id: "art-<hex>", kind, media, name, size, caption?, title?}
kind: image | audio | video | markdown | text | code | csv | ui | file
```

- Record: the message's `artifacts: [entry, ...]`, assistant messages
  (`show_file`, `show_ui`) and user messages (O-2: the owner's attachments).
  Absent when empty.
- Frame (daemon to satellite, broadcast, `plan.md` 4.3):
  `{type:"artifact", chat, role:"assistant"|"user", artifact: entry}`.
- `GET /api/artifacts/{chat}/{id}`: the bytes. `?download=1` forces
  `attachment`; `?preview=1` answers at most 256 KiB as
  `text/plain; charset=utf-8` with `X-Butler-Truncated: 1` when cut. A `ui`
  artifact answers its validated JSON (`{title, blocks}`).
- `GET /api/project/{id}/file` (existing preview) gains `kind` and `media`.
- `GET /api/project/{id}/raw?path=<rel>[&download=1]`: the bytes.
- `POST /api/project/{id}/upload?path=<dir rel>&name=<file>`, body is
  the file: 200 `{path, size}`, 409 exists, 413 too big, 400/403/404 by the
  explorer's rules.
- The page builds every `src` and `href` for these from ids and paths it
  already holds; the only element sources allowed are `/api/artifacts/` and
  `/api/project/{id}/raw`.

`show_ui` blocks (validated by the daemon before anything is stored; the
page may trust the shape and still renders every string as text):

```
{type:"Card", title, blocks:[block...]}           # nesting depth <= 3
{type:"Text", markdown}                           # the chat's markdown renderer
{type:"Stat", label, value: number|string, unit?, delta?: number}
{type:"Table", columns:[string], rows:[[string|number|null]]}
{type:"List", items:[string]}
{type:"BarChart"|"LineChart", title?, labels:[string],
 series:[{name, values:[number|null]}]}           # each values as long as labels
```

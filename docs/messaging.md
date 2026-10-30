# Messaging: WhatsApp and two-way Telegram (PROPOSAL)

Written 2026-10-06 against HEAD `e475f76`. Nothing here is accepted:
AC-500..AC-539, AC-560..AC-563 and AC-586..AC-612 are RESERVED for this spec. It is built between phases P2 and
P3 of its sibling `docs/proactive.md`, by the owner's build order.

WhatsApp is a PORT of an earlier prototype's implementation, not a new design: every
behaviour below that says "as an earlier prototype" is pinned by a file and line in
`.references/prototype-whatsapp.md` (E:/PROJECTS/prototype at the state read
on 2026-10-06), and the ported tests come with it. Butler-side attach points
are in `.references/butler-integration-map.md` (R5, R8). Spec-local decision
ids are MS-n.

Provenance tags as `docs/proactive.md`.

## 0. The owner's decisions (2026-10-06) [OWNER]

- **"Full WhatsApp integration EXACTLY like" an earlier prototype and `/whatsapp-skill`**:
  linking, archive, the self-chat agent, guarded sends, media and voice
  notes, and the agent's read/search/send tools.
- **Standalone, per user.** "I'm developing butler mostly for other users
  ... so i need full whatsapp capability standalone in Butler, so other
  users, on other machines, can connect their whatsapp in there." Butler
  does not use an earlier prototype at runtime; each Butler links its own user's number
  by a QR code on its settings page.
- **Development and tests run on Sam's laptop Butler, with her real
  number.** A ban or a bug there costs a real person's account.
- **Keep it simple: Butler sends AND replies to other people and groups**
  (second ruling, same day). "Whatsapp can send but also reply to other
  users and groups ... sensible defaults are fine." Section 6.1 is that
  feature; its defaults are this spec's [DECIDED].
- **WhatsApp is a true channel into the user's conversation** (third
  ruling, 2026-10-07; it replaces the first day's "one standing WhatsApp
  chat"). "Sam can send text or voice messages in portuguese or english
  and have the agent interact with her on the general chat." Which chat is
  a setting with both choices, default the root Butler chat; the reply form
  is a setting, default a voice note plus text, in the language received
  (MS-16, MS-18a).
- **Two-way Telegram** (R5) uses the same inbound design (section 7).

## 1. Why a port and not a library swap [READ]

Baileys `7.0.0-rc.9` in an earlier prototype carries four traps that each cost a real
link to discover, and each is pinned by a test there:
`fetchLatestBaileysVersion()` or WhatsApp answers 405 before any QR;
`markOnlineOnConnect: false` or the PHONE stops getting notifications;
`syncFullHistory: false` WITHOUT a `shouldSyncHistoryMessage` filter drops
every history sync (0 contacts after linking, and the initial sync comes once
per link); and `getMessage` wired to the archive or recipients see "This
message took a while". A Python client (for example neonize over whatsmeow, from memory)
would avoid a Node process but start that list from zero. Butler already
runs a Node sidecar for speech (`daemon/engines/tts/tts-sidecar.mjs`, Node
22.17 on the desk, 25.8 on the laptop [MEASURED 2026-10-06]), so a second
sidecar is a known shape, and `node:sqlite` (Node 22+) needs no native module.

## 2. Architecture

```
 phone (user's WhatsApp)
        |  WhatsApp multi-device
 [wa sidecar, Node, Baileys rc.9]  <-- loopback HTTP, per-boot token -->  [butlerd]
   socket, pairing, archive,                                     settings group, routes,
   send guard + audit, media,                                    phone-channel routing,
   inbound filter                                                STT, attachments, questions,
        |                                                        notify_whatsapp channel
   <records>/whatsapp/{session/, whatsapp.db, sent.jsonl}        pi tools, tools/wa.py
```

- **MS-1 The sidecar is a transport** [DECIDED]. `daemon/engines/whatsapp/`
  holds `wa-sidecar.mjs` and the modules ported from an earlier prototype (`whatsapp-store`,
  `whatsapp-pairing`, `whatsapp-send-guard`, `whatsapp-media`, the socket
  setup of `channels/whatsapp.js`), Baileys pinned to exactly `7.0.0-rc14`
  (from `7.0.0-rc.9` on 2026-10-29: a critical spoofing advisory) and `qrcode` in `package.json`. It owns the socket, linking, the archive
  (it is the ONLY writer), the send guard and its audit, media
  download/upload, and an earlier prototype's inbound filter. It knows nothing of chats,
  agents or speech.
- **MS-2 The daemon owns the sidecar** like the TTS one
  (`daemon/adapters/tts_kokoro.py`): spawns it, `childproc.assign`, binds it
  to `127.0.0.1:0` and reads the port back, passes a per-boot random token
  every request must carry, drains its output to the log, restarts it with
  backoff on a crash. It is started only when WhatsApp is enabled in
  settings.
- **MS-3 The daemon is always the client.** Commands are HTTP calls into
  the sidecar (`/pairing`, `/send`, `/media/<id>`, `/status`); inbound
  arrives on ONE long-lived NDJSON stream `GET /events` the daemon holds
  open. The sidecar never learns the daemon's port.
- **MS-4 Records** live under the data root, from `daemon/paths.py`
  (`Paths.whatsapp`): `session/` (Baileys auth, one JSON per key, SECRET:
  deleting it unlinks), `whatsapp.db` (archive, plaintext private
  conversations), `sent.jsonl` (audit). Never in the repo.

## 3. Linking (as an earlier prototype, on the settings page)

- **MS-5** A settings group "WhatsApp" with an enable switch
  (`whatsapp_enabled`, default off: nothing starts until the user links) and a pairing
  block. The page shows status (idle, connecting, awaiting scan, linked as
  +<number> <name>, disconnected, logged out, error, from an earlier prototype's pure
  reducer), and while awaiting a scan the QR code on a forced white plate
  (an inverted QR does not scan), polled every 2 s only in those states,
  with the phone steps: WhatsApp > Settings > Linked devices > Link a
  device. After two reissued QR codes it hints at WhatsApp's passkey gate
  without claiming it. AC-41 holds: the group declares
  `pairing="/api/whatsapp/pairing"` the way a group declares `collection`,
  and `web/settings.js` renders any such group generically.
- Buttons: Link (idempotent: never a second socket, which WhatsApp would
  count as another device), Disconnect (closes, keeps the link), Unlink
  (asks, then logs out and deletes `session/`).
- Socket options exactly as an earlier prototype except the device name, which shows
  "Butler" in the phone's Linked devices list.
- **MS-6 Reconnect, two fixes over an earlier prototype** [DECIDED]. An earlier prototype reconnects in
  3 s after any close but 401. Butler also treats 440 (connection replaced:
  the same session opened elsewhere) as terminal, shows "this link is open
  in another program", and never loops; 515 (restart required, normal after
  a scan) reconnects at once. 401 (logged out from the phone) shows "Unlinked
  from the phone: link again".

## 4. The archive and the agent's read tools

- **MS-7 Same schema, same ingest** as an earlier prototype: tables `contacts`, `chats`,
  `messages`, upserts with COALESCE, every message type and sender archived
  on a subscription separate from the routing one, wrappers unwrapped up to
  depth 3, text from conversation/extended/captions. A person may exist as
  both a phone JID and a LID row (an earlier prototype does not merge them; neither does
  this port: a known gap, section 11).
- **MS-8 Reading is gated** by setting `whatsapp_read` (default OFF, as
  an earlier prototype's `read_enabled`). When on, the agent gets an earlier prototype's four read
  tools under the same names: `whatsapp_list_chats`, `whatsapp_read_chat`,
  `whatsapp_search_messages`, `whatsapp_list_contacts`. They are offered in
  `daemon/adapters/pi_memory.ts` (pi children) and on the endpoint brain
  where `library.TOOLS` are, and they read through a loopback-only daemon
  route that opens the archive read-only (`mode=ro`). Name resolution
  REFUSES TO GUESS: zero or several matches return candidates.
- **MS-9 `tools/wa.py`** is the port of `/whatsapp-skill`'s `wa.py` for any
  other harness (claude, codex) and for the user's shell: the same eight
  subcommands (`status`, `inbox`, `read`, `search`, `chats`, `contacts`,
  `send`, `me`), the same `--since` grammar, the same exit codes (0 ok, 2
  needs confirmation or ambiguous, 1 everything else), stdlib only. It finds
  the archive through Butler's data root (the same resolution
  `daemon/paths.py` uses) and sends only through the daemon's door.
  `tools/wa-SKILL.md` is the skill text, with an earlier prototype's rules (ask before
  every send to anyone but the user, naming the recipient and text; never
  bulk; never someone who has not written first) adapted to "the user".
  Groups are allowed (MS-12) under the same ask-first rule.

## 5. Sending

- **MS-10 One door.** `POST /api/whatsapp/send` on the daemon, LOOPBACK ONLY
  (403 otherwise, as `/api/tasks/agent`), forwarding to the sidecar's guard.
  An earlier prototype's route is open to anything reaching port 3001; Butler's routes are
  open to the whole tailnet (R2), so loopback is the boundary here. Bodies
  as an earlier prototype: JSON `{recipient, text, confirm}` or multipart with exactly one
  file (100 MB cap), `confirm` true only as boolean `true` or the string
  `"true"`.
- **MS-11 Guard order exactly as an earlier prototype**: send switch (re-read on every
  send, no restart), resolve (ambiguous: 409 with candidates; unknown: 403),
  known contacts only (the user's own JIDs exempt), rolling per-minute rate
  limit charged only on real sends (default 10, 1..60), first send to each
  person per process held for confirmation (409 naming the person; the
  user's own chat exempt), every decision audited without the body.
  Not linked: 503. Socket error: 502, audited.
- **MS-12 One switch, default on** [OWNER: keep it simple]. Setting
  `whatsapp_send` (bool, default on) is an earlier prototype's kill switch. Contacts AND
  groups are valid recipients; a group counts as known when it is in the
  archive's chats. An earlier prototype's skill banned group sends by rule; Butler allows
  them through the same guard.
- **MS-13 The notification channel.** `daemon/adapters/notify_whatsapp.py`,
  kind `whatsapp`, registered in `ports.NOTIFIERS` like Telegram. Its one
  field `to` is `me` (default) or a known contact or group, resolved when the
  channel is saved (ambiguity refused with candidates) and stored as a JID.
  It sends through the same door, so rate limit and audit apply.
- Replies to a message that already passed the inbound filter (section 6)
  do not pass the send guard, as in an earlier prototype; guest replies have their own
  limits (MS-14b).

## 6. Inbound: the user talks to Butler from WhatsApp

- **MS-14 The filter, as an earlier prototype**: only `notify` deliveries route (the bot's
  own sends and offline-queued deliveries arrive as `append` and are
  archived, not answered), `fromMe` is answered ONLY in the user's own chat
  (matched by phone JID or LID, server part compared), group traffic needs
  an allowlist and a trigger, other DMs need the allowlist. Who is on the
  lists is section 6.1.
- **MS-15 Cross-talk guard** [DECIDED, new]. A second agent on the same
  number (an earlier prototype, or another Butler) sends to the same self chat, and those
  messages arrive as `notify` + `fromMe`, indistinguishable from the user
  typing; the two agents would answer each other forever
  (`.references/prototype-whatsapp.md` 8C). Butler ends every message it
  sends with an invisible marker (`\u2063`, INVISIBLE SEPARATOR, written as
  an escape in code) and ignores inbound self-chat messages that end with
  it. This stops Butler-to-Butler loops only; an earlier prototype does not know the
  marker, so the settings page also warns: "Do not link a number where
  another assistant already answers 'Message yourself'."
- **MS-16 Which chat: a setting** [OWNER]. `phone_chat`, shared by WhatsApp
  and two-way Telegram:
  - `root` (DEFAULT): the newest chat in the root Butler project, the
    everyday assistant conversation; created if there is none.
  - `current`: the chat the user last spoke or typed in on ANY device. The
    hub keeps `last_owner_chat`, updated on every owner turn (desk, page,
    phone), so a conversation started at the desk continues from the phone
    and back.
  Either way the message is an OWNER turn in that chat, not a separate
  WhatsApp conversation: the page shows it live with a small "via WhatsApp"
  mark, the brain sees it as the user's own message, and the brain
  dispatches project agents from it as from any turn. `/new` from WhatsApp
  starts a fresh chat where that mode points (a new root chat, or a new
  chat in the current chat's project). Guests never enter this chat
  (section 6.1).
- **MS-17 A turn.** The text becomes a typed turn on a headless session (the
  `RunSession` pattern, `hub.py:729`) attached to the MS-16 chat, with
  origin `whatsapp`. One turn at a time per chat: a message that arrives
  while that chat is answering anyone (desk, page or phone) queues in
  order, at most 10 (the 11th is answered "I am still working on your
  earlier messages"). While the turn runs the sidecar shows "typing". The
  answer goes back to WhatsApp in the MS-18a form, the text quoted to the
  triggering message and split at line boundaries past 65,536 characters.
  Nothing is spoken at the desk for a phone-origin turn: the user is on
  the phone.
- **MS-18 Media and voice notes.** Declared size over 100 MB is refused in
  the chat before download. A voice note is decoded with ffmpeg to 16 kHz
  PCM and transcribed by BUTLER's speech engine in 30 s windows (an earlier prototype's
  is English-only). Its language is decided the way a desk voice turn's is
  (`docs/language-auto.md`); a typed message's language comes from the same
  detector, `dialogue.detect_language` over the installed voices'
  languages. That language is stored on the turn: the brain answers in it
  and MS-18a speaks in it. Butler adds an earlier prototype's warning prefix: "[Voice
  note, auto-transcribed: may contain misheard words; ask if unclear]". No
  words:
  "I could not hear any words in that voice note." Images, documents and
  video go through `daemon/attachments.py` (type from magic bytes, documents
  read locally first); a video's audio track is transcribed. Every refusal
  or failure is said in the chat, quoted, because a silent drop reads as
  being ignored.
- **MS-18a The reply form: a setting** [OWNER]. `phone_reply`:
  - `voice_text` (DEFAULT): every reply is a voice note followed by the same
    answer as text, whatever she sent;
  - `mirror`: a voice note (plus text) only when she sent a voice note;
  - `text`: text only.
  The voice note speaks IN THE LANGUAGE RECEIVED (MS-18), with a Butler
  voice of that language (`voice_ids` filtered by language, AC-154; the
  user's chosen voice when it speaks that language). Kokoro PCM is encoded
  with ffmpeg to OGG/Opus mono and sent as a push-to-talk voice note. The
  spoken text is the reply passed through the desk's existing spoken-form
  cleaning (code, links and tables are not read aloud). A reply longer than
  about two minutes of speech speaks its first part and ends "the rest is
  in the text". If synthesis fails the text is still sent, followed by
  "(voice reply failed: <reason>)". Guest replies (section 6.1) are always
  text.
- **MS-18b Attachments both ways** [OWNER 2026-10-07: "whatsapp should also
  be able to receive and send attachments, images to be used against the
  vision model on butler"].
  - IN: an image, document or video from the user's own chat is saved
    through `daemon/attachments.AttachmentStore` exactly as a page upload is
    (type sniffed from magic bytes, `safe_name`), and attached to that owner
    turn, so images reach the model that answers (AC-815) and documents follow
    `document_reading`, by the same code a page attachment takes. The
    caption is the turn's text; an image with no caption is asked "What is
    in this image?" (as an earlier prototype). A video's audio track is transcribed (MS-18).
  - OUT: Butler can send a file or image with its reply or on its own. The
    agent tool `whatsapp_send` (pi extension and endpoint brain, beside the
    read tools) takes an optional `file`; the TOOL reads the bytes and
    uploads them through the loopback send door (the door never opens a
    caller-named path, as an earlier prototype). The file must lie inside the turn's
    project folder or the data root; anything else is refused naming the
    rule. To the user's own chat it needs no confirmation; to anyone else
    the MS-11 guard applies. Inline kinds as an earlier prototype (jpeg/png/webp images,
    mp4, ogg/mpeg/aac audio), everything else as a document; 100 MB cap.
  - Guests (6.1) neither send nor receive files: a guest's media gets "I
    can only read text here." and their turns stay brain-only.
- **MS-19 Messages missed while Butler was down** are archived but not
  answered (they arrive as `append`, as in an earlier prototype). Butler adds one line on
  reconnect when any self-chat messages arrived while it was away: "I was
  offline and did not answer N messages; send again what still matters."

## 6.1 Replying to other people and groups [OWNER, defaults DECIDED]

**What the user gets:** Butler answers chosen people and groups on the
user's behalf, says it is Butler, and steps back when the user replies
themselves.

- **MS-14a Who gets answers.** A settings list "Butler answers"
  (`whatsapp_answer_chats`, JIDs) picked from the archive's chats (people
  and groups), empty by default, plus a switch "Answer every saved contact
  who writes" (`whatsapp_answer_all`, default off). In a listed GROUP,
  Butler answers only a message that starts with its trigger word (setting
  `whatsapp_group_trigger`, default `Butler`) or that replies to one of
  Butler's own messages. A mention of the user is NOT a trigger: in a group
  that names the human, not the assistant. In a listed DM every message is
  answered.
- **MS-14b Guest turns** [DECIDED; this is the safety line of the feature].
  A message from anyone but the user is a GUEST turn:
  - its own standing chat in the root project, titled
    "WhatsApp: <name>", one per person or group, visible on the page;
  - brain only, NO tools of any kind: no project agent, no file reads (even
    `read_only` reads the disk), no memory or task tools, no dispatch;
  - the identity block is SOUL.md plus a fixed guest rule ("You are
    answering <name> on behalf of <user>. You are not talking to <user>.
    Do not share <user>'s private information."); USER.md, memory notes and
    other chats are NOT in the prompt;
  - every guest reply starts with "Butler: " so nobody mistakes it for the
    human;
  - at most 20 replies per guest chat per hour; past that, silence until
    the hour rolls, recorded in the chat.
- **MS-14c The human takes over.** When the user sends a message themselves
  in a guest chat (a `notify` + `fromMe` there that does not carry the
  marker), Butler stays quiet in that chat for 30 minutes.
- Guest chats never receive questions (MS-21) and never trigger speech at
  the desk.

## 6.2 As built in M6/M7: deviations (2026-10-07) [READ]

- **Phone turns are WORK.** `hub.PhoneSession` types with reason
  `whatsapp`, which `_on_final` treats like `recipe`: the daemon-answered
  spoken commands (mode, "work in X", "remember that", recipes, tasks) are
  not recognised from WhatsApp; `/new` is the router's own. The `final`
  frame's `reason` carries "whatsapp" and the turn's frames go to every
  page (plan.md 4.3 does not list either yet).
- **Replies have their own sidecar door**, `POST /reply` (own chat only,
  403 otherwise; not the send guard, not charged to the rate limit, audited
  with `reply: true`), plus `POST /presence`. `whatsapp_send` does not gate
  replies to the user's own messages. An unknown quote id is a 404 and that
  reply is not sent (logged); the archive answers for anything the live
  cache dropped.
- **No desk spoken-form cleaning existed.** `whatsapp.spoken_form` is new and
  voice-note only: fenced code, table rows and URLs dropped, link words kept,
  markdown marks removed; pieces the brain marked `spoken=False` are not
  voiced. "The rest is in the text." has no translation (`dialogue.LINES`).
- **Video is not stored**: `AttachmentStore` keeps images, PDF, docx and text
  only. Its sound is transcribed into the turn text under a video prefix;
  the model never sees frames. A document with no caption asks "What is in
  this document?".
- **`current` before any owner turn since boot**, or after that chat was
  deleted, is the conversation the daemon holds.
- **The "via WhatsApp" mark is live only**: the chat record does not carry
  it, so a reload shows the message unmarked.
- **MS-19 limits**: missed is decided per message, not by Baileys'
  `append` label (section 6.4). One line per burst, after a 3 s settle. A message that
  arrives while the sidecar runs but the daemon's stream is reconnecting is
  neither answered nor counted.
- **Cross-talk gap**: a voice note carries no marker (audio has no
  caption), so a SECOND Butler on the same number would answer one. This
  Butler's own echoes are excluded by the ids it sent.
- Section 11's "No voice-note REPLIES" line is overtaken by MS-18a.

## 6.3 As built in M9: deviations (2026-10-07) [READ]

- **Which model answers a guest.** `whatsapp.Guests` reaches a model only
  through `adapters.brain_endpoint.GuestCall`: one `llm.Client.complete`
  (which has no tools parameter) on the `reply` role, `endpoint_model`, in
  EVERY brain mode. Never a pi child: pi loads the owner's skills and
  extensions, whose text this request must not carry. `endpoint_model`
  ships blank, so on the defaults every guest gets "Butler: I cannot reply
  here right now." and the log says `endpoint_model names no connection`.
- **The prompt** is SOUL.md (labelled as both brains label it) and the
  guest rule, plus one sentence: "This is a WhatsApp chat: answer in a few
  plain sentences." `<user>` is the linked account's WhatsApp name (`/status`),
  "the user" when it has none. In a group the text is "<speaker>: <text>".
- **History is that guest's own exchange, in memory only** (10 messages,
  lost at restart), never read from the chat record: the owner can type in
  that chat on the page.
- **The standing chat holds notes, not turns.** It is born unfocused in the
  root project with `origin {kind: "whatsapp_guest", jid, title}`; every
  line is Butler's (`record_note`): "Alice wrote: ...", "Butler: ...", the
  silence and takeover lines. Nothing a guest wrote carries the owner's
  role. `phone_chat: root` skips guest chats when it picks the newest root
  chat. Endpoint mode keeps no chats: the guest is still answered and
  nothing is recorded.
- **Known exposure:** if the owner types INTO a guest chat on the page, that
  is an ordinary owner turn with tools, and the guest's lines in that record
  can reach that agent. Only the owner's own act opens it.
- **Rate and takeover are in memory**, per chat: a rolling 60 minutes (20
  replies), a silence line once per window, 30 minutes after a takeover; a
  restart forgets both. Messages arriving during either are not recorded.
  At most 10 messages wait per guest chat; more are dropped with a log line.
- **The door is the sidecar's `POST /guest-reply`**: text only, must start
  "Butler: " (400), only into a chat that is on the answer list NOW or a
  saved contact's one-to-one chat under answer-everyone (403
  `not_guest_chat`, audited), marker appended, audited `guest: true`, not
  charged to the per-minute send rate. Group replies quote the triggering
  message from the live cache only (404 if evicted: not sent, logged); DM
  replies are not quoted.
- **Guest media** is never fetched: the reply is the fixed line and the chat
  note says what kind arrived. A caption on it is not answered either.
- **The picker**: `whatsapp_answer_chats` has `choices_from="whatsapp_chats"`;
  that `GET /api/models` target answers `{value, label}` entries (people and
  groups, `status@broadcast` excluded; 502 with no archive), and the
  settings page's generic list control offers any target's choices in a
  datalist and shows each entry's label. Typing a chat id still works.

## 6.4 Defect: live owner messages read as missed (2026-10-08) [READ]

- **Seen.** Sam's laptop Butler (commit 5be42b4): 11 voice notes to her
  own chat in about 45 minutes, several a minute apart; 7 answered. butler.log
  had no line about the other 4, because nothing logged a received message.
- **Cause, proven in Baileys rc.9's own source and by
  `daemon/engines/whatsapp/test/delivery.test.mjs`.** The sidecar decided
  "live or missed" from the upsert's `type`, which is a BATCH label:
  `lib/Utils/event-buffer.js` flushes buffered messages as one upsert typed
  after the FIRST of them, and `lib/Socket/messages-recv.js` labels every
  message the server held during a socket reconnect `append`. So a voice
  note sent while Butler was up was emitted as `missed` (one "I was
  offline..." line, no answer) whenever it shared a flush with an `append`
  (Butler's own reply echo, an offline delivery) or arrived during a socket
  blip. The reverse also held: Butler's own voice-note echo flushed behind a
  live message was routed as the owner speaking. NOT verified on the laptop
  which of the two triggers fired there; its chat showing "I was offline and
  did not answer" lines, or `[whatsapp] [wa] connection closed` lines near
  those minutes in butler.log, would confirm it.
- **Fix.** In the own chat the label is replaced per MESSAGE
  (`whatsapp-inbound.js` `ownChatType`): a message this sidecar sent is
  never routed or missed; otherwise it is live when written after this
  sidecar started and delivered within `LIVE_LATE_S` (15 min [DECIDED]),
  else missed (MS-19 unchanged in meaning: written while Butler was down,
  or delivered so late that answering it would surprise). Other chats
  (guests, takeovers) keep Baileys' label; a guest message flushed behind an
  `append` can still go unanswered (open).
- **Assumption.** The machine's clock is within `LIVE_LATE_S` of WhatsApp's:
  a clock that far ahead makes every own-chat message "missed" (said in the
  chat, never silent).
- **The daemon side was not the cause.** `test/test_phone_back_to_back.py`
  pushes text and voice notes through the fake sidecar's `/events`, one
  during the previous note's transcription and one mid-answer, and the same
  burst over Telegram; each is answered in order, quoted to its own trigger.
- **The log now shows every owner message** (`phone.log`, ids and shapes
  only, never content): `[<app>] owner message <id> received (<text|voice|
  video|image|document>); <n> answering, <n> waiting`, then `reply to <id>
  sent`, `voice reply to <id> sent`, `reply to <id> was NOT delivered: ...`,
  `owner message <id> refused: 10 already waiting`, `owner message <id>
  failed: ...`, `owner message <id> missed: written while Butler was down`,
  and `message <id> was taken as a question's answer`.

## 6.5 Review fixes (2026-10-08) [OWNER: "fix it", all 25 findings]

Evidence: `.references/whatsapp-review-2026-10-08.md` (F-1..F-25, each with
path:line). One decision per finding; criteria AC-586..AC-612 (reserved).

Security (wave 1, agent A):
- **RV-1 (F-1) Local routes refuse cross-site callers.** Every `is_local`
  route also requires the header `X-Butler-Local: 1` and, when an `Origin`
  header is present, an origin of Butler's own (its listener addresses). A
  web page cannot set a custom header cross-site without a CORS preflight,
  which Butler never answers, so a page in the owner's browser cannot reach
  a local route. The pi extension, `tools/wa.py` and the desktop app send
  the header. **AC-586**: a cross-site POST to the send door, the memory and
  task routes and `/api/shutdown` is refused 403; the same with the header
  from a local tool succeeds. *NOT recoverable* (a sent message).
- **RV-2 (F-2) No secrets as attachments; a human confirms.** A tool's
  file must lie inside the turn's project folder or the data root but NEVER
  inside the records folder (`<data>/.butler`, incl. the WhatsApp session,
  `ssl`, `notify`, `triggers`). A tool cannot set `confirm`: a send that the
  guard holds for confirmation (409) becomes a harness question to the owner
  ("Send <text or file name> to <name>?") through the existing question
  door, and only the owner's yes re-sends it confirmed. **AC-587, AC-588.**
- **RV-3 (F-14) `wa.py` large files.** The client timeout scales with the
  file size, and a timeout exits 3 "unknown whether it was sent; check
  before retrying" instead of a traceback. **AC-589.**
- As built, RV-1..RV-3 (2026-10-08) [READ]:
  - The gate is `hub.is_local` itself (`LOCAL_HEADER`, `own_origins`: every
    listening port under 127.0.0.1 / localhost / [::1], http and https);
    `test/test_local_gate.py` holds the list of gated handlers to the source.
    The settings page also sends the header on its pairing fetches
    (`web/settings.js`), because `/api/phonelink` set-up is local-only.
  - A send is a TOOL's when it names `chat` or `source`; the pi extension now
    sends `chat` on every send. `whatsapp.tool_send` is the one place a held
    send is confirmed. The question is the daemon's own (`Hub.ask_owner`,
    qid prefix `owner-`, like `prop-`), answered only through
    `answer_question`; it invents no deadline and closes with its turn (a pi
    turn's 600 s idle deadline still runs while it waits). With no turn
    running in that chat the send is refused (`no_turn_to_ask`).
  - The refused folders are `records`, `ssl` and `<data>/.butler`
    (`whatsapp.records_dirs`), so a `BUTLER_STATE` that moved the records
    still leaves `<data>/.butler` closed. A HARD LINK is not detected (real
    paths only).
  - `wa.py` also exits 3 on the door's 504 `send_uncertain`.
  - NOT covered by RV-1: `/ws` cannot carry the header; RV-25 closes it with
    an `Origin` check instead.

Sidecar robustness (wave 1, agent C1):
- **RV-4 (F-6)** Each own-chat message id is routed at most once, across
  reconnects and sidecar restarts (recorded in the archive). **AC-590.**
- **RV-5 (F-7)** A failed version fetch is an error state the page shows,
  never a silent fallback to the bundled version; non-terminal closes back
  off exponentially to 5 minutes; 403 is terminal. **AC-591.**
- **RV-6 (F-12)** The archive opens with WAL and a 5 s busy timeout; a
  reader never makes a write fail at once. **AC-592.**
- **RV-7 (F-13)** An audit write that fails after a successful send still
  answers "sent" (the audit failure is logged loudly). **AC-593.**
- **RV-8 (F-15)** Album children arriving within 3 s become ONE owner turn
  with all images. **AC-594.**
- **RV-9 (F-17)** Stickers, locations, contact cards and polls are answered
  "I can read text, voice notes, photos, videos and documents here, not
  that."; an edited message reaches Butler as "(edited) <new text>".
  **AC-595.**
- **RV-10 (F-18, F-20)** Baileys' own logger prints text only (the RV-guard
  rule); the console guard is installed BEFORE Baileys loads. **AC-596.**
- **RV-11 (F-19)** Events published with no subscriber wait in a bounded
  buffer (200) until the daemon reattaches. **AC-597.**
- **RV-12 (F-16)** Every Butler send carries a Butler message-id prefix;
  inbound own-chat messages with that prefix are never routed (voice notes
  included); `/reply` is rate-limited (30 a minute). **AC-598.**

Daemon robustness (wave 1, agent C2):
- **RV-13 (F-5)** The phone queue is persisted in the records folder.
  After a restart, messages that never STARTED run again in order; the one
  that was IN FLIGHT is not re-run (its effects are unknown) and Butler says
  so in that chat: "I restarted while working on your message from HH:MM;
  send it again if it still matters." WhatsApp and Telegram both.
  **AC-599, AC-600.**
- **RV-14 (F-8)** When WhatsApp stops (440, 401, sidecar given up), Butler
  sends ONE line through the `notify_default` channel and says it at the
  desk. **AC-601.**
- **RV-15 (F-9)** A voice note is transcribed before deciding whether it
  answers an open question. **AC-602.**
- **RV-16 (F-22)** Audio over 10 minutes is refused in the chat with the
  limit named. **AC-603.**
- **RV-17 (F-23)** `/new` queues like any message. **AC-604.**
- **RV-18 (F-25)** One reply path (`PhoneTurns.say`), no dead branches, one
  refuse-to-guess resolver. **AC-605** (source test).
- **RV-19 (F-21)** The fake sidecar's request log is written under a lock.

Guests (wave 2, agent B, after wave 1):
- **RV-20 (F-3)** A "known contact" (send guard and `whatsapp_answer_all`)
  is a contact with an address-book name, or a chat the user has written in
  themselves; a push name alone does not count. **AC-606.**
- **RV-21 (F-4)** Guest chats are never indexed for `memory_search`; the
  page's composer is disabled in a guest chat ("Butler answers here as a
  guest assistant; reply from your phone"); no guest line is ever seeded
  into an owner turn. **AC-607, AC-608.**
- **RV-22 (F-10)** `phone_chat: current` never targets a guest chat.
  **AC-609.**
- **RV-23 (F-11)** A takeover is recognised per message, not by batch
  label, and a guest reply still being generated when the owner takes over
  is not sent. **AC-610.**
- **RV-24 (F-24)** The group trigger treats an apostrophe as part of the
  word ("Butler's" is not "Butler"). **AC-611.**
- As built, RV-20..RV-24 (2026-10-08) [READ]:
  - RV-20 since rc14 (2026-10-29): rc14 builds a history-sync contact's
    `name` as `displayName || name || username` (lib/Utils/history.js:58),
    so `username` -- the other person's choice -- would read as saved.
    `ingestHistory` therefore takes `name` ONLY from the raw conversation's
    `name` in `payload.chats` (history.js:100). Not changed: rc14's app-state
    contact name is `fullName || firstName || username`
    (lib/Utils/sync-action-utils.js:23); app-state contact actions come from
    the address book, so that is believed safe [INFERRED, not verified live].
  - RV-20 as first built: Baileys rc.9 fills a contact's `name` only from app-state
    `contactAction.fullName` (lib/Utils/chat-utils.js:667-674) and
    history-sync conversations (lib/Utils/history.js:31-36); every inbound
    message emits `contacts.update {notify: pushName, verifiedName}`
    (lib/Socket/chats.js:791-796). `isKnownContact` (sidecar) and
    `Archive.is_known` (daemon, a notify channel's `to`) are one rule: a
    non-blank `name`, or a `from_me` message in that chat that is not a
    Butler send (id prefix or marker). Deviation: a business's
    `verifiedName` is the sender's claim too and is now stored in `notify`,
    not `name`; rows an older sidecar already wrote that way still count as
    saved (they cannot be told apart). Whether WhatsApp ever fills
    `Conversation.name` for an UNSAVED one-to-one chat is not verified.
  - RV-21: the marker is `chats.is_guest_chat` (`origin.kind
    whatsapp_guest`), moved from `phone.py` to the record it marks. The
    memory index keeps a guest chat as a text-less `guest` row; its SCHEMA
    went 2 to 3, so an index that already holds guest text is rebuilt at the
    next boot. `ChatStore.needs_seed` is false in a guest chat. The three
    conversation routes now carry `origin` (one shape), which is how the
    page knows: `paintGuestLock` makes the composer `inert`, disables the
    textarea and shows the line through `textContent`; `sendTyped` refuses
    while locked. NOT covered: a SPOKEN turn while a guest chat is on screen
    still runs there (no seed, no index, but the record is readable by an
    agent with file tools).
  - RV-22: `PhoneTurns._current` answers "" for a guest chat (whether
    `last_owner_chat` or the held one), and the root rule picks the chat.
  - RV-23: `ownChatType` labels the user's own (`fromMe`) message in ANY
    chat per message, by `liveSince` and `LIVE_LATE_S`; others' messages
    keep Baileys' label. A takeover item now carries its message `id`, so
    the routed table drops a redelivery. `Guests._answer` re-checks the
    quiet window after the model answers and before `guest_reply`.
  - RV-24: the lookahead also excludes `'` and U+2019.
- **RV-25 (found during wave 1) The page socket refuses cross-site pages.**
  `/ws` is not covered by `is_local`'s header (a browser cannot set one on a
  WebSocket), and a socket accepted on the LOCAL listener is the desk: a web
  page in the owner's browser could open it and type turns into an agent
  with tools. The handshake now requires an `Origin` that is one of
  Butler's own origins for the listener it arrived on (a page served by that
  listener), or no Origin at all only from a non-browser local client.
  **AC-612**: a WebSocket handshake from Origin https://evil.example is
  refused on every listener; the page's own origin is accepted; the phone's
  ts.net origin is accepted on the proxied listener. *NOT recoverable* (a
  turn run with tools).
- As built, RV-25 (2026-10-08) [READ]: `hub.ws_origin_ok`, called by
  `Hub.handle` before `ws.prepare` (403 `cross_site_origin`). No `Origin` is
  accepted (a browser always sends one; the desk agent `desk/deskd.py` uses
  HTTP only, and RunSession/PhoneSession open no socket). Any listener
  accepts `own_origins`; LOCAL accepts nothing else. MAIN and PROXIED also
  accept the request's own URL, scheme + `Host` (PROXIED is always https:
  `tailscale serve` terminates TLS and keeps the phone's Host,
  `Out.Host = In.Host` in its serve.go [READ]). Deviation: over plain http
  that Host must be an IP literal, because a hostname over http is a
  DNS-rebinding page; TLS (the phone on 8140) rules that out, so
  `https://<name>.local:8140` is accepted. `test/test_ws_origin.py`.

## 7. Questions over WhatsApp and Telegram: one inbound design

- **MS-20 One capability, not a seventh port** [DECIDED]. A notification
  adapter MAY declare `INBOUND = True` and implement `start(on_message)`,
  `stop()` and `ask(question)` beside `send`. Six ports remain; the argument
  is recorded in `plan.md` 4.10 in the build commit (daemon/AGENTS.md: a
  seventh port needs that argument first, and this avoids one).
- **MS-21 Which questions go out.** A harness question (`Hub._on_ask_human`)
  from an automation run, or raised during a turn that came from the phone,
  is sent to that channel when setting `phone_questions` is on (default
  on). A question raised during a desk or page turn is not.
- **MS-22 WhatsApp has no buttons**: the question is text: "Butler needs you
  (<run or chat>): <message>" then numbered options, "Reply with the
  number, or reply to this message." A reply that QUOTES the question
  answers it (an `input` question takes the reply text as the value); an
  unquoted bare number or yes/no/sim/nao answers it only when exactly one
  question is open on that channel; anything else is a normal message.
- **MS-23 Telegram has buttons**: inline keyboards for confirm and select,
  a reply for input; callback data is a short token mapped in memory to the
  question id (64-byte limit; nothing about a pending question goes on disk,
  daemon/AGENTS.md).
- **MS-24 One answer door.** Every answer goes through
  `Hub.answer_question`; a late answer gets the 410 reason back in the
  chat ("That question was already closed: ..."), never a silent success.
  Only an explicit user reply or tap answers; Butler never auto-answers.

## 7.1 As built in M8: deviations (2026-10-07) [READ]

- **The capability has a fourth method, `own(settings)`**: the channel
  that reaches the OWNER, or None while that kind is off. WhatsApp's is
  the own chat whenever `whatsapp_enabled` is on and the daemon serves the
  door; it needs no channel FILE. `ask(question, where)` takes the run or
  chat title beside the `ports.Ask`, and answers a `NotifyResult` whose new
  `ref` field is the sent message id (Telegram's callback token later).
- **The sidecar changed by one field**: an `/events` message item now
  carries `quoted` (Baileys `contextInfo.stanzaId`, or null). Without it a
  quoted reply cannot be told from any other. Pinned by
  `inbound.test.mjs`; not yet seen on a real phone.
- **The question is sent through the reply door** (own chat only, not
  rate-limited), never the guarded send door, and is not quoted.
- **Every reply that is read as an answer gets a line back**, quoted:
  "Answered: <value>", the closed reason (MS-24), or a hint ("Reply with a
  number from 1 to 3.", "Reply 1 for yes or 2 for no.") when a reply that
  targets a question does not fit it. An `input` question ends with "Reply
  to this message with your answer." instead of the number line.
- **Unquoted bare words**: digits, yes, no, sim, nao, compared lower case
  with accents and a trailing "." or "!" dropped, so "Nao" and the accented
  form count. Anything longer is a normal message.
- **Which questions**: a phone turn's question goes to the channel it came
  from; a scheduled run's goes to every inbound channel that reaches the
  owner now (WhatsApp while enabled, Telegram while a channel has
  `inbound` on). Methods other than select, confirm and input are not sent.
- **Memory**: the sent message id -> question map holds 64 entries and
  outlives the question, so a late quoted reply is told why; the open set
  is read from the hub, never copied. An open question pushed out of the 64
  can still be answered on a page, not on the phone.
- **Media items are never answers**, and an answer is offered BEFORE the
  inbound queue, because the phone turn that asked is still in flight.

## 8. Two-way Telegram (R5)

- **MS-25** The Telegram channel file gains `inbound` (bool) and uses its
  existing `chat_id` as the owner. When on, an `INBOUND` poller long-polls
  `getUpdates` off the loop with its own timeout and a persisted offset
  (`Paths.notify` / `<name>.offset`). Only updates whose `chat.id` AND
  `from.id` equal the owner's are used; everything else is dropped with a
  log line that carries no content.
- A 409 from `getUpdates` means another program reads the same bot; the
  channel shows that as its `problem` ("another program is reading this
  bot: use a separate bot for Butler") instead of retrying in a loop.
- Messages land in the `phone_chat` target exactly as MS-16/17; voice
  notes as MS-18; replies as MS-18a (Telegram `sendVoice`, OGG/Opus).

## 8.1 As built in T1..T3: deviations (2026-10-07) [READ]

- **One turn machinery for both apps.** `whatsapp.Inbound`'s owner half
  moved to `daemon/phone.py` (`PhoneTurns`, `Message`, `Media`,
  `offer_answer`, the ffmpeg and text helpers, `is_guest_chat`);
  `whatsapp.Inbound` subclasses it and keeps the sidecar item shape, MS-19
  and guests. A channel supplies `reply`, `presence` and `media`, raising
  `phone.ChannelError`. The KIND ("whatsapp", "telegram") is the turn's
  typed reason AND where its questions go (`hub.PHONE_REASONS`).
- **The reader** is `notify_telegram.Inbound`, one per daemon: it re-reads
  the channel files every 5 s and reads the ONE channel with `inbound` on.
  With two, neither is read and both show "only one Telegram channel may
  have inbound on; 2 do". `inbound` takes a bool or the Add form's text
  (true/false/on/off/yes/no/1/0/empty) and is stored as a bool; with a
  negative (group) `chat_id` it is refused at the file.
- **Long poll**: Telegram holds 25 s, Butler's deadline is 35 s, each poll on
  its own daemon thread (cancelled at shutdown without holding the exit).
  Failures back off 1..30 s; a 409 backs off 30, 60, 120, 300 s.
- **The offset file** is `<notify>/<name>.offset`, JSON `{bot, offset}`
  (`bot` = the token's numeric id; a file for another bot is ignored), and is
  written BEFORE each update is handled: a crash mid-update loses that one
  update rather than running an agent's action twice. The first start of a
  bot answers NO backlog (it asks for the last update only and starts after
  it). Messages sent while Butler was down ARE answered after a restart,
  unlike WhatsApp's MS-19 (Telegram keeps them; AC-531 asks for that).
- **The 409 problem is shown with `configured: true`**, because the channel
  still SENDS. The settings page shows `problem` only when `configured` is
  false (`web/settings.js` paint), so today the 409 reaches the log and
  `GET /api/notify` but not the page. Open.
- **Owner only**: chat id AND sender id equal `chat_id`, for messages and for
  button taps; a stranger's tap is not even acknowledged. Edited messages,
  channel posts and other update types are skipped (`allowed_updates` asks
  for messages and taps only).
- **Questions**: confirm is Yes/No, select is one button per option (one per
  row), input is a forced reply ending "Reply to this message with your
  answer." No numbered lines (MS-22's text is WhatsApp's). Callback data is
  `<8 hex token> <option number>`. A reply to a question message carries its
  token; a reply to any OTHER message still counts as quoted, so it is never
  read as an unquoted bare answer. A tap is acknowledged with what it came
  to ("Answered: yes", or the closed reason) and the question message is
  edited to add that line, buttons removed. A tap whose token is gone (a
  restart) is acknowledged "That question is no longer known to Butler." and
  answers nothing.
- **Telegram's limits**: 4,096 characters per text (`split_text` cuts there);
  files over 20 MB (the Bot API's `getFile` limit for bots) are refused
  before download, the line naming 20 MB. A photo is fetched at its largest
  size. A sticker, location or contact is answered "I can read text, voice
  notes, photos, videos and documents here, not that."
- **Typing** is `sendChatAction typing`, re-sent every 10 s; Telegram shows
  it about 5 s, so it blinks during a long turn. "paused" sends nothing.
- **The page adopts a Telegram turn live** like a WhatsApp one: `web/app.js`
  keys phone turns on `PHONE_VIA` (the daemon's `hub.PHONE_REASONS`) and
  marks it "via Telegram".
- `phone_chat`, `phone_reply` and `phone_questions` sit in the WhatsApp
  settings group (a layout choice left as is); their help names both apps.
- A usable channel's live problem (the 409 "another program is reading this
  bot") is shown on its row as "PROBLEM: ..." without disabling it.

## 9. Ban risk and security, stated plainly

- Any unofficial WhatsApp client breaks WhatsApp's terms, and a ban on a
  personal number cannot be appealed. Reading is passive; SENDING carries
  the risk, and bulk or cold sends are the common trigger (anecdotal
  evidence, `.references/prototype-whatsapp.md` 9). The owner chose sending
  and guest replies with that known; the defaults (empty answer list,
  known recipients only, first-send confirmation, 10 sends a minute, 20
  guest replies an hour) keep volume low.
- Testing on Sam's real number puts her account at that risk. During
  development her answer list holds only people she names.
- Guest turns are the exposure that matters: anyone on the answer list
  types into a model on the user's machine. They get no tools and no
  private memory (MS-14b), so the worst case is a rude or wrong reply in the
  user's name, not a command run or a file read.
- The archive is a plaintext copy of the user's private conversations in the
  data root. The session folder is a credential: anyone who copies it IS
  the user's linked device.
- The self chat drives an agent with whatever the Tools setting allows; with
  `full`, a stolen phone is a remote shell, as with every satellite today.
- Two-way Telegram is the same posture: one owner id, nothing else read.

## 10. Criteria (AC-500..AC-539, AC-560..AC-563 reserved)

- **AC-500 (sidecar lifecycle).** Started only when enabled; job object at
  spawn; loopback port read back; every request without the per-boot token
  refused; a crash restarts it with backoff and logs its output.
  **Recoverable.**
- **AC-501 (records).** Session, archive and audit live under
  `Paths.whatsapp`; no repo path. **Recoverable.**
- **AC-502 (socket options).** The ported config test pins latest version
  fetch, `markOnlineOnConnect: false`, `syncFullHistory: false` WITH the
  history filter, `getMessage` from the archive, browser name Butler.
  *Fails as:* a link that archives 0 contacts, which cannot be fixed without
  relinking. **NOT recoverable** for that link.
- **AC-503 (pairing page).** The settings group renders states, QR on white,
  phone steps and the passkey hint from the schema with no page code naming
  WhatsApp. **Recoverable.**
- **AC-504 (one socket).** Link while linking returns the attempt in flight.
  **Recoverable.**
- **AC-505 (reconnect).** 515 reconnects at once; 440 stops and says the link
  is open elsewhere; 401 stops and asks to link again; nothing loops.
  **Recoverable.**
- **AC-506 (archive).** Ported store tests pass: schema, upserts never blank
  a field, every type and sender archived, wrappers unwrapped.
  **Recoverable.**
- **AC-507 (first real link).** On the laptop Butler, linking Sam's
  number archives contacts greater than 0 and recent chats within 10 minutes.
  [MEASURED at build] **NOT recoverable** (the sync comes once per link).
- **AC-508 (read gate).** With `whatsapp_read` off the four tools are not
  offered; on, they return results through a read-only open. **Recoverable.**
- **AC-509 (no guessing).** A name matching 0 or several contacts returns
  candidates and no JID. **Recoverable.**
- **AC-510 (wa.py parity).** Eight subcommands, `--since` grammar and exit
  codes match `/whatsapp-skill`'s `wa.py`; reads work with Butler stopped.
  **Recoverable.**
- **AC-511 (loopback door).** The send route answers 403 off loopback.
  **Recoverable.**
- **AC-512 (guard order).** Ported guard and route tests pass: send switch,
  resolve, known contact, rate limit charged on success only, first-send
  confirmation, own chat exempt from contact and confirm rules but not from
  switch and rate. **Recoverable** for refusals; a wrong send is not.
- **AC-513 (send switch and groups).** `whatsapp_send` defaults on, is
  re-read on every send, and when off refuses naming it; a group in the
  archive is a valid recipient under the same guard. **NOT recoverable**
  for a wrong send.
- **AC-514 (audit).** Every decision, refusals included, is one JSON line
  without the body. **Recoverable.**
- **AC-515 (files).** One file per send, 100 MB cap (413), inline kinds as
  an earlier prototype, everything else a document. **Recoverable.**
- **AC-516 (notification channel).** Kind `whatsapp` takes `to` = `me` or a
  known contact or group, refuses an ambiguous name with candidates, sends
  through the door (rate limit and audit apply), and appears in the channel
  Add form from `FIELDS` with no page code. **Recoverable.**
- **AC-517 (inbound filter).** Ported self-chat tests: LID and phone forms
  reach Butler; `fromMe` in any other chat never does; `append` never does;
  with the default empty answer list no one else does. **Recoverable.**
- **AC-518 (cross-talk marker).** Every Butler send ends with the marker; an
  inbound self-chat message ending with it is archived and not answered.
  **Recoverable.**
- **AC-519 (the user's own chat).** With `phone_chat: root` (default) a
  self-chat message is an owner turn in the newest root chat (created if
  none); with `current` it lands in `last_owner_chat`, so a desk turn
  followed by a WhatsApp message continues one conversation; the page shows
  the "via WhatsApp" mark; `/new` starts a fresh chat where the mode
  points; no guest message ever enters it. **Recoverable.**
- **AC-520 (reply per turn).** The final answer returns quoted to the
  trigger, split past 65,536 characters; typing shows while it works;
  nothing is spoken at the desk for a phone-origin turn; a message arriving
  while that chat answers another device queues behind it.
  **Recoverable.**
- **AC-521 (queue).** Turns run one at a time in arrival order; the 11th
  waiting message is answered with the busy line. **Recoverable.**
- **AC-522 (voice note in).** Transcribed by Butler's engine with the
  language decided as a desk voice turn's; a typed message's language from
  `detect_language`; the language is stored on the turn; the misheard
  prefix is added; no words gives the fixed line. [MEASURED at build: one
  Portuguese and one English note from Sam] **Recoverable.**
- **AC-523 (media).** Over 100 MB refused before download; images, documents
  and video through `attachments.py`; every refusal said in the chat.
  **Recoverable.**
- **AC-524 (missed while down).** Messages that arrived while Butler was
  down are archived, not answered, and one line on reconnect counts them.
  **Recoverable.**
- **AC-525 (inbound capability).** `INBOUND` is an optional adapter
  capability; `ports` still has six ports; `plan.md` 4.10 carries the
  argument. **Recoverable.**
- **AC-526 (questions out).** With `phone_questions` on, a question from an
  automation run or raised during a phone-origin turn reaches that channel;
  one raised during a desk or page turn does not. **Recoverable.**
- **AC-527 (WhatsApp answers).** A quoted reply answers that question; an
  unquoted number or yes/no answers only when exactly one is open;
  otherwise it is a normal message. *Fails as:* "1" meant for a person
  confirming an agent's action. **NOT recoverable** once acted on.
- **AC-528 (Telegram answers).** Buttons for confirm and select, reply for
  input, tokens in memory only. **Recoverable.**
- **AC-529 (one answer door, late answers).** Every answer goes through
  `Hub.answer_question`; a late one gets the closed reason back.
  **Recoverable.**
- **AC-530 (Telegram owner only).** Only updates from the owner's chat and
  user ids are used; others are dropped with no content logged.
  **Recoverable.**
- **AC-531 (Telegram offset).** The offset persists; a restart neither
  replays nor loses an update. **Recoverable.**
- **AC-532 (Telegram 409).** A second reader shows as the channel's problem
  and polling backs off; no tight loop. **Recoverable.**
- **AC-533 (Telegram turns).** Messages land in the `phone_chat` target
  with MS-17's rules; voice notes as AC-522; replies as AC-539.
  **Recoverable.**
- **AC-534 (ported tests run in the suite).** The ported Node tests run under
  `node --test` from one pytest wrapper, so the gate sees them by exit code.
  **Recoverable.**
- **AC-535 (who is answered).** A listed DM is answered on every message; a
  listed group only on the trigger word or a reply to Butler; a mention of
  the user is not a trigger; "answer every saved contact" off by default.
  **Recoverable.**
- **AC-536 (guest turn has nothing to give away).** A guest turn's request
  carries no tools, no project agent, no USER.md, no memory and no other
  chat; a source test holds the guest path apart from the owner path.
  *Fails as:* a stranger in a group reading the user's files or private
  notes. **NOT recoverable.**
- **AC-537 (guest reply shape).** Every guest reply starts "Butler: ", lands
  in that person's or group's standing chat, and stops at 20 an hour.
  **Recoverable.**
- **AC-538 (human takeover).** The user's own message in a guest chat
  silences Butler there for 30 minutes. **Recoverable.**
- **AC-539 (voice reply out).** With `phone_reply: voice_text` (default)
  every owner reply is an OGG/Opus voice note then the text; `mirror` sends
  voice only after a voice note; `text` never does; the voice speaks the
  language RECEIVED (a Portuguese note gets a Portuguese voice, an English
  one an English voice); code and links are not read aloud; past about two
  minutes it ends "the rest is in the text"; a synthesis failure still
  sends the text and says why; guests get text only. [MEASURED at build:
  Sam hears both languages on her phone] **Recoverable.**

- **AC-560 (image in, vision).** A photo from the user's chat is stored by
  `AttachmentStore` and attached to the owner turn; with a model that can
  see it is answered from the image; no caption asks "What is in this
  image?". [MEASURED at build: one photo from Sam] **Recoverable.**
- **AC-561 (document in).** A PDF or text file follows `document_reading`
  exactly as a page upload does. **Recoverable.**
- **AC-562 (file out).** `whatsapp_send` with a file inside the project or
  data root sends it (inline kind or document) through the door with one
  audit line; a path outside both is refused naming the rule; to the
  user's own chat no confirmation; to others the guard. **NOT
  recoverable** for a wrong recipient.
- **AC-563 (guest media).** A guest's file is answered "I can only read
  text here." and never stored or shown to a model. **Recoverable.**

## 11. Known gaps carried over from an earlier prototype

- No LID-to-phone merge: a contact known only by LID may be refused by the
  known-contact rule or resolved by name only (an earlier prototype's open question, not
  tested there either).
- The confirmation set is per process: after a restart the first send to
  each person is held again. Kept: it errs toward asking.
- No voice-note REPLIES (text only), as an earlier prototype.
- Received files live as long as `attachments.py` keeps them, not
  an earlier prototype's one hour; to check at build.

## 12. Build plan

Red-green per phase. An earlier prototype's tests are PORTED with the code
(`whatsapp-config`, `-store`, `-pairing`, `-pairing-routes`, `-send-guard`,
`-send-route`, `-media`, `-self-chat`, `-voice`) and run under `node --test`
through one pytest wrapper (AC-534); the daemon side uses a fake sidecar at
the HTTP layer, the way `test/test_notify.py` fakes the Bot API. Nothing in
the suite touches a real WhatsApp account. Every real-account step is on the
LAPTOP Butler, done with Sam present to scan and watch, through the
`remoteshell` route and its WMI restart rule; the desk's link to an earlier prototype is
never touched.

| phase | builds | criteria | verify |
|---|---|---|---|
| M0 | settings mock: WhatsApp group (switch, pairing block, send switch, read gate, answer list, group trigger, warning) and the question message format | -- | owner's look approval |
| M1 | sidecar port + ported Node tests, no daemon wiring | AC-502, 504, 506, 512, 514, 515, 517, 534 | `node --test` via the wrapper |
| M2 | daemon lifecycle, records, pairing routes, settings group | AC-500, 501, 503, 505 | scoped pytest; link a THROWAWAY session on a scratch daemon only up to "awaiting scan" (no scan) |
| M3 | first real link on the laptop | AC-507 | Sam scans; contacts and chats counted from the archive |
| M4 | read tools, `tools/wa.py`, skill text | AC-508..510 | scoped tests; `wa.py inbox --since today` on the laptop |
| M5 | send door, notification channel | AC-511, 513, 516 | one `wa.py me "test"` on the laptop; one automation result to her own chat; one send to a person she names (first-send confirmation seen) |
| M6 | inbound self chat into `phone_chat` (both modes), text replies, queue, marker | AC-518..521, 524 | Sam writes to herself from the phone; the reply arrives and the turn shows in the root chat on her page; then `current` mode continues a desk conversation |
| M7 | media, voice notes in, voice replies out | AC-522, 523, 539 | a Portuguese and an English voice note each answered by a voice note in that language plus text; one photo; one PDF |
| M8 | questions over WhatsApp, the inbound capability | AC-525..527, 529 | an automation that asks; she answers by quoting |
| M9 | guest replies: answer list, guest turns, prefix, rate, takeover | AC-535..538 | a person she picks writes; Butler answers "Butler: ..."; she replies herself and Butler goes quiet |
| T1..T3 | two-way Telegram on the same capability | AC-528, 530..533 | a fake Bot API in tests; then one real bot, separate from any other program |

Sam chooses who is on the answer list; the developer never adds anyone.
`docs/desktop-app.md` D1 (trust by listener) must land before M5 if that
machine uses `tailscale serve`: the send door is loopback-only, and a
proxy on loopback makes every phone look local.

## 13. Open for the owner

- **O-1.** The cross-talk marker protects Butler from Butler only. If the
  owner ever links Butler on HIS number, an earlier prototype's self chat must be turned
  off first (or taught the marker). Accept that as a documented rule?
- **O-2.** Voice-note replies (Butler answering with audio, using its TTS)
  are not in an earlier prototype and not here. Wanted later?

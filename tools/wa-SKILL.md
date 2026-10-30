---
name: butler-whatsapp
description: Read, search, summarize and send the user's WhatsApp through Butler, from ANY agent or shell on the machine Butler runs on. Use when the user asks "what did I get on WhatsApp", "summarize my WhatsApp messages today", "what did <person> say", "search WhatsApp for X", "find <contact>'s number", "send <person> a WhatsApp", "WhatsApp me X", or when an agent needs to notify the user (`wa.py me`). Reads Butler's archive read-only (works with Butler stopped); sends ONLY through Butler's guarded door.
---

# butler-whatsapp

One stdlib Python CLI, `wa.py`, beside this file in Butler's `tools/` folder.
The user's number is linked as a device of Butler (settings page, WhatsApp);
Butler archives every message into `<Butler records>/whatsapp/whatsapp.db`.

```bash
python -X utf8 <butler>/tools/wa.py <command> [flags]
```

| Goal | Command |
|------|---------|
| Is it linked, how big is the archive | `wa.py status` |
| Messages RECEIVED today (summaries start here) | `wa.py inbox --since today [--no-groups] [--json]` |
| One chat | `wa.py read "<name or +number or jid>" [--limit 30] [--since 7d]` |
| Search text | `wa.py search "dentist" [--chat "<name>"] [--since 30d]` |
| Chats, newest first | `wa.py chats [--groups] [--limit 30]` |
| Find a contact | `wa.py contacts "maria"` |
| Message the USER (their own chat) | `wa.py me "text"` or `wa.py me --file <path> [--caption "..."]` |
| Send text | `wa.py send "<name, +number or group>" "text"` then, if told, again with `--confirm` |
| Send a file | `wa.py send "<name, +number or group>" --file <path> [--caption "..."] [--confirm]` (text optional; the file type decides inline image/video/audio vs document; max 100 MB) |

`--since` takes `today`, `yesterday`, `90m`, `24h`, `7d` or an ISO date (local time).

## Rules

- **When the message is FOR THE USER, use `wa.py me`** -- never guess or look up a
  recipient. It goes to their own "Message yourself" chat, needs no `--confirm`,
  and needs no prior approval: it reaches only them. The send switch and the rate
  limit still apply, so a refusal there is final.
- **For anyone else, ASK THE USER BEFORE EVERY SEND, with the exact recipient and
  text -- or, for a file, the recipient, file name and caption.** It goes out from
  their real number as them. Groups are allowed, under the same ask-first rule.
  Never bulk-send, and never message someone who has not written to the user first
  unless the user says so -- cold outbound is what gets personal numbers banned,
  and a ban loses all their chats.
- `send` exit codes: 0 sent; 2 = HTTP 409, either `confirmation_required` (first
  send to that person since Butler started -- rerun with `--confirm` only after the
  user approved) or `ambiguous_recipient` (pick from `candidates`, never guess);
  1 = refused (`send_disabled`, `unknown_recipient`, `rate_limited`, 403 off this
  machine), not linked (503), or Butler is not running. A refusal is final; do
  not work around it. 3 = UNKNOWN whether it was sent: Butler did not answer in
  time (the wait is 30 s plus 1 s per MB of file, at most 630 s) or WhatsApp
  took the message without answering (504). It may have gone out: check the
  chat (`wa.py read`) before sending again, never retry blindly.
- Ambiguous chat names in `read`/`search` exit 2 with a candidate list -- rerun
  with the jid. Never pick the first match.
- Summaries: run `inbox --json`, group by chat, lead with DMs and anything that
  asks the user a question or needs a reply; collapse busy groups to one line each.
  Media shows as `[image]`, `[voice note 12s]`, etc. -- the CLI does not
  transcribe received voice notes or open media.
- The archive only grows while Butler runs with WhatsApp on. `status` shows the
  newest message time; if it is stale, say so rather than reporting "nothing new".
- Read-only on the database. Never write to `whatsapp.db` and never touch the
  `session/` folder beside it (that is the link; deleting it unlinks the phone).

## Environment

- The archive is found the way Butler finds its records: `BUTLER_DATA` (the data
  root), `BUTLER_STATE` (the records alone), else Butler's legacy layout. Or pass
  `--db <path to whatsapp.db>`.
- `BUTLER_URL` for `send`, `me` and `status` (default `http://127.0.0.1:8141`, the
  daemon's LOCAL listener). The send door answers this machine only, and only a
  caller that sends the header `X-Butler-Local: 1` (wa.py does).
- Sending also needs Settings > WhatsApp > "Butler may send" on (the default).

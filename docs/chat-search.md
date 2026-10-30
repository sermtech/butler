# Finding a conversation again

Owner's ask, 2026-09-10:

> "a search bar over past chats, so user can find previous chats easily."

Criteria AC-185..AC-188. They stay here until the owner accepts them;
`plan.md` is still the contract.

---

## 0. What is already true

`GET /api/chats` serves `{id, title, turns, updatedAt}` per row, newest first,
bounded at `butlerd.CHATS_LISTED` (200). The sidebar draws those rows. There is
no message text on that wire at all, so a search this page could run by itself
would be a search over FIRST UTTERANCES -- `chats._title` is the first thing
the owner said, cut to 60 characters.

**The scan the owner would pay for is already being paid.**
`ChatStore._records()` reads and `json.loads` EVERY conversation in
`state/chats/` on every `listing()` call, and `get_chats` calls it on the event
loop today. So matching against message CONTENT costs a substring test per
record on a walk that already happens -- the expensive half is not the search,
it is the listing, and that is not new.

`docs/settings-page.md`'s AC-85 search is the pattern for the control itself:
a `data-hay` string precomputed from the PAYLOAD and never scraped off the
DOM, a lowercase `indexOf`, containers that collapse when they empty, a
persistent "nothing matched" element rather than a blank panel, and
re-application after any rebuild.

---

## 1. The decisions

**D1. ONE rule, on the DAEMON.** Title matching could be done in the page from
data it already holds, and content matching cannot. Two filters means two
rules that disagree the first time one is fixed, and the disagreement is
invisible: a row that the page hid and the daemon would have shown just is not
there. So the query goes to the daemon and the page draws what comes back.
The cost is a round trip, bounded by a debounce; this daemon is on localhost or
a tailnet.

**D2. A CONTENT match carries a SNIPPET, a title match does not.** A row that
matched on something the owner cannot see is a row they have to open to
understand. The snippet is the matching excerpt with the surrounding words,
whitespace collapsed, from the message that matched -- which is also the
cheapest possible explanation of why the row is there.

**D3. OFF THE EVENT LOOP.** `_records()` is a full directory read plus a
`json.loads` per conversation, and searching runs it on every debounce tick
rather than once per page load. `asyncio.to_thread`, which is the rule D-25
already states for the cheap-read-that-loads-the-model, applied to the one
route that makes it hot.

**D4. The bound is `CHATS_LISTED`, and it is applied AFTER the match.**
Bounding first would search the newest 200 conversations and call that "every
chat"; bounding after searches everything on disk and shows the newest 200
that matched. The daemon holds one owner's conversations, so the walk is the
same walk either way.

---

## 2. Acceptance criteria

**AC-185 `GET /api/chats?q=<text>` answers the same shape, filtered.**
`{chats: [...]}`, newest first, bounded at `CHATS_LISTED`, 404 in endpoint
mode on `NO_CONVERSATION` like the routes beside it. A blank or absent `q` is
the unfiltered listing, byte-identical to what it serves today. FAILS BY: a
second response shape for the same list, which would give the page two
rendering paths. Recoverable.

**AC-186 It matches the TITLE and the message CONTENT, case-insensitively.**
One lowercase substring test over the title and over every message's text.
FAILS BY: a search that only sees titles, which is the search the page could
already have done for itself and is not what was asked for.

**AC-187 A content match carries a SNIPPET and a title match does not.** The
excerpt is whitespace-collapsed and bounded in characters. FAILS BY: a row
appearing with nothing on it explaining why -- the same class of failure as a
control that reports a success it did not have (AC-7b).

**AC-188 The search bar is AC-85's control, in the sidebar.** It filters the
conversation list, the day headings collapse when they empty, a query that
matches nothing SAYS so rather than leaving a blank column, and the filter
survives every rebuild of the list -- `paintChatList` does `replaceChildren`,
and a delete rebuilds it. FAILS BY: a filter silently lost on the next
repaint, which reads as the search having stopped working.

---

## 3. What this could have wrong

**3.1 The scan is O(every conversation) per debounce tick.** It is off the
event loop, so it cannot freeze a turn, but a daemon with thousands of
conversations would make the search feel slow and would do real disk work
while the owner types. The honest fix at that size is an index, and the honest
reason not to build one now is that this daemon holds one person's chats.

**3.2 The snippet shows the owner's own words and Butler's, in a sidebar.**
That is no more than the transcript already shows, but it shows it in a column
that is visible while somebody else is looking at the screen.

**3.3 Nothing here searches what the AGENT did**, only what was said. The
tool-level record went with `daemon/sessions.py` (docs/harness-mode.md 10.5),
so "find the conversation where it edited that file" is not answerable.

# Showing Butler something

Owner's ask, 2026-09-10, two items that share one path:

> "attach images and camera to the chat for analysis, like chatgpt and claude"

> "attach or upload documents for OCR or visual analysis, so we can talk to it"

and, when asked how each half should work (2026-09-11):

> "add pypdf and python-docx for now but plumbing for alternatively sending
> whole documents to vision model as well"

> "both modes -- ask the connection then try and report, and a separate vision
> model settings. default to separate vision model setting."

Criteria AC-197..AC-206. They stay here until the owner accepts them;
`plan.md` is still the contract.

**BUILT 2026-09-11.** Section 4a records what the build found; one of the
three changed where a decision lives.

**Owner, 2026-10-29: the answering model itself has eyes, so there is no
separate vision model any more.** AC-815..AC-818 (`plan.md`) replace AC-203:
`vision_model` and the resolver's `vision` role are gone, and an image goes to
the model that answers. Section 3 is the current design; 4a.1 is history.

---

## 0. The socket is the wrong path, and this is already written down

Every binary frame on the WebSocket is fed straight into the live turn's ASR
pipeline with no type tag (`hub.py:633-637`). An image arriving that way would
be handed to the recognizer as audio. `web/AGENTS.md` already makes exactly
this argument for the ambient ring -- "it goes over HTTP and never the socket:
binary frames there are fed straight into the live turn's pipeline, so ambient
audio arriving that way would become part of what the owner is treated as
having SAID" -- and an image inherits it verbatim.

So an attachment is an HTTP POST, and it is `post_ambient`'s shape:
`request.content` read in `PCM_READ_CHUNK` pieces against a ceiling DERIVED
from a setting's own maximum, the read ABORTED when it is crossed rather than
draining a body already too large, and a 413 naming the number. aiohttp's
1 MB `client_max_size` is deliberately not raised (`butlerd.py:211-214`).

There is no multipart handling anywhere in this daemon and this does not add
any: the body IS the file and the name rides in the query, which is the same
shape the two raw-PCM routes already use.

---

## 1. Where an attachment goes, and it is NOT a protocol change

`POST /api/attach?device=<id>&name=<filename>` stores the file and hangs it on
that satellite's SESSION, pending. The next turn from that session consumes
it, whichever door the turn comes in by.

That is `POST /api/ambient?turn=<id>`'s shape, and it is chosen over a new
field on the `typed` frame for one reason: a `typed`-only field would mean an
attachment can be sent with a typed message and NOT with a spoken one, and
"show Butler this and ask about it out loud" is the case the owner named
first. Nothing in `plan.md` 4.3 changes.

Pending attachments are cleared at the start of every turn after being read,
which is `s.ambient`'s rule in `_begin_turn` and for `s.ambient`'s reason: a
clip its own turn never used must not become the next question's context
(D-6, AC-72).

---

## 2. What the daemon does with the bytes

**Images** are kept as bytes and passed on as bytes.

**Documents are read FIRST and looked at SECOND.** `document_reading` is a
setting with three values and the default is `text_then_vision`:

- `text` -- extract only. A PDF with no text layer is reported as such.
- `text_then_vision` -- extract, and when there is no text worth having, show
  the file to the model instead (it needs a model that can see).
- `vision` -- never extract; the file goes to the model whole.

Extraction is `pypdf` for PDFs and `python-docx` for `.docx`, plus a direct
read for anything that is already text (`.txt`, `.md`, `.csv`, source files).
Both libraries are local, need no GPU and no model, and are the right answer
for the overwhelmingly common case: a PDF with a text layer. The 1080 Ti is
below the compute-capability floor for most current vision stacks, which is
measured and recorded in the TTS research of 2026-09-10, so "just show it to a
model that can see" is a network call every time.

**The FILE IS KEPT ON DISK and its path travels with it.** A coding agent can
open a file; that is most of item 5 and item 6 for almost none of the work, and
it is what makes an attachment useful in harness mode even when no model can
see.

---

## 3. Which model looks at it

**The model that answers** (AC-815, owner 2026-10-29). There is no separate
vision model: an image goes to the conversation model (`endpoint_model`, the
resolver's `reply` role). A `vision_model` left in an old `settings.json` is
dropped at load by the unknown-key line, said once. Harness modes are
unchanged: the agent gets the attachment itself (AC-201).

**Ask the connection, then try and report.** The chosen model's entry is read
from that connection's `/v1/models`; if the provider says it takes no image
input, Butler refuses ALOUD before sending. Most OpenAI-compatible servers say
nothing about modalities, so otherwise Butler sends it and, if the endpoint
refuses, says what the endpoint said. There is deliberately NO capability table
in the daemon: a hardcoded list of which models can see is exactly the kind of
list that was wrong by a factor of two and a half when `Tts.info` had one
(AC-42).

---

## 4. Acceptance criteria

**AC-197 `POST /api/attach` takes one file and answers what it made of it.**
`{id, name, kind, media_type, bytes, chars}` -- and it is refused by SIZE
against a ceiling derived from a setting's own maximum, with the read aborted
and a 413 naming the number. FAILS BY: an unbounded body read into a
single-user daemon's memory from anywhere on the tailnet.

**AC-198 The TYPE is sniffed from the bytes, not trusted from the caller.**
`Content-Type` and the filename are both the caller's, and this route stores a
file a coding agent may later be pointed at. A type nothing recognises is
refused by name rather than stored as "probably fine". FAILS BY: a file whose
extension and contents disagree.

**AC-199 An attachment is PENDING ON THE SESSION and is consumed by the next
turn, spoken or typed.** Cleared in `_begin_turn` after it is read, so one that
its own turn never used cannot become the next question's context. FAILS BY: a
`typed`-only field, which would make "show Butler this and ask out loud"
impossible.

**AC-200 Documents are EXTRACTED before they are looked at**, per
`document_reading`. Text-layer PDFs and `.docx` bodies come back as text with
no model involved. FAILS BY: sending a 40-page text PDF as a picture,
which costs tokens, loses fidelity, and fails entirely when nothing can see.

**AC-201 The FILE stays on disk and its PATH reaches the harness.** A coding
agent gets a labelled block naming the path -- and the extracted text when
there is some -- so an attachment is useful in harness mode even when no
model can see. FAILS BY: an attachment that only works in endpoint
mode.

**AC-202 `TurnContext` carries attachments as its own field**, beside
`overheard` and for `overheard`'s reason: it is per-turn side context that must
never be merged into the utterance. `Brain.turn(text, ctx)` does not change
shape. FAILS BY: an attachment's filename entering the record as something the
owner said.

**AC-203 (REPLACED by AC-815, 2026-10-29) An image goes to `vision_model`
when one is named**, and to the conversation model when it is not. FAILS BY: an
image silently going to a model the owner did not choose for it. Now: an image
always goes to the model that answers; `vision_model` is retired.

**AC-204 A model that cannot see is reported OUT LOUD, before or after.**
Before, when the connection's own model list says it takes no image input;
after, in the endpoint's own words, when it does not say. Never silently
dropped and never a reply that answers as if it had seen something. FAILS BY:
Butler describing an image it never received -- the one failure here that
produces a confident wrong answer rather than an error.

**AC-205 The page can attach a FILE and take a PHOTO**, both from the
composer, both showing what is attached before the message is sent, and both
removable. The camera is opened only when the button is pressed and its stream
is stopped as soon as the photo is taken. FAILS BY: a camera left running.

**AC-206 Nothing here is on the WebSocket.** No new frame, no binary upload,
no change to `plan.md` 4.3. FAILS BY: an image reaching the ASR pipeline.

---

## 4a. What the build found

**4a.1 (history: the `vision` role was removed by AC-815) "Blank
vision_model means the conversation model" belonged in the RESOLVER, not in
the adapter.** The obvious build is
`try: resolve("vision") except: resolve("reply")` in `brain_endpoint`, which
is control flow through an exception AND an adapter deciding what a setting's
blank value means -- the thing `docs/architecture-seams.md` 3.4 built this
seam to prevent. It is a third row in `SettingsResolver.ROLES` and a
`FALLS_BACK_TO` table beside it, so the object reading the setting is the one
that knows what blank means and the port stays at one method.

**4a.2 `sees_images` had to be three-valued before anything else would work.**
The first sketch returned a boolean, and a boolean forces silence to mean
either yes or no. Most OpenAI-compatible servers say nothing about modalities,
so "no" would refuse every image on every local server and "yes" would make
the pre-flight decorative. None is the normal answer, and it is what makes
AC-204's two halves necessary rather than redundant.

**4a.3 The PNG the tests use is written as hex and the PDF and docx are
generated by the libraries that read them.** A checked-in binary fixture is
something nobody can regenerate or verify by reading; the PDF fixture asserts
a pipeline instead -- write a text layer with pypdf, read it back with pypdf --
which is also what catches a dependency that changes behaviour on upgrade.

**4a.4 What was measured.** A scratch daemon on a free port with its own
`BUTLER_STATE`, driven in a real browser. A PNG attached gives a chip reading
`kitchen.png / image`; a text file gives `notes.txt / 41 chars`, so the
extraction ran; an ELF binary named `.bin` is refused in the daemon's own
words ("It keeps images (png, jpeg, gif, webp), PDFs, Word documents and plain
text files") with no chip; removing a chip leaves the note saying Butler still
has the file; Chrome's fake camera produced a real photo through the same
upload path and gave a third chip. Three files on disk and the refused one
absent. A whole typed turn with a picture attached cleared the chips on the
`final` and rendered as an ordinary turn. Zero uncaught page errors in every
run. `test_attachments.py` 32 passed; `mutate_attachments.py` 8/8 caught;
`probe_page.py` and `probe_ux.py` both exit 0; pytest 953 passed.

---

## 5. What this could have wrong

**5.1 An attachment is a file a coding agent may be pointed at.** That is the
point of AC-201 and it is also the risk: the path goes into a prompt read by
an agent that runs commands. What bounds it is that the owner put the file
there deliberately, and that the path is inside `state/attachments/` rather
than anywhere the caller names.

**5.2 The store grows.** Files are kept so the path stays valid for as long as
the conversation is worth continuing. Bounded by count rather than by age, so
there is no clock logic to get wrong, and the oldest go first.

**5.3 OCR is NOT implemented.** A scanned PDF has no text layer, so
`text_then_vision` shows it to the model that answers -- which is OCR by
another route and only works if that model can see. `text` alone reports that there was
nothing to read. No local OCR engine is added: that is a third dependency and
a model, for a case the owner has not yet hit.

**5.4 The capability probe is weak on purpose.** Most OpenAI-compatible
servers say nothing about modalities, so for most owners the pre-flight will
pass everything and the report will come from the endpoint. That is why AC-204
has two halves and why neither alone would do.

**5.5 Nothing here is tested with a screen reader**, and the composer now has
five controls in it.

# Product

## Register

product

## Users

One owner today, on three devices over a tailnet: a Windows desk machine that
runs the daemon, a phone used one-handed and often on the move, and a tablet.
The owner is an expert user who lives in the app daily and reaches for chords,
the numpad and the settings page without hesitation.

Design for a SECOND user who is not the owner. That person has never seen
Butler, does not know which glyph means what, may be using a keyboard or a
screen reader, and will not read documentation. The owner's ruling of
2026-09-16: "others will use it."

The job to be done: ask Butler something and get an answer, by voice when the
room allows it and by typing when it does not, and find that answer again
later.

## Product Purpose

An always-on, hands-free voice assistant that holds a conversation, reads
answers aloud, and dispatches real coding-agent harnesses as background workers
that report back by speech. Success is that the owner talks to it all day
without touching the page, AND that a stranger opening the page knows what to
do inside five seconds.

Not telephony. Not multi-tenant. Not a cloud service.

## Brand Personality

Utilitarian, dense, legible. A tool, not a companion. The reference is
ChatGPT's chrome rather than Claude.ai's calm: controls on the surface where
you can see them, tight rows, model and tool state always readable.

CHAT-FIRST. The transcript is the hero and voice is a state on it, not a stage
for it. The wave is a status line, not a canvas. This is the owner's ruling of
2026-09-16 and it reverses the visual emphasis the lattice-wave work assumed.

## Anti-references

- Any desktop window the daemon draws. Overruled by the owner 2026-09-09 after
  one commit of a tkinter picker: "I don't want to see an ugly tkinter
  interface."
- The first `#pickRow`, rejected on sight 2026-09-11: "too big, ugly, and on
  top of the chat input instead of underneath". Five 44 px bordered boxes, 94 px
  tall, the pressed state louder than Send.
- A voice-first hero: the wave owning the upper third of the page while the
  answer the user is trying to read is pushed below it.
- Elements that move while the user is reading or recording (owner's backlog,
  2026-09-11 item 7).
- Icon-only controls. The only affordance for New chat was an unlabelled 44x44
  pencil until 2026-09-15; that is the shape of the defect, not a one-off.

## Design Principles

1. **The transcript is the product.** Voice is a state on it. If a change makes
   the answer harder to read in order to make the microphone more visible, it
   is the wrong change.
2. **Every control says what it does.** A glyph with no text label is a defect
   to be justified, not a style to be defended.
3. **The page names no setting.** Options are rendered from the daemon's own
   schema (AC-41). A surface that hardcodes a setting's name is the drifted
   copy by construction.
4. **Familiar beats clever.** Standard chat affordances in standard places.
   A user fluent in ChatGPT should not have to learn a new vocabulary for
   send, stop, attach, copy, retry or new chat.
5. **Nothing moves while you read.** Layout transitions during a reply or a
   recording are a bug, whatever triggered them.

## Accessibility & Inclusion

WCAG AA is the bar and it is blocking, not polish (owner's ruling 2026-09-16).

- Body text at or above 4.5:1; large text at or above 3:1. The existing colour
  schemes already hold this and AC-174 forbids a scheme from repainting the
  state accent.
- Every interactive control reachable and operable by keyboard, with a visible
  focus indicator.
- No meaning carried by colour alone. The speaking / thinking / listening
  states need a word or a shape, not only a hue -- the light theme already
  collapses `thinking` and `speaking` to 1.18:1 apart in all five schemes
  (docs/lattice-wave.md 9).
- Touch targets 44x44 CSS px on anything reached from a phone.
- Every animation needs a `prefers-reduced-motion` alternative.

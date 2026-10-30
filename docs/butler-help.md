# Butler knows Butler -- a feature index and a help tool

Owner's ask, 2026-10-08:

> "does the agent have enough knowledge about the butler app itself? its
> features, tools available, so it can recommend it to user when
> appropriate?"

and, offered six options, the owner chose the fourth: "i like number 4. your
recommendation. spec, plan, implement".

Criteria AC-641..AC-656 are RESERVED here until the owner promotes them into
`plan.md`.

**Status: BUILT 2026-10-08.**

---

## 1. What the agent knew before this [READ]

- The endpoint brain: one line, "You are Butler, a voice assistant... READ
  ALOUD" (`daemon/adapters/brain_endpoint.py:39`), then SOUL.md, USER.md, the
  memory, task and reminder rules and the memory index
  (`daemon/persona.py` `Persona.blocks`), and its tool descriptions.
- The harness child (pi): pi's own coding-agent prompt, the project's
  AGENTS.md, the same persona blocks as an appended file, and its tool
  descriptions. Nothing from the daemon says "you are running inside Butler".
- The router sends every question about Butler itself to the conversation
  model (`daemon/dialogue.py` `ROUTER_SYSTEM`), which did not know either.
- So the agent knew a feature exactly when it held a TOOL for it (memory,
  tasks, reminders, schedules, WhatsApp, show_file/show_ui) and nothing about
  recipes, automations, heartbeat, triggers, quiet hours, projects and "work
  in X", rewind and retry, the explorer, attachments, dictation and the desk
  shortcuts, the phone link and desktop app, Telegram, or chat search. The
  failure modes are silence where a feature would help, and an invented
  feature when asked.

---

## 2. Decisions

- **BH-1. A short INDEX always, the DETAIL on demand.** Every request where
  Butler's tools exist carries one block: the suggestion policy (BH-4) and one
  line per feature (name and when it helps), at most 1,500 characters,
  capped by test. Details come from a tool. The pattern is the one skills
  use: names always loaded, bodies when needed. *Losing options:* the whole
  guide in every request (paid on every turn, and the part that changes --
  what is on -- would churn the prefix cache and, for pi, respawn the child,
  AC-60); a tool alone (the model calls it only when it already suspects a
  feature fits, which is the case this exists for).
- **BH-2. `butler_help(topic?)`, one tool, both brains.** No topic answers the
  index with every feature's current state; a topic answers that feature's
  detail, its state, and the blurb of each settings group it covers. Offered
  exactly where the memory tools are: the endpoint brain by capability
  (`NEEDS_GUIDE`), the harness through `pi_memory.ts` to a loopback route,
  named in read_only's `--tools`. Never to guests. Read-only: it changes
  nothing.
- **BH-3. One table, and the drift is a test failure.** The features live in
  ONE table (`daemon/guide_features.py`), each naming the settings groups and
  agent tools it covers. A test fails when a settings group, or a tool any
  brain is offered, is covered by no feature and not listed as deliberately
  excluded: a new feature cannot ship without the agent being told about it.
  Settings blurbs (`settings.GROUPS[...].blurb`, written for the owner) are
  reused, never rewritten. The words the page and the voice really use
  (button names, spoken phrases) are checked against the code by test where
  the code holds them.
- **BH-4. The suggestion policy (owner's choice: proactive).** Suggest a
  Butler feature only when it would clearly save the owner effort with what
  they are doing now; at most one per reply and never in the middle of work;
  never one that is already on or already in use (ask `butler_help` when
  unsure); before saying HOW, call `butler_help` and give its steps, never
  guessed ones (the first scenario run, section 6, is why). If the
  owner says to stop suggesting something, save that with `memory_write`.
  SOUL.md wins where it says otherwise (it is shown first).
- **BH-5. Live state is read at call time, never stored in the prompt.**
  Whether WhatsApp is linked, which notification channels exist, how many
  recipes and automations there are and their names, whether ambient
  listening and quiet hours are on, which brain mode and tools the agent has.
  Secrets are never part of it (`Connection.public()`'s rule; a channel is its
  name and kind, never a token).
- **BH-6. Facts only, from the code.** Every detail line describes what HEAD
  does; a feature still a proposal (`worker-mode.md`,
  `parallel-same-project.md`) is not in the table. The guide states the limits
  the owner would hit (endpoint mode cannot show files; WhatsApp sending is a
  setting).

---

## 3. Criteria (proposed)

- **AC-641** The index block is in every persona render where the memory
  tools are offered, after the reminder rules, and absent where they are not.
- **AC-642** The index block is at most 1,500 characters and names every
  feature in the table.
- **AC-643** The index holds no live state: two renders with different
  settings are byte-identical (no respawn, warm prefix cache).
- **AC-644** `butler_help` with no topic answers every feature with its state.
- **AC-645** `butler_help("<topic>")` answers that feature's detail, state and
  its settings groups' blurbs; an unknown topic answers the list of topics.
- **AC-646** Every settings group is covered by a feature or listed in
  `EXCLUDED_GROUPS` with a reason.
- **AC-647** Every tool offered to any brain (memory, library, WhatsApp,
  artifacts, butler_help) is covered by a feature.
- **AC-648** The endpoint brain offers `butler_help` when built with a guide
  and routes the call to it; a brain built without one offers nothing new.
- **AC-649** The pi extension declares `butler_help` exactly as the daemon
  does (parity test) and it joins read_only's `--tools`.
- **AC-650** `POST /api/guide/agent` answers this machine only (`is_local`),
  and is in `test_local_gate.py`'s table.
- **AC-651** No answer contains a secret: a channel token, a webhook token or
  a connection key planted in the settings never appears.
- **AC-652** Guests are offered no tool (unchanged, re-asserted).
- **AC-653** Every phrase the guide tells the owner to SAY for a
  daemon-answered command is one the daemon matches (checked against the
  matchers, not a copy).
- **AC-654** The policy text contains the five BH-4 rules and names no
  language (language.md D2).
- **AC-655** State is read per call: flipping a setting changes the next
  answer with no restart.
- **AC-656** A scenario check against a real model (manual, `test/manual/`):
  for each of a fixed list of owner requests, the reply suggests the right
  feature, suggests none, or invents one; recorded before and after.

---

## 4. Build plan

| step | builds | check |
|---|---|---|
| G1 | `daemon/guide_features.py` (the table, drafted from the specs by a subagent, reviewed against the code) | AC-646, AC-647, AC-653 |
| G2 | `daemon/guide.py`: index, policy, `help_text`, `TOOLS`; persona block | AC-641..AC-645, AC-654 |
| G3 | live state in `butlerd` (`guide_state`), route, endpoint `NEEDS_GUIDE`, pi tool, `--tools` | AC-648..AC-652, AC-655 |
| G4 | scenario check before/after against the owner's configured model | AC-656 |
| G5 | DOX, commit, restart the desk and the laptop | both answer the route |

---

## 5. What does NOT change

- SOUL.md and USER.md stay the owner's; nothing is written into them.
- The router is shown nothing new (4.4): it stays a classifier.
- No feature behaviour changes; this only describes.

---

## 6. Scenario check (AC-656), 2026-10-08 [MEASURED]

`test/manual/eval_butler_help.py`, the desk's own setup (harness only, pi on
`hub/acct/opencode-go/glm-5.3-flash`, the owner's SOUL.md and USER.md),
two scratch daemons: BEFORE at `b4b2c30`, AFTER with this feature. One run
each (n=1 per scenario: model variance is real, see `dave`). Scenarios share
one memory store, equally on both sides.

| scenario | before | after (first) | after (policy fixed) |
|---|---|---|---|
| photo on phone | USB or email workaround | attach in the composer | attach in the composer |
| Dave's emails | invented 15-min poll | mail-triggered automation | 15-min poll again |
| leaving the desk | "not possible" (wrong) | INVENTED app sign-in | Settings, Devices, scan code (right) |
| phrased it badly | no rewind | no rewind | no rewind |
| what can you do | generic, outside skills | accurate Butler overview | -- |
| quiet at night | memory note | speaker state correct | speaker state correct |
| weekly summary, WhatsApp, files, lights, capital | fine | fine | -- |

The first AFTER run is why BH-4 says "call butler_help and give its steps;
never guess steps": the model knew the phone feature existed and invented how
to reach it. Rewind is still never suggested; a stronger index line is the
next thing to try if the owner wants it. "Remind me ..." is answered by the
daemon without a model, so that scenario measures nothing here.

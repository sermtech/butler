# Project shortcuts and configurable hotkeys -- spec and build order

**Status: PROPOSAL, 2026-09-09.** Criteria AC-133..AC-151 live here and move
into `plan.md` only as the owner accepts them. Until then `plan.md` is the
contract and this file is an argument.

**Section 9 is a SECOND ASK, made the same day after the owner used what
sections 0-8 built**: a row's agent buttons should OPEN a terminal on click,
without needing a chord first, because chords are for the few rows they reach
for constantly. **BUILT 2026-09-09**, criteria AC-146..AC-151, with the owner
ruling option 1 on its D8 and asking for row editing in the same build.

Owner's ask: a hideable, persistent pane on the MAIN page listing project
paths they can add, remove and edit, each row carrying three buttons -- claude
code, pi, opencode -- and each launchable by a keyboard chord they choose.
Plus a chord for plain dictation, replacing today's hardcoded numpad `*`.

---

## 0. What this collapses

Three mechanisms become one. `terminal_cwd` (one directory, one agent, edited
in the settings page), the "starred row" idea, and the hardcoded
`Ctrl + numpad *` all become rows in ONE binding table. Nothing in this
feature adds a second way to say where a terminal opens.

**Naming, and it must survive into the code.** `claude` and `opencode` are NOT
Butler harnesses. `ports.HARNESSES` holds `pi` and the fake; `Harness` is the
port the voice brain dispatches through, and adding a sixth adapter needs its
argument written into `plan.md` 4.10. What this feature runs is a **terminal
command table** -- a closed set of three program names, resolved by the daemon
and never interpolated from a request -- and it is entirely separate from that
port. A reader who conflates them will look for `TERMINAL_AGENTS` in
`ports.py` and, worse, will eventually try to make the voice brain dispatch
through it.

## 1. What already exists (READ FROM CODE, 2026-09-09)

Seven findings decide the design. Each is a file and a line.

**`daemon/connections.py` is the precedent for the whole store half, and it is
the right one because a shortcut is the same KIND of thing as a connection:**
an owner-authored record with a fixed shape the settings schema cannot model.
A `Setting` of `kind="list"` is a flat list of STRINGS written whole
(`settings.py:888-896`) -- it lowercases or strips each entry, refuses
anything that is not a string, and has no notion of a per-entry field, an id,
or a partial modification. A shortcut row is `{id, path, label, chords}` and
needs add, remove, MODIFY and per-field validation. So it is a store: a JSON
file at `STATE / "shortcuts.json"`, its path handed in by `build_hub` beside
the other stores (`butlerd.py:771-774`), `load()` in the constructor, `save()`
after every mutation, and a frozen dataclass whose `__post_init__` validates a
HAND-EDITED file (`connections.py:66-78`).

**`POST /api/terminal` ignores its body entirely today**
(`butlerd.py:436-489`). `terminal_cwd(hub.settings)` is the only source of the
directory and a `cwd` in the request is not refused, it is simply not read.
That is the property this feature reverses; see D4.

**`TERMINAL_ARGV` is already a `{cwd}` template** (`butlerd.py:392`):
`("wt.exe", "-d", "{cwd}", "cmd", "/k", "pi")`, formatted per part in
`spawn_terminal`. Adding a second slot is one word.

**`reject_shell_metacharacters` is load-bearing rather than defence in depth**,
because the directory goes onto `wt.exe`'s own command line, which wt parses
and which then carries a nested `cmd /k <agent>` (`daemon/AGENTS.md`, AC-120).
It is the SAME helper `harness_pi._argv` uses and stays that way.

**The daemon already checks `Path(cwd).is_dir()` itself** (`butlerd.py:470`),
because `wt -d missing` opens a window and complains inside it rather than
failing -- so without the check the route answers 200 and the room hears the
success cue for a terminal that never opened (AC-7b).

> **RETIRED 2026-09-11 (the owner's backlog item 4).** This entry and the
> reference to it further down are kept as the ARGUMENT rather than as a
> description of the code: the `projects` setting and its `LOCAL_TARGETS` entry
> are both gone, because the pane's ROWS became the one project list and a
> datalist off them could only offer paths already on screen above it. D4 below
> predicted the drift that forced it, and what follows describes the mechanism
> as it was.

**`LOCAL_TARGETS["projects"]` is how a daemon-side list fills a control
without any path being written into the schema** (`butlerd.py:84`), through
`choices_from`. It is the mechanism the key-name picker fallback copies, and
the one entry deliberately not wrapped in `_nonempty`, because an empty
projects list is a FACT rather than a failure to ask.

**The diagnostics fold is the page precedent, and it is cheap.**
`button.diagbtn[aria-expanded]` with a `.caret`, toggling a `hidden` sibling
(`index.html:933-937`, `app.js:1946-1950`), inside a `div.diag` that is a
direct child of `main` and therefore needs `width: 100%` (`index.html:345`).
`openPanel` is hard-wired to `#panel`/`#scrim`/`#settingsBtn`
(`app.js:1898-1919`) and is NOT reusable, which is the good news: a card in
`main` needs no scrim, no z-index above 1, no focus trap and no Escape
interaction at all.

## 2. What this is NOT

**It is not a second settings surface.** The pane lives in `main`, under the
transcript and above the diagnostics fold. It is not a modal, not a drawer,
not a second URL. `web/AGENTS.md` records why a second URL is impossible here
at all: this page IS the satellite, so navigating away tears down the
microphone, the wake detector, the socket and the playback worklet.

**It is not a scripting surface.** `desk/AGENTS.md`: "Actions are a CLOSED
SET -- a function plus a registry entry, never a config file that can name a
command." The binding table can pick between two written behaviours
(`dictate`, `terminal`) and, within `terminal`, between three program names
the daemon holds. It can never name a command line. That rule is unchanged by
this feature and is what keeps an unauthenticated tailnet route from being a
remote shell.

**It is not push-to-talk (AC-26).** `docs/desk-hotkeys.md` 3 already draws
that line and it still holds: dictation starts a TRANSCRIPTION Butler never
speaks.

## 3. Decisions

Each decision keeps its rejected options, because the argument is what a later
session needs and the conclusion alone reads as arbitrary.

**D1. The pane's open/closed state lives in the STORE, not in a setting.**
[DECIDED]

`GET /api/shortcuts` answers `{open, dictate, rows, agents, keys}` and the
page POSTs `{"open": bool}` when the fold is toggled.

- *(chosen) In the store.* One resource, one fetch, and `web/` names no
  setting at all. AC-41 is enforced by REVIEW rather than by any grep over
  `web/` -- `test_http_surface.py` only greps for browser storage -- so the
  cheapest way to keep it true is by construction: there is no setting for
  this page to name.
- *(rejected) A `look`-group setting beside `theme`.* It would render as a
  checkbox in the settings surface, which is a control for a fold the owner
  can see and click directly -- two ways to say one thing, and the settings
  one is the worse one. It would also put the pane's view state into the
  schema, which every other device then renders as a preference.
- *(rejected) Not persisted at all.* The ask says PERSISTENT. A fold that
  reopens closed on every page load is the thing the word rules out.

**D2. A chord slot per harness button on a row, not a leader key.**
[DECIDED by the owner]

A leader key (`Ctrl+numpad *` then `1`, `2`, `3`) is fewer bindings and one
more keystroke, and it needs a modal timeout state in the hook -- which is the
one place in this repository nothing may hold state, because Windows removes a
hook whose thread stops pumping. Per-slot bindings keep `classify` a table
lookup.

**D3. Chord capture by pressing the keys, with pickers as the fallback.**
[DECIDED by the owner]

The page sends a browser `KeyboardEvent`'s W3C `code` plus four modifier
booleans; the DAEMON maps it to a key name and builds the canonical chord
string. **There is deliberately no key table in JavaScript.** One table, in
Python (`desk/actions.py`), read by the daemon for the `code` half and by the
desk agent for the VK half -- so a name cannot mean one key in the page and
another on the hook.

The picker fallback is filled from a daemon-served list, exactly as
`LOCAL_TARGETS["projects"]` fills a control today. It exists because a capture
cannot express a key the browser will not deliver to the page (a browser
shortcut, a key the window manager eats) and because a keyboard with no F13
still has to be able to pick one.

**D4. `POST /api/terminal` may take a path from the caller.** [DECIDED by the
owner]

This REVERSES the property set on 2026-09-09 by the ruling in
`docs/desk-hotkeys.md` D3, and the cost is one line: an unauthenticated
tailnet request can once again choose where a full-capability coding agent
starts. Not re-litigated. What survives is every other gate --
`reject_shell_metacharacters` (AC-120), the `is_dir` check, and the closed
`{pi, claude, opencode}` table that resolves `agent` so a request can never
name a command. The `projects` allow-list is NOT reinstated as a gate on
`cwd`, because the rows the owner types in the pane are exactly the list they
would be checked against, and a second list to keep in step is the drift
`connections.py` was written to avoid.

**D5. The dictation chord is a ROW in the store, not a setting.** [DECIDED]

- *(chosen) A row.* Then every chord in the system is validated at ONE door
  (`POST /api/shortcuts`), by ONE parser, and refused with a named 400 the
  pane can show. It is also what "one binding table" means.
- *(rejected) A `dictate_chord` text setting.* `settings.validate` would have
  to import the key table to refuse a bare letter, which points `daemon/` at
  `desk/` for a validation the store already does -- and a `kind="text"`
  setting that is not validated means one write to `/api/settings`, an
  unauthenticated surface, could bind bare `Return` and deny input on the
  owner's desk until they found Task Manager.

**D6. The folder dialog is a short-lived CHILD PROCESS of the daemon.**
[DECIDED]

The owner's ruling of 2026-09-09 ("I don't want to see an ugly tkinter
interface... options/paths etc should be on our webui settings page") deleted
a picker from the desk agent. What made that picker bad is recorded in
`desk/AGENTS.md` and it is mechanical, not aesthetic: it BLOCKED the one
thread that polls settings and serves dictation, so every dictation was
refused until somebody dismissed it, and it took the foreground away from the
window AC-114 promises to leave alone.

`POST /api/browse` has neither problem. The owner clicked "Browse..." in the
browser, so taking the foreground is what they asked for; and it is a separate
process the daemon waits on off the event loop, so nothing that serves a
hotkey or a turn is blocked. It is still the closest thing in this feature to
the overruled decision, and it is the first thing to delete if the owner
dislikes it -- the path field beside it is a plain text input and works with
no dialog at all.

**D7. A row's chords are stored as canonical STRINGS, not as VKs.** [DECIDED]

`"ctrl+numpad_multiply"`. A VK in the file is unreadable to the owner editing
it by hand and meaningless on a machine with a different key table; a name is
both. The VK exists for the length of one `parse_binding` call on the hook's
side of the wire.

## 4. The shape

```
                     state/shortcuts.json          <- the store
                            |
   POST /api/shortcuts  ----+----  GET /api/shortcuts        (the page)
   DELETE /api/shortcuts/{id}          |
                            |          |
                     GET /api/settings + "bindings"          (the desk agent)
                            |
                   desk/actions.Table (parse, refuse)
                            |
              hotkeys.Hook.set_table  ->  classify(vk, wparam, mods)
                            |
                   deskd.Agent._ACTIONS[action]
                        /          \
                 _begin (hold)   _open_terminal (once)
                                       |
                            POST /api/terminal {cwd, agent}
```

### 4.1 The store

`daemon/shortcuts.py`, following `connections.py`:

```json
{
  "open": false,
  "migrated": true,
  "dictate": "numpad_multiply",
  "rows": {
    "s-1757...": {"path": "E:\\PROJECTS\\butler", "label": "butler",
                  "chords": {"pi": "ctrl+numpad_multiply", "claude": ""}}
  }
}
```

`Shortcut` is frozen and validates its own SHAPE in `__post_init__`, because
this file is one the owner can edit and a bad row otherwise blows up later
inside `public()`, taking the whole list down with it -- which is the recorded
defect `connections.py:66-78` exists for.

**One deliberate improvement on connections.** A bad row is LOGGED AND
SKIPPED at load rather than raising out of the constructor. Connections raises
and therefore stops the daemon booting; `settings.py`'s own rule is that a bad
preference is never worth refusing to boot over, because a daemon that will
not start leaves no microphone at all (D-1). Losing one hand-edited row and
saying so is strictly better than losing Butler.

Chord SEMANTICS -- is this a real key, is it bare-safe -- are validated at the
route and again at bind time, not here, because that is where the answer can
be a sentence the owner reads.

### 4.2 The wire the desk agent reads

`GET /api/settings` grows ONE top-level key beside `groups` and `settings`:

```json
"bindings": [
  {"chord": "numpad_multiply", "action": "dictate"},
  {"chord": "ctrl+numpad_multiply", "action": "terminal",
   "cwd": "E:\\PROJECTS\\butler", "agent": "pi", "label": "butler"}
]
```

Composed by the ROUTE (`{**hub.settings.describe(), "bindings": ...}`), not by
`Settings.describe()`, so the settings schema stays the settings schema and
the store stays the store.

**A missing `bindings` key is a REFUSAL, not an instruction to clear.** This
is the same class as the defect that made the whole desk feature silently do
nothing: `Daemon.poll` read a `values` dict `/api/settings` has never served,
saw an empty settings map forever, and `hotkeys_enabled` was permanently
False. It survived the whole suite because every double overrides `get`
(`docs/desk-hotkeys.md` step 3). A key that is absent means "this daemon does
not serve bindings", which is a daemon to keep the current table for, never
one to unbind every key on the owner's machine for.

### 4.3 `desk/actions.py`

Pure data plus one parser. It imports nothing Win32, which is what lets the
daemon import it for the `code` half of the key table on any platform.

- `KEYS: {name: Key(vk, code, bare_safe)}`. **The table IS the allow-list.**
  An unnamed key cannot be bound, and NO MODIFIER VK HAS AN ENTRY -- so "bind
  Ctrl itself" is impossible by construction rather than by a check that can
  be forgotten.
- `MODS`, a bitmask, canonical order `ctrl alt shift win`.
- `Binding(chord, vk, mods, action, hold, cwd, agent, label)`, frozen.
- `parse_binding(action, chord, ...) -> Binding`, and `Table(rows)`.
- `ACTIONS`, the closed registry: `dictate` (hold), `terminal` (once).

**Correction to a rule stated earlier in this session: "numpad" is NOT
bare-safe.** `VK_NUMPAD0..9` only arrive with NumLock ON -- with it off the
same physical keys send Insert/End/Home/PageUp/arrows, so a bare
numpad-digit binding silently stops working the first time somebody touches
NumLock. And numpad `+` and `-` are typed constantly by anyone who uses the
numpad at all. The honest bare-safe set is:

**`numpad_multiply`, `numpad_divide`, `F13`-`F24`, `Pause`, `Scroll Lock`.**

That is also exactly why `docs/desk-hotkeys.md` D2 (`:132-140`) chose numpad
`*` originally: suppression means the key stops producing its character
system-wide, so the only keys safe to bind bare are the ones almost nothing
needs. Everything else in `KEYS` requires at least one modifier.

`numpad_enter` is deliberately ABSENT from `KEYS`. Its W3C code is
`NumpadEnter` but its VK is `VK_RETURN`, the same as the main Enter, and
`classify` matches on VK alone -- so binding it would suppress the owner's
Enter key and the table would be lying about which key it named.

### 4.4 The hook

**`Table` is immutable and published by a single attribute store.**
`set_table` assigns; `_callback` reads it ONCE into a local. **No lock.** The
coordinator thread would hold that lock while parsing and formatting a new
table, so a lock here converts a settings-poll hiccup into a REMOVED HOOK --
which is the exact failure this module exists to prevent (Windows silently
unhooks a callback whose thread stops pumping past `LowLevelHooksTimeout`).
An atomic reference swap costs nothing and cannot stall.

**The hot path is two levels.** `vk not in table.keys` -- one frozenset test,
and it runs for EVERY KEYSTROKE ON THE MACHINE -- then, only for a candidate,
five `GetAsyncKeyState` calls and one dict lookup. Nothing else may happen on
that thread, and specifically **no `print`**: the agent inherits the daemon's
stdout (`desk.py:80-83`), and a Windows console write blocks indefinitely
while the operator has text selected in quick-edit mode, so a log line there
silently uninstalls the hook.

**The DOWN resolves, the UP replays.** `Hook._held: dict[vk, Binding]`, owned
by the hook thread.

```
DOWN, vk already in _held        -> replay _held[vk], suppress   (auto-repeat)
DOWN, table.match(vk, mods)      -> record in _held, suppress
DOWN, bound key, wrong modifiers -> pass through, no entry
UP,   vk in _held                -> replay it, pop, suppress
UP,   vk not in _held            -> pass through
```

Two properties fall out of that and both are what the design is for.
Suppression is SYMMETRIC: a DOWN that was passed through leaves no entry, so
its UP passes through too. And the whole class of mislabelled-edge defects
this session already paid for twice -- `("terminal", down)` followed by
`("dictate", up)` because Ctrl was released first, and the mirror image that
lost a whole dictation -- stops being produced at all, because the edge is no
longer re-resolved from the modifiers held at the instant of the UP.

**`classify(table, held, vk, wparam, mods)` is a module function and is the
seam that makes the hook testable in pytest for the first time.** It touches
no Win32; it is pure of Windows, not of state -- it updates `held`, which
belongs to the caller. One separate test drives the real `_callback` against a
fake `_u32` to prove the suppression return value actually reaches Windows,
because a `classify` that decided correctly and a `_callback` that returned
`CallNextHookEx` anyway would look identical from every other angle (D-18).

### 4.5 The agent

- `Agent._ACTIONS`, name -> bound method. `_begin` for `dictate`, and
  `_open_terminal(binding)` for `terminal`, which now carries the row's
  directory and agent.
- `_held: dict[vk, action]` replaces the single `_key_held` boolean: the
  auto-repeat latch is per KEY, because two bindings can be held at once.
- **`_capturing_vk` is new and necessary.** With two hold bindings: press A,
  press B (refused, already RECORDING), release B. Today's single boolean
  would end A's capture and post a clip the owner is still speaking into.
- **Lost key-up recovery, and it is a TIMEOUT.** Windows does not remove a hook
  on ONE timeout, it SKIPS it for that event -- so a single stall can lose
  exactly one edge, and a lost UP leaves `_held` stuck and the hotkey dead until
  hotkeys are toggled off and on. `Agent.check_stuck` runs on the coordinator's
  existing 10 ms loop and finishes a capture that has run past
  `dictate_max_seconds` plus a slack.
  **The obvious mechanism -- ask whether the key is still down -- is impossible
  here, and this paragraph said to use it.** It shipped, and it broke dictation
  completely; see AC-145 for the measurement and the cost. A suppressed key's
  physical state is unreadable BY CONSTRUCTION, because suppression is Windows
  not recording the keystroke.
- **`apply_settings` splits in two.** `_apply_bindings` before
  `_apply_enabled`, and a binding change must not run `_start`/`_stop`. If it
  did, editing a chord would drop a held key's UP, close and reopen the audio
  stream -- the fragile operation `docs/desk-audio.md` is entirely about --
  and log `hotkeys OFF` / `hotkeys ON` on every keystroke of an edit.
- **An invalid or empty table refuses to change anything.** Not clear, not
  default. Clearing turns hotkeys off silently; defaulting binds keys nobody
  asked for. The refusal names the chord and the reason, once per distinct
  message rather than once per poll (AC-7b).
- **The install log line is GENERATED FROM THE TABLE.** `deskd.py:339`
  hardcodes "numpad * dictates", which becomes a lie the moment the table is
  configurable -- and it is the owner's only record of what is now suppressed
  system-wide on their machine.

### 4.6 Two contracts this deliberately changes

Both get their docstrings REWRITTEN rather than quietly relaxed (D-12).

**`Daemon.open_terminal()` takes a directory and an agent again.**
`test_desk_dictation.py:442-468` asserts the empty signature, in a test whose
docstring says a parameter coming back would mean the desk agent had started
deciding. It has -- the owner asked for it (D4) -- so the test is rewritten to
assert the NEW property: the agent sends exactly what its binding carries and
never anything it chose itself, and it still imports no picker.

**`paste.wait_for_modifiers` injects Ctrl-up for modifiers the owner is
physically holding** (`paste.py:190`). That is correct today because the only
shipped binding with a modifier is a terminal chord, which pastes nothing. It
becomes reachable the moment a HOLD binding has a modifier, and then the paste
path would send a key-up for a key the owner is still pressing. The capturing
binding's mods are passed down so the wait EXCLUDES them.

### 4.7 AltGr is not solved

On European layouts AltGr arrives as left-Ctrl plus right-Alt, and a
`WH_KEYBOARD_LL` hook cannot reliably tell it from a real Ctrl+Alt: the
synthesised left-Ctrl is not marked injected. So a `ctrl+alt+X` binding will
fire when the owner types AltGr+X, and a `ctrl+X` binding may too. Recorded
rather than fixed. What bounds it: the shipped table's only modified binding
is on Ctrl alone, and the owner's layout is US.

### 4.8 The page

A top-level collapsible block in `main`, immediately before
`<div class="diag">`. `div.shortcuts` with `width: 100%` like `.diag`, a
`button.diagbtn[aria-expanded]` with a `.caret`, and a `hidden`
`section.card` body whose row list scrolls internally.

**It is NOT a surface, and that is what makes it cheap.** No scrim, no
z-index above 1, no focus trap, and **no Escape interaction** -- which retires
the hazard raised before this was explored, that a third Escape case would
have to be carved into `app.js`'s three (AC-112, the safety argument
`web/AGENTS.md` says must not be loosened).

**Chord capture must swallow the keystroke.** The single global keydown
listener (`app.js:1920-1944`) consumes `Escape` unconditionally and `/` while
the panel is open, so a focused capture field needs `stopPropagation` AND
`preventDefault` -- the first so Escape cancels the capture instead of
closing the settings surface, the second so the key does not also type.

Wiring goes in `app.js` beside `restartDaemon` and the new-conversation
button; `settings.js` owns only the settings-surface carve-outs. `app.js` has
no fetch helper and calls `fetch` directly, and `restartDaemon`'s
transient-label idiom (`app.js:1974-2002`) is what a failed POST reports
with. The `.diagbtn` / `.caret` CSS is REUSED rather than cloned, which makes
it the page's general fold button; `web/AGENTS.md` says so.

## 5. Acceptance criteria

Each says which way it fails and whether that is recoverable -- the AC-92
lesson from `docs/satellite-ux.md` 11.1.

- **AC-133 (the pane, and it stays where it was put).** A collapsible block on
  the main page lists the configured project rows. Its open/closed state
  survives a page reload and a second device sees the same state, because it
  lives in the daemon's store and nothing in `web/` names a setting. Fails as:
  a fold that reopens closed every time, or a pane that is a modal. Recoverable.
- **AC-134 (rows are the owner's to edit).** A path can be added, given a
  label, modified and removed, through the pane alone. An upsert MODIFIES the
  row with that id rather than adding a second one. Fails as: a list that only
  grows, whose only exit is hand-editing a JSON file the owner was never told
  about -- `connections.py`'s recorded trap. Recoverable.
  **The MODIFY half was true only of chords until the owner asked for the rest**
  in section 9's build: clicking a row's name now opens its path and label in
  place. The gap was written down in 9.7 before it was closed rather than left
  to read as done. One rule the editor depends on and the store cannot enforce:
  an upsert replaces a row WHOLE, so a save that sent only the path and label
  would silently unbind every chord on it -- and the owner would find out the
  next time they pressed one, with nothing on screen connecting the two.
- **AC-135 (a chord is captured by pressing it).** Clicking a chord slot and
  pressing the keys records that chord, and the keystroke reaches nothing else
  on the page -- no Escape-closes-the-surface, no `/`-focuses-search, no
  character typed. Fails as: the settings surface closing while the owner
  binds Escape. Recoverable.
- **AC-136 (a chord opens the named agent where the row points).** Pressing a
  row's chord opens ONE terminal, in that row's directory, running that row's
  agent, and it outlives the daemon (AC-119 unchanged). Fails as: no window, a
  window in the wrong directory, or the wrong agent. Recoverable.
- **AC-137 (the dictation chord is the owner's).** The dictate binding is a
  row in the same table and can be rebound; the hardcoded `VK_MULTIPLY`
  default in `hotkeys.Hook` is gone. Fails as: a rebind that saves, reports
  success and changes nothing -- AC-7b exactly. Recoverable.
- **AC-138 (a bare key that is not bare-safe is refused BY NAME).** Binding a
  bare letter, digit, Return, Escape or numpad digit is refused, at the route
  with a 400 the pane shows and again in `Table.__init__`, and the refusal
  names the key and the bare-safe set. Validation is at BIND TIME and there is
  no other door: no `add_binding`, only `set_table`. Fails as: a global
  suppression of the owner's `e` key. **NOT recoverable from the page** -- with
  the letter suppressed they cannot type the pane's own inputs -- which is why
  the refusal is in the daemon and in the desk agent and not only in the UI.
- **AC-139 (the UP replays the DOWN).** A press whose modifiers changed before
  the release still ends the action the press started, and a DOWN that was
  passed through has its UP passed through too. Fails as: a dictation whose
  clip is never delivered while the microphone fills to the ceiling, or a
  suppressed key-up with no matching key-down that leaves an application
  believing a key is still held. Recoverable.
- **AC-140 (a bad table changes nothing).** An unparseable chord, a duplicate
  chord, an empty list, or a settings body with no `bindings` key at all
  leaves the running table exactly as it was, and says why once. Fails as:
  every hotkey silently unbound, or keys nobody asked for bound. Recoverable
  but invisible, which is why the log line is part of the criterion.
- **AC-141 (`POST /api/terminal` takes `{cwd, agent}` and the agent is a
  closed table).** The route honours a caller-supplied directory (D4) and
  resolves `agent` through `{pi, claude, opencode}`, never interpolating a
  command from the request. A `cwd` holding `" % & | < > ^` is still refused
  by name and nothing is spawned; a `cwd` that is not a directory is still a
  400; an unknown `agent` is a 400 naming the three. Fails as: a folder name
  running a second command, or a request naming its own executable. **NOT
  recoverable.**
- **AC-142 (the log says what is suppressed).** The `hotkeys ON` line is
  generated from the live table and names every chord and what it does. Fails
  as: a line saying "numpad * dictates" on a machine where it does not, which
  is the owner's only record of what this process has taken over.
  Recoverable.
- **AC-143 (Browse never blocks the agent).** `POST /api/browse` opens a
  folder chooser in a short-lived child process, answers the chosen path, and
  204 on cancel. While it is open, dictation still works and the settings poll
  still runs. Fails as: the overruled picker's failure -- every dictation
  refused until somebody dismisses a dialog. Recoverable, and the reason the
  dialog is not in the desk agent.
- **AC-144 (`terminal_cwd` is migrated once, then retired).** On first boot
  with a set `terminal_cwd` and no shortcuts, one row is created from it bound
  to `Ctrl + numpad *` running pi, and the setting is gone from the schema.
  Deleting every row does not bring it back. Fails as: a row the owner deleted
  reappearing on every restart, or an owner's configured directory silently
  lost. The second half is NOT recoverable -- nothing else remembers that
  path.
- **AC-145 (a lost key-up does not kill the hotkey).** While a hold binding is
  capturing, a key-up the hook never received is recovered once the capture has
  run past `dictate_max_seconds` plus a slack, so the hotkey works again. Fails
  as: a hotkey dead until hotkeys are toggled, and a stage no later press can
  enter. Recoverable by toggling, which is exactly the recovery this removes the
  need for.
  **REWRITTEN 2026-09-09, hours after it shipped, because its first mechanism
  was impossible and broke dictation completely.** The criterion said "recovered
  from the physical key state within one coordinator tick", and 4.5 argued that
  physical state cannot drift the way a queue of edges can. It cannot -- and it
  also cannot be READ. `GetAsyncKeyState` reports a SUPPRESSED key as UP,
  because returning non-zero from a low-level hook is precisely Windows not
  updating the async key-state table for that keystroke [MEASURED, 5 of 5 each
  way with suppression as the only variable,
  `test/manual/probe_asyncstate.py`]. Every key this agent waits on is a key it
  suppresses, so NO POLL CAN EVER WORK HERE. The losing argument is kept because
  it is the obvious one and the next reader will have it too.
  What it cost, and it is the shape worth remembering rather than the fact: the
  tick ended every capture inside 200 ms, so every clip was the lookback ring
  alone, and the daemon answered `nothing was said in that clip` -- a sentence
  about SPEECH for a failure that was nothing of the kind. The owner reported it
  as "holding * to asr is broken", which is the same wrong-organ trap
  `docs/desk-audio.md` cost an hour to.
  A TIMEOUT needs no key state and cannot fire early: the microphone has already
  stopped accumulating at the ceiling, so finishing a moment later delivers
  exactly what a released key would have. What it gives up is immediacy -- a
  lost key-up is recovered after the ceiling rather than at once -- and that is
  the honest bound on what this can know.

## 6. Build order

Ten steps, each ending at something observable. **STEPS 1-10 BUILT
2026-09-09**, in this order.

1. **This spec**, for approval before any code.
2. **`desk/actions.py`** -- pure data plus one parser. Parametrized refusals.
3. **`hotkeys.Table` usage, `Hook(table)`, `set_table`, and the pure
   `classify` seam.** One `_callback` test with a fake `_u32` proves
   suppression actually reaches Windows.
4. **`Agent._ACTIONS`, `_held: dict`, `_capturing_vk`, the stuck-key check.**
5. **The store and its routes.**
6. **`POST /api/terminal` takes `{cwd, agent}`**; extend
   `test_terminal_route.py`.
7. **`apply_settings` split, the refusal rules, the table-generated log line,
   the migration.**
8. **`desk/browse.py` and `POST /api/browse`** (manual probe -- it opens a
   window).
9. **The page.**
10. **DOX**, and the criteria into `plan.md` on acceptance.

### 6.1 What the build found that this spec did not predict

**`desk/` had to become a REGULAR package, and the reason is nasty.**
`daemon/desk.py` (the supervisor) and `desk/` (the process) are both reachable
as the name `desk`. Without `desk/__init__.py`, `desk/` is a NAMESPACE package,
which Python treats as a portion to be continued rather than as a match -- so
the search carries on down `sys.path` and `daemon/desk.py`, a regular module,
WINS. `butlerd.py` puts the repository root at `sys.path[0]` and `import desk`
still resolved to `daemon/desk.py`, whose own `from . import childproc` failed
with "attempted relative import with no known parent package" and the daemon
would not boot. It only became reachable because `daemon/shortcuts.py` imports
`desk.actions` -- the one key table -- so the daemon now reaches that package at
IMPORT time and not only at spawn time. `desk/__init__.py` exists for exactly
this and says so.

**`classify`'s hot-path test needed a second half.** The first version was
`if vk not in table.keys: return None, False`, which drops the key-up of a key
that was REBOUND AWAY while the owner was holding it -- the entry is never
popped, the capture runs on with the microphone open, and the agent has a stage
it can never leave (D-1). It is now `and vk not in held`. Found by the test that
names the case (`test_a_table_swapped_MID_HOLD_still_lets_the_release_finish`)
rather than by review, which is the argument for having written the seam.

**Six honest witnesses for the exact-match rule, not one.** `mutate_bindings.py`
names all six rather than pretending it has one (test/AGENTS.md), because a
subset match has six different visible consequences and every one of them is a
real assertion.

## 7. Tests, and the one instrument gap

**`test/test_shortcuts.py`** (new), in `test_connections.py`'s style: a
docstring stating the RULE, assertion messages naming the failure, and
confirmation by observable consequence rather than by status code -- a row
gone from the list, not a 200.

**`test/test_desk_hotkeys.py`** (new) over `classify`: exact match, exact
non-match, unbound pass-through, UP replays the DOWN's action, UP under
changed modifiers still replays, auto-repeat replays without re-resolving,
table swap mid-hold preserves `_held`.

**`test_desk_dictation.py`** extended, and
`test_the_agent_reads_settings_off_the_REAL_route` extended to assert the
bindings WIRE SHAPE. That is the cheapest high-value assertion in this
feature, because every other double in that file overrides `get` -- which is
precisely how the `values`-dict defect survived the whole suite.

**`test/manual/probe_bindings.py`** (new). A suppressed key is INVISIBLE to
pytest: the only observable difference between "suppressed" and "the hook
never fired" is whether the character reached an application, and no test in
this repository can look at an application. So the only way to prove
suppression SELECTIVITY is to bind `Ctrl + numpad *`, inject a BARE
`numpad *` as a finger, and read a literal `*` back out of a named scratch
file -- whose window title is checked FIRST, because
`test/AGENTS.md` records what it cost when a probe assumed a process it
launched was a window it owned.

**D-13 is required on three rules**: the exact-modifier match, the bare-key
restriction, and the UP-replays-the-DOWN rule. For the last one, delete the
`_held.pop` and prove exactly one test reddens.

**One instrument gap, stated rather than discovered later.** Nothing in pytest
reaches `web/`. The page is covered only by `probe_page.py` (the settings
surface) and `probe_ux.py` (accessibility and copy), and neither knows about
this pane. **Neither is extended by this feature and the page half is verified
BY HAND**, against the owner's own daemon: open the fold, add a row, capture a
chord, press it, watch the right agent open in the right directory, then
reload the page and confirm the fold is still open. The reason for choosing
hand verification over extending `probe_page.py` is that the pane's whole
point is a KEYSTROKE reaching a hook in another process, which a browser
automation harness cannot inject; the parts it could check (the fold, the
rows) are the parts least likely to break silently.

### 7.1 What was actually run (2026-09-09)

- `pytest test\ -q -m "not slow and not needs_gpu"`: **724 passed, 55
  deselected**, exit 0. (Before this feature: 642 after the existing files were
  updated; 82 of the 724 are new.)
- `test_shortcuts.py` 31 passed, `test_desk_hotkeys.py` 51 passed,
  `test_desk_dictation.py` 43 passed, `test_terminal_route.py` 29 passed.
- `mutate_bindings.py`: **3/3** rules caught -- aimed exit 1, rest of the file
  exit 0, restore exit 0, for the exact-modifier match, the bare-key
  restriction and the UP-pops-the-entry rule.
- `node --check` on all three JavaScript files, exit 0.
  `LC_ALL=C grep -c "[^ -~]"` is 0 on every changed file except `web/app.js`,
  which had FIVE non-ASCII characters before this feature (a middot in the mic
  label, curly quotes around the wake phrase) and has the same five now.
- **A LIVE scratch daemon**, checked over HTTP: `terminal_cwd` migrated into a
  row bound to `ctrl+numpad_multiply`/pi on first boot, the setting absent from
  the schema, the `bindings` key on `/api/settings` carrying the dictation chord
  plus one terminal binding per chord, a bare `e` refused with the sentence
  naming the bare-safe set, an agent of `bash` refused naming the three, a path
  holding `&` refused, and a missing directory refused.
- **THE PAGE, DRIVEN IN A REAL BROWSER** (patchright, headless Chromium,
  scratch daemon): zero page errors, the fold opens and its state survives a
  RELOAD, a row is added and removed, a chord is captured by pressing
  `Ctrl+F9` and appears in the store, a bare `e` shows the daemon's refusal in
  the pane, and Escape while a slot is armed cancels the capture. That was a
  throwaway script and is NOT kept in the tree -- see the instrument gap below,
  which is unchanged: nothing in `test/` reaches `web/`.
- **NOT RUN: `probe_bindings.py`.** It opens Notepad and injects keystrokes on
  the operator's own desktop, so it is the owner's to run. Everything it proves
  -- that suppression reaches Windows and is SELECTIVE -- is therefore
  unverified by measurement; `test_desk_hotkeys.py` proves only that
  `_callback` returns 1.

## 8. What this spec could have wrong

Six, written down now so a later session can check them rather than
rediscover them.

**A HOLD binding with a modifier still ends with Windows and the application
disagreeing about that modifier.** `wait_for_modifiers` now EXCLUDES the
capturing chord's own modifiers, which stops the paste path injecting a Ctrl-up
for a key the owner is pressing -- but the `ctrl_v` chord that follows sends its
own Ctrl-down and Ctrl-up, and that Ctrl-up lands while the owner may still be
holding the physical key. This is the same class as the AltGr problem below and
neither is solved. What bounds it: the shipped dictation chord is BARE, so a
hold binding with a modifier is something the owner has to choose. If they do
and paste starts misbehaving, this paragraph is the place to start.

**A chord the browser never delivers.** Chrome and the OS eat some
combinations before the page sees them -- `Ctrl+W`, `Ctrl+N`, `F11`, anything
the window manager claims. The capture UI will simply never fire for those,
which reads as a broken capture field rather than as a key that cannot be
captured here. The picker fallback is the answer and is why D3 has one, but
nothing tells the owner WHICH keys are in that category, because nothing can
know.

**Five `GetAsyncKeyState` calls per candidate keystroke is unmeasured.** The
current hook makes one, and the frozenset test in front of them means they run
only for a bound key -- so on a machine with two bindings this is a handful of
calls a day. It has not been profiled and the claim it is free is reasoning
rather than measurement.

**The stuck-key recovery has never fired, and the last time it was wrong it was
wrong in the path that runs CONSTANTLY.** AC-145 exists because Windows'
documented behaviour on a hook timeout is to SKIP the event, not to unhook,
which makes a single lost edge possible. Nobody has observed one. Its first
mechanism was a poll and it broke every dictation on the machine within minutes
of shipping -- which is the opposite of "buggy in a path that never runs", and
is the reason this bullet is now a warning rather than a worry: what fires
constantly is the CHECK, not the recovery, so a cheap check with an expensive
mistake is the shape to watch for. The timeout that replaced it reads no state
and compares two floats.

**Three agents may not all be installed.** `pi` is at
`C:\Users\user\AppData\Roaming\npm\pi.cmd` [READ 2026-09-08]. Whether `claude`
and `opencode` resolve on this machine's PATH has NOT been checked, and
`wt -d <cwd> cmd /k claude` for an absent `claude` opens a window that says
"not recognized" -- a visible failure inside the terminal rather than a
route-level one, which is the same shape as the missing-directory case that
needed its own `is_dir` check. No pre-flight check is proposed: `where claude`
per press is a subprocess on the event loop, and a cached answer goes stale.

**The pane is one more thing on the main page, and the main page is settled.**
`web/AGENTS.md`: "The page that ships stays. Its layout, tokens, type, state
hues and mic treatments are settled." A collapsed fold above the diagnostics
fold costs 46 px and matches an element that is already there, which is the
narrowest reading of that rule this feature could take. If the owner reads it
as a violation, the pane moves into the settings surface as a hand-built block
beside connections -- and loses the "one keystroke from the main page" half of
the ask.

---

## 9. Clicking an agent opens it (second ask, 2026-09-09)

Owner's ask, after living with what sections 0-8 built: "when I add a
line/project I want to be able to also just click on claude, or pi, or opencode
and open the cmd there directly, without NEEDING to setup a keyboard shortcut.
I'll have shortcuts only for specific ones." And, separately: "when CLAUDE CODE
is triggered, it should be with the option --dangerously-skip-permissions every
time."

### 9.1 What exists (READ FROM CODE, 2026-09-09)

**The daemon needs no change at all for the first ask.** `POST /api/terminal`
already takes `{cwd, agent}` and answers `{cwd, agent}` (`butlerd.py`,
AC-136/AC-141). A click is a second CALLER of a route that ships, not a new
route -- which is the good shape: D-18's lesson is about two IMPLEMENTATIONS of
one contract, and one route with two callers is the cure rather than the
disease.

**The page has exactly one control per agent and it does the RARE thing.**
`chordSlot` in `app.js` renders a `button.chordbtn` whose `onclick` arms a chord
capture; nothing on the page launches anything. So the frequent action is
unreachable and the infrequent one owns the only hit target. That is the whole
of the first ask, and it is a `web/` problem.

**`TERMINAL_AGENTS` is already a name-to-command MAP, not a set**
(`daemon/shortcuts.py`). `spawn_terminal` resolves the key and formats the
command into `TERMINAL_ARGV`'s `{agent}` slot, so the second ask is a DATA
change. That map being a map rather than a set was not foresight about flags; it
was the closed-table rule, and it happens to pay here.

**But `{agent}` is ONE argv element, and a flag makes it two.** `subprocess`
builds a command line from the list and QUOTES any element containing a space,
so `claude --dangerously-skip-permissions` as one element would reach `cmd` as a
single quoted token and `cmd` would look for a program with that literal name.
The slot has to SPLICE a tuple, not interpolate a string.

**Two callers already override `TERMINAL_ARGV` wholesale** and neither includes
an `{agent}` element: `test_desk.py`'s AC-119 host (a `title` marker plus
`ping`) and `probe_terminal.py` (the same). Splicing only where an element is
literally `{agent}` leaves both untouched, where making the argv a prefix and
appending the agent would break both.

**`public()` serves `sorted(TERMINAL_AGENTS)`**, which renders claude, opencode,
pi -- alphabetical, and not the order the owner named them.

### 9.2 D8. Where does the LAUNCH live, and where does the BIND go?

**[THE OWNER'S DECISION. Options, not a design.]**

Every option below keeps one rule, because it is the only one with a cost worth
avoiding: **a launch and a bind must never be the same click.** Both outcomes
are recoverable -- close a window, press Escape -- but conflating them makes the
page's most consequential control ambiguous, and one of the two starts a
full-capability coding agent in a repository.

- **(1) Launch buttons, chord slots behind a per-pane disclosure. CHOSEN BY
  THE OWNER, 2026-09-09.** The row shows claude / pi / opencode, each a launch.
  One checkbox at the top of the pane -- "show chords" -- adds a chord slot under
  each button when the owner is actually binding. Recommended because the stated
  usage is that most rows have no chords, and this is the only option where a
  chordless row costs no vertical space and no reading. Its state lives in the
  store beside `open`, at `open`'s cost: one boolean, same file, same route, and
  `web/` still names no setting. The cost is two clicks to reach a binding, and a
  fold inside a fold.
  The slot's unbound label is **"bind"** rather than "unbound", decided while
  building: with a launch button above it the slot stopped being a status and
  became a call to action -- the row already works without a chord.
- **(2) Launch buttons with the chord slot always beneath.** No mode, no
  disclosure, everything visible. The cost is that "unbound" is printed three
  times on every row for a feature the owner says they will use on a few, and
  every row is twice as tall -- which is real at 390 px.
- **(3) Launch buttons, and a per-row "chords" toggle that swaps THAT row's
  buttons into capture slots.** Cleanest row when idle and no global state. The
  cost is a MODE on the launch control: the buttons mean two different things
  depending on a toggle the owner may not remember pressing, which is the one
  thing the shared rule above exists to avoid.
- **(4) Click launches, Alt-click or right-click binds.** One hit target,
  nothing added, no mode. Offered for completeness and not recommended: it is
  undiscoverable, right-click is nothing on a tablet, and long-press fights text
  selection. This page is used across a room and on an iPad.

### 9.3 D9. claude carries the skip-permissions flag, always

[DECIDED by the owner, 2026-09-09.] It goes in `TERMINAL_AGENTS`, which is the
DAEMON's own closed table, so the flag is as unreachable from a request as the
program name is -- a caller still names a KEY and can still put nothing on a
command line (AC-141 unchanged).

Stated once and not argued: every claude started from this pane can write and
run commands with no confirmation. That is the posture the owner already set
when they retired AC-29 on 2026-08-18 -- a coding agent runs at full capability,
with `bash`, in every permission mode -- so this is that ruling reaching a second
launcher rather than a new decision. What bounds it is the same thing that bounds
the rest of this feature: the row's directory, which the owner typed.

`pi` and `opencode` get no flags. Nobody asked, and a flag nobody asked for is
the kind of thing that is discovered three months later in a log.

### 9.4 D10. The buttons render in the order the DAEMON names them

[DECIDED.] `public()` drops `sorted()` and serves `TERMINAL_AGENTS`'s own
insertion order -- claude, pi, opencode, which is the order the owner named them
in the original ask. Dict order carrying a decision has a precedent here and a
rule beside it: `settings.GROUPS` is ordered and `daemon/AGENTS.md` records that
"dict ORDER is what the page renders". The page still names no agent, so this
costs nothing in `web/`.

### 9.5 What any option must also do

Four rules that are not the layout and are easy to leave out.

**One click, one terminal.** The button is disabled for the length of the
request and for a short cooldown after it. This is the page-side twin of the
defect that put "138 repeats of one held chord against 4 real dictations" in
`butler.log`: a control that starts a coding agent must not be double-clickable
into two of them, and an impatient second click on a slow spawn is the normal
case rather than the perverse one.

**A launch that failed SAYS so, in the daemon's own words.** The route answers
400 for a directory that is not there -- and that is now the COMMON failure,
because a row's directory is a path the owner typed weeks ago and may have
renamed since. A click that does nothing and says nothing is indistinguishable
from a dead button (AC-7b). The pane already has `#shortNote` for exactly this,
and the chord refusals already use it.

**Success is the WINDOW, not the 200.** The button shows a transient
"opening..." and returns to its label; it must not latch a success state,
because the daemon cannot see whether the terminal became visible to anybody
(see 9.8).

**An accessible name that says what and where.** "Open claude in butler", not a
button reading "claude". `web/AGENTS.md` records that 42 of 61 controls on this
page once computed no accessible name (AC-100), and a one-word button is exactly
that failure arriving again.

### 9.6 Acceptance criteria

- **AC-146 (a click opens the agent where the row points).** Clicking an agent
  on a row opens one terminal, in that row's directory, running that agent, with
  no chord configured and nothing else set up. Fails as: nothing happens, or the
  wrong directory or agent. Recoverable.
- **AC-147 (one click, one terminal).** A double-click, or a second click while
  the first request is in flight, starts exactly ONE coding agent. Fails as: two
  or twenty full-capability agents in one repository. Recoverable, and expensive
  in a way the owner notices immediately.
- **AC-148 (a launch that did not happen says why).** Every refusal the route
  can give reaches the pane as the daemon's own sentence. Fails as: a button
  that does nothing and says nothing -- AC-7b, and the likeliest real failure
  here is a renamed directory. Recoverable.
- **AC-149 (launch and bind are never the same click).** No single activation of
  one control can produce both, and which one a control does is visible before
  it is pressed. Fails as: clicking an agent arming a capture, or an attempt to
  rebind opening a terminal. Both recoverable; the second wastes a spawn.
- **AC-150 (claude runs with the skip-permissions flag).** Every claude started
  from this table carries it, and the flag lives in the daemon's closed table so
  no request can add, remove or forge one. Fails as: a claude that stops to ask,
  which the owner has to answer in a window they may not be looking at -- or, in
  the other direction, a request able to put flags on a command line, which is
  AC-141 breached. The second is NOT recoverable.
- **AC-151 (the buttons read in the order the daemon serves them).** The page
  renders `agents` in the order given and sorts nothing. Fails as: a button
  order that changes when an agent is renamed. Recoverable and cosmetic.

### 9.7 What was actually run (2026-09-09)

- `pytest test\ -q -m "not slow and not needs_gpu"`: **734 passed, 55
  deselected**, exit 0.
- **THE PAGE, DRIVEN IN A REAL BROWSER** against a scratch daemon whose
  `spawn_terminal` was replaced by a recorder, so nothing started three coding
  agents in this repository. Zero page errors, and every criterion in 9.6
  observed rather than reasoned:
  - buttons read `claude, pi, opencode` -- the owner's order, not alphabetical
    (AC-151);
  - `aria-label` on the first reads "Open claude in butler" (AC-150's rule from
    9.5);
  - with the disclosure OFF, zero chord slots are rendered;
  - one click on `pi` recorded exactly `('E:\PROJECTS\butler', 'pi')`, the
    button read `opening...` and was disabled, and the label was restored
    (AC-146);
  - **six rapid clicks on `claude` recorded exactly ONE spawn** (AC-147);
  - the disclosure ON showed `claude/bind pi/bind opencode/bind`, a capture of
    `Ctrl+F9` landed on `pi` alone, and clicking `opencode` afterwards still
    LAUNCHED rather than bound (AC-149);
  - editing the row's label to "renamed" left `{'pi': 'ctrl+f9'}` intact, which
    is the assertion the whole-row upsert makes necessary;
  - pointing a row at a directory that does not exist put the daemon's own
    sentence in the pane and recorded NO spawn (AC-148);
  - a reload came back with both the fold and the disclosure as they were left.
- `node --check web/app.js` exit 0; `LC_ALL=C grep -c "[^ -~]"` unchanged at 0
  for every changed file except `web/app.js`, whose five non-ASCII characters
  predate this feature.
- **NOT run: a real claude.** `test_claude_carries_the_skip_permissions_flag_as_
  SEPARATE_ARGV` asserts the argv this daemon builds; whether `claude` on this
  machine accepts that flag under `cmd /k` is unverified, and an absent or
  unhappy claude fails INSIDE the terminal where the route cannot see it (9.7).

### 9.8 What this could have wrong

**The daemon cannot tell whether the terminal became VISIBLE.**
`desk/hotkeys.py` has `input_desktop_reachable()` and `Agent._start` refuses to
install a hook without it, because a hook on a session that does not own the
input desktop succeeds and then receives nothing forever. The DAEMON has no
equivalent, and `wt.exe` spawned onto a locked screen or a disconnected RDP
session opens a window nobody can see while the route answers 200. That was
already true of the chord path; a button makes it easy to hit repeatedly. Not
fixed here, and it is the reason 9.5 says the success state must not latch.

**A PHONE can now open a terminal on the desk machine.** Not a new route --
`/api/terminal` has been reachable unauthenticated from the tailnet since D4,
and curl could always do this -- but a tap is not curl. A tablet left face-up
with the pane open is one touch from a coding agent starting in a repository.
Stated rather than gated, because there is no device identity anywhere in this
daemon to gate it with, so the only alternative is not building the feature.

**Three agents may not all be installed** -- unchanged from section 8, and a
click makes it more visible: `wt -d <cwd> cmd /k claude` for an absent claude
opens a window that says "not recognized", which is a failure inside the
terminal rather than one the route can report.

**Row EDITING is still missing and this ask does not add it.** The pane can add
and remove a row and bind its chords; there is no way to change a path or a
label once typed. AC-134 claims a row "can be added, given a label, modified and
removed", and the modify half is true only of chords. That is an honest gap in a
criterion already written down, not a new one.

---

## 10. The keyboard's MAIL button (third ask, 2026-09-09)

Owner's ask: their keyboard has a physical mail button, pressing it opens a
Chrome tab, and they want it to open Butler instead.

### 10.1 Butler CANNOT bind it, and that is measured

[MEASURED 2026-09-09, `test/manual/spike_launch_keys.py`.] Across three runs and
100 s of watching, a `WH_KEYBOARD_LL` hook saw **55 ordinary key-downs** -- so it
was alive and receiving -- and **ZERO** keys in `0xA6`-`0xB7`. The mail button on
this keyboard never becomes a keyboard event. It arrives as a HID consumer usage
that Windows turns straight into `WM_APPCOMMAND`.

**No hook work will change that, and there is no in-process way round it.**
`WM_APPCOMMAND` is delivered to the FOREGROUND window and the shell, and a
process can only receive one for its own windows -- the desk agent has none.
`WH_GETMESSAGE` would need a DLL injected into every process on the machine,
which is not something to do at all, let alone in Python.

So `launch_mail` is NOT in `desk/actions.KEYS` and must not be added: a key in
that table is a key the owner can bind, and this one would silently never fire.

### 10.2 What the spike got wrong first, which is the transferable part

Its first two runs reported a confident **NO** and that was not an answer. With
no control, "saw no launch key" is ambiguous between *this keyboard has none* and
*this hook is receiving nothing at all* -- and the second is a live possibility
here, because a hook on a session that does not own the input desktop SUCCEEDS
and then receives nothing forever (`desk/AGENTS.md`), and the measuring process
was in session 2 while the console session was 1.

**The instrument was also mis-validated.** It was checked by injecting vk `0x88`
and confirming the recorder caught it -- which proves the RECORDER works and says
nothing about whether a real finger arrives, because `SendInput` from that
process reaches its own desktop either way. That is "one wrong answer ruled out
is not an instrument" (`test/AGENTS.md`) walked into head first.

What fixes it is a COUNT of every other key-down -- a number, never the keys,
because that callback sees everything typed on the machine. Any typing at all
proves the hook is alive, so zero launch keys beside a non-zero count is a fact
about the KEY. Phase 1 now also REFUSES to measure when the input desktop is
unreachable, the way `Agent._start` refuses, rather than blaming the keyboard for
it (AC-7b).

### 10.3 The route that is left: `AppKey`, not Butler

READ from this machine, and it is why this route was chosen rather than guessed
at: `HKCU\...\CurrentVersion\Explorer\AppKey` did not exist at all; the
machine-wide `HKLM\...\Explorer\AppKey` had subkeys `7, 15, 16, 17, 18` --
browser-home, mail, media-select, app1, app2, which are the APPCOMMAND values --
and **all five were EMPTY**; and `mailto:`'s UserChoice ProgId was `ChromeHTML`.
Nothing was overriding the key, so it fell through to the association, which is
Chrome. The reported symptom was the key working correctly.

`scripts/bind-mail-key.py` writes `ShellExecute` under
`HKCU\...\AppKey\15`. A script rather than a hand-edit, for `make-cert.py`'s
reason: machine setup has to survive a Windows reset, and an undo nobody wrote
down is not an undo. HKCU, so no administrator rights and nothing machine-wide.
`--show` reads the current state and is the first thing to run if the button ever
stops working; `--remove` deletes the one key and the shortcut.

**`ShellExecute` takes a FILE, not a command line.** A browser plus arguments
cannot go in the value -- one string is treated as a filename -- so `--install`
writes a `.lnk` carrying `--app=<url>` and points the key at that. `--url-only`
puts the bare URL in the value instead, which opens an ordinary tab in the
default browser and has the fewest moving parts.

App mode because the page is a SATELLITE rather than a document: a chromeless
window has no tab strip and no other tabs to be buried under, and Butler already
ships a web app manifest (AC-106). `localhost` deliberately, not the LAN address:
it is in the certificate's SANs [MEASURED 2026-09-09 -- `localhost`,
`DESKTOP-EXAMPLE`, `127.0.0.1` and four more, valid to 2028] and it is a secure
context, so the page's microphone works.

**IT WORKS. Both halves confirmed on the owner's machine, 2026-09-09.** Running
the shortcut opened an app window titled `Butler` with no browser suffix, which
is the `--app` signature; and the owner pressed the mail button and Butler
opened. So Windows DOES consult the per-user `AppKey` entry for this keyboard
even though the key never becomes a keystroke -- the redirect happens on the
APPCOMMAND path, which is exactly why it works where a hook cannot.

**THE ONE THING THAT BIT, AND IT WILL BITE ANYONE POINTING THIS AT A NEW
ADDRESS.** The first press opened the window, and the page could not reach the
microphone and reported a websocket error. `https://localhost:8140` is a
DIFFERENT ORIGIN from whatever address the owner normally uses, and Chrome keeps
its self-signed-certificate exception AND its microphone grant PER ORIGIN -- so
neither carried over, and an `--app` window has no address bar to fix them from.
It cleared once the certificate was accepted and the microphone granted for the
new origin. Worth knowing before choosing the URL: an origin already in use costs
nothing, and a brand-new one costs one round of clicking through. The certificate
covers `localhost`, the hostname and every local IP [MEASURED: SANs are
`localhost`, `DESKTOP-EXAMPLE`, `DESKTOP-EXAMPLE.local` and five IPs, valid to
2028], so any of them is a legal choice -- the only question is which one the
browser already trusts.

A second caution, paid for while testing: **do not sample window titles four
seconds after launching.** The first check here reported `about:blank` and it was
taken as "the URL never reached Chrome"; the page had simply not loaded yet, and
two pre-existing `about:blank` windows were attributed to the launch. Look for
the title `Butler`.

### 10.4 What this does NOT give, and the cheap thing it enables

The `open_page` action was NOT built. A keystroke that opens Butler's page is
still a reasonable feature and the daemon half is nearly free -- a route that
opens THIS daemon's own address, taking no URL from the caller, which keeps the
property `/api/terminal` gave up in D4. It just cannot be the mail button, and
would have to go on a key the hook can see: `pause`, `scroll_lock`,
`numpad_divide`, or F13-F24. Not built because nobody has asked for it on
another key.

Four other launch keys are registered and unclaimed on this machine -- `7`, `16`,
`17`, `18` -- so the same script with a different subkey redirects
media-select, app1 or app2 without spending the mail button.

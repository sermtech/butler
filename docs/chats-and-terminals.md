# Chats and terminals: Claude Code and opencode beside pi (BUILT)

Written 2026-10-29 against HEAD `f8ea916`; built the same day (owner: "no
need for spikes ... get it done fast"). AC-760..AC-773 are promoted to
`plan.md`; AC-772 is moot while OD-1 bypasses permissions. AC-774..AC-799
stay reserved. Rolled out on the desk, laptop and Mac mini 2026-10-29. Spec-local decision ids are
CT-n. Provenance tags as `docs/proactive.md`.

## 0. The owner's decisions [OWNER]

2026-10-29: "I want butler to also have options of chats AND terminals like
e projects an earlier prototype on Claude code and opencode." Then, one question at a
time:

1. Terminals: **in-page** (a real terminal inside Butler's page, phone
   included), not the external window.
2. Chats: **yes, both** -- Butler conversations can run on Claude Code and on
   opencode as well as pi, chosen per chat.
3. Voice and terminals: **dictate in, no read-back.** The dictation key and
   the microphone button type into the focused terminal; Butler never reads
   terminal output aloud.
4. Lifetime: **terminals survive Butler restarts** (a separate host process,
   an earlier prototype's design).
5. Who: **owner only.** This PC and the owner's Tailscale login; a paired
   device cannot open or type into a terminal.

## 1. What exists [READ 2026-10-29]

- A project row's click opens `claude --dangerously-skip-permissions`, `pi`
  or `opencode` in an EXTERNAL window (`wt.exe` on Windows, Terminal.app on
  macOS; `butlerd.TERMINAL_ARGV`, `shortcuts.TERMINAL_AGENTS`, owner ruling
  D9). Butler does not see that window again.
- Butler chats run on ONE harness kind: pi (`daemon/adapters/harness_pi.py`,
  `pi --mode rpc`), behind the `Harness` port (`daemon/ports.py`: `start`,
  `prompt`, `abort`, `dispose`, exactly one terminal event per prompt,
  AC-48; questions to a human through `Ask`, AC-234). The `harness` setting
  names the default.
- On the desk: Claude Code 2.1.312 (`--print --input-format stream-json
  --output-format stream-json --include-partial-messages`, `--permission-mode`,
  `--permission-prompt-tool`, `--resume <id>`), opencode 1.19.3 (`serve`,
  `acp`, `run`), Node 24 [MEASURED `--help`, 2026-10-29].
- An earlier prototype (`E:\PROJECTS\prototype\scripts\dashboard\terminal-host\`) has
  a tested terminal stack: a DETACHED Node host owning node-pty shells on a
  named pipe, HMAC token handshake, per-session `seq` so attach replays with
  no gap, headless xterm state, checkpoints, idle exit, stale-code detection;
  xterm.js 6 in the page; dictation into terminals.
- `web/` has no build step and fetches nothing at run time (`web/AGENTS.md`):
  xterm.js must be VENDORED like the fonts.
- The gate (`docs/remote-access.md`) and its `OWNER_ONLY` list exist.

## 2. The shape

### 2.1 Terminals

- **CT-1 One terminal host, ported from an earlier prototype.** `daemon/terminal_host/`
  (Node, plain `node`, no build): an earlier prototype's host's protocol, token
  handshake, `seq` replay, idle exit and stale-code report, with Butler's
  paths (`paths.records / "terminals"`) and pipe name. On macOS a Unix
  socket in the records folder replaces the named pipe. The DAEMON is its
  client (`daemon/terminals.py`): spawn-if-absent, reconnect, re-adopt.
- **CT-2 What a terminal runs: the closed table.** A terminal is opened for
  a PROJECT ROW with an agent from `shortcuts.TERMINAL_AGENTS` (claude with
  `--dangerously-skip-permissions`, pi, opencode) or a plain shell
  (`cmd`/`pwsh` on Windows, the login shell on macOS). Never a caller's
  command line. The working directory is the row's path (so
  `docs/project-roots.md` applies).
- **CT-3 Owner only.** Every terminal route and the terminal socket join
  `access.OWNER_ONLY`; a DEVICE gets the gate's 403 before anything spawns.
- **CT-4 The page.** A project shows its chats AND its terminals; a terminal
  opens as a view beside the chat (xterm.js, vendored under `web/vendor/`,
  fetched once by a script and gitignored, like `web/fonts/`). On a phone
  it is full width with a key row (Esc, Tab, Ctrl, arrows) the soft
  keyboard lacks.
- **CT-5 Dictation goes to the focused terminal.** When a terminal has
  focus, a dictation result is written to that PTY as typed text, with NO
  Enter; otherwise dictation behaves as today. Nothing a terminal prints is
  spoken (owner decision 3).
- **CT-6 Survives restarts.** The host outlives the daemon; a restarted
  daemon re-adopts every live terminal and the page reattaches with replay.
  AC-715's automatic restart is NOT blocked by open terminals (they
  survive it).

### 2.2 Chats on Claude Code and opencode

- **CT-7 Two new harness adapters behind the existing port.** BUILT
  2026-10-29, no spike (owner: "an earlier prototype works, take code from it").
  `adapters/harness_claude.py`: ported from an earlier prototype's claude backend,
  `claude --print --input-format stream-json --output-format stream-json
  --verbose --include-partial-messages --permission-mode bypassPermissions`
  held for the conversation, `--resume` for its session.
  `adapters/harness_opencode.py`: `opencode run --format json`, one process
  per prompt, `--session` for its session (OD-2). Each module docstring holds
  its event mapping. Both keep the engine's session id inside Butler's
  minted session file (`adapters/session_id_file.py`). Both pass the
  conformance suite as replay rows over recorded output; the live rows run
  with `BUTLER_LIVE_AGENTS=1` [MEASURED 2026-10-29: a live prompt, a resume
  and an interrupt on claude 2.1.312 haiku; two prompts in one session on
  opencode 1.19.3]. Neither gets Butler's memory, task or library tools
  (pi's extension has no equivalent for them yet), and opencode gets no
  persona (no system-prompt flag on `opencode run`).
- **CT-8 The agent is chosen per chat.** BUILT (daemon side): a new chat
  takes the `harness` setting's value at its first spawn, records it in its
  `harness` field and keeps it across restarts and setting changes
  (`butlerd.chat_harness`, `test/test_chat_agent.py`). A chat recorded
  before this has its old value (pi). `harness_model` reaches only a chat
  running the setting's agent. NOT built: the page chip (AC-688).
- **CT-9 Permissions in chats.** Bypassed, per OD-1: neither new adapter
  raises an `Ask`. `harness_tools: read_only` is the engine's own read-only
  mode (Claude Code `--tools Read,Grep,Glob --strict-mcp-config`; opencode
  `--agent plan` without `--auto`, where an asked permission is
  auto-rejected [MEASURED 2026-10-29]). AC-772 is therefore not built.

## 3. Criteria (AC-760..AC-799 reserved; draft, to be confirmed)

- **AC-760** A terminal for a project row runs only a `TERMINAL_AGENTS` entry
  or the plain shell, in that row's directory; any other command is refused
  and nothing spawns.
- **AC-761** Terminal routes and the terminal socket are owner-only: a paired
  device gets the gate's 403 and no PTY is created (spawn count).
- **AC-762** Output reaches the page in order with no gap or duplicate across
  a reattach (`seq` replay), proven by a byte-exact echo test.
- **AC-763** A daemon restart leaves every terminal's shell PID alive and the
  page reattaches to it with its screen restored.
- **AC-764** The host's token never crosses the pipe; a process without it
  is refused (an earlier prototype's v2 handshake, ported with its test).
- **AC-765** Dictation with a terminal focused writes the text to that PTY
  without a newline; with no terminal focused it behaves as before.
- **AC-766** Nothing a terminal prints reaches speech (TTS call count).
- **AC-767** The page loads xterm.js from `web/vendor/` only (grep gate: no
  CDN URL), and the phone view has the key row.
- **AC-770** A chat on Claude Code and a chat on opencode each pass the
  harness conformance suite, including one terminal event per prompt.
- **AC-771** A chat keeps the agent it was born with across restarts and
  `harness` setting changes.
- **AC-772** A tool-permission question from either agent reaches `Ask` and
  the answer reaches the agent (per OD-1).
- **AC-773** `daemon/guide_features.py` names terminals and the per-chat
  agent (BH-3).

## 4. Build plan

Each step red-green with mutation checks (`test/AGENTS.md`); scratch daemons
and a scratch terminal host only (its own records folder, so its pipe name
differs from the live one).

1. **Spike (no product code), half a day.** Record real Claude Code
   stream-json and opencode `serve`/`acp` event streams for: text, a tool
   call, a permission request, an error, an abort. Decide opencode transport
   (OD-2). Measure node-pty install on Windows and macOS with Node 24.
   [opus: protocol judgment]
2. **Terminal host port** (CT-1, AC-762..AC-764): copy an earlier prototype's host,
   protocol and their tests, re-pathed; the daemon client. [sonnet: bounded
   port of tested code, tests come with it; opus reviews]
3. **Routes, gate and spawn table** (CT-2, CT-3, AC-760, AC-761). [opus:
   security]
4. **Page** (CT-4, CT-5, AC-765..AC-767): vendor script, terminal view, phone
   key row, dictation target. [sonnet against the tests; opus review]
5. **Claude Code harness** (CT-7..CT-9, AC-770..AC-772) from the spike's
   recordings, then **opencode**. [opus: event mapping is where silent
   wrongness lives]
6. **Per-chat agent and the chip** (CT-8, AC-771, AC-688). [sonnet]
7. **Docs, guide row, attack review** (terminal routes from a device, the
   pipe from another local user, a dictation injection). [opus]

Rollout: desk first; the laptop and Mac follow `docs/remote-access.md`
section 6's care (the host is a new long-lived process on each).

## 5. Open decisions, for later [OWNER]

- **OD-1 Permission posture in chats. DECIDED 2026-10-29 (owner): like
  an earlier prototype** -- Claude Code chats run `bypassPermissions`, opencode chats
  `--auto`. Asking through `Ask` (AC-772) stays possible later
  (`--permission-prompt-tool`), not built.
- **OD-2 opencode transport. DECIDED by the build, 2026-10-29: `opencode run
  --format json`**, one process per prompt with the prompt on stdin and
  `--session` to continue. Neither `serve` nor `acp` was needed: `run`
  already streams JSON events, reports tools with their outcome and
  continues a session [MEASURED]. Cost: a process start per prompt (a short
  tool run took 6.8 s end to end on a free cloud model [MEASURED]).
- **OD-3 Does a project click still open the EXTERNAL window?** Today's D9
  click opens `wt.exe`. Keep it, switch it to an in-page terminal, or offer
  both (click = in-page, a menu item = pop out)?
- **OD-4 Limits.** How many terminals at once, scrollback size, and whether
  an idle terminal is ever closed for you.
- **OD-5 Spoken summary of a terminal** ("what is the claude terminal
  doing?") -- ruled out for now (decision 3); revisit after use.
- **OD-6 Paired devices and terminals** -- owner-only now (decision 5);
  revisit only with a per-device grant.
